"""Graph Memory Engine leveraging networkx and SQLAlchemy for Deep GraphRAG.

This module provides the GraphMemoryEngine, which records succession events,
decisions, constraints, and roles into a persistent global Knowledge Graph.
"""

import json
import logging
import math
import random
import re
import uuid
import networkx as nx
from typing import TYPE_CHECKING, Dict, List, Optional
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from self_governance.db import GraphNode, GraphEdge, get_db
from self_governance.learning import embed_text

if TYPE_CHECKING:
    from self_governance.learning import AgentDB

logger = logging.getLogger("self_governance.graph")

# A-MEM-style dynamic linking (book §17.11): link a new constraint to prior
# constraints that share enough vocabulary to plausibly be about the same
# concern, so query_context can surface a related insight even when it was
# filed under a different feature name. Lexical (Jaccard over tokens), not
# embedding similarity -- no vector store in this stack, and a threshold
# tuned on real token overlap is at least as auditable as a cosine cutoff.
_RELATION_STOPWORDS = {
    "a", "an", "the", "in", "on", "for", "to", "of", "and", "or", "is",
    "must", "use", "with", "be", "at", "by", "this", "that",
}
_RELATION_JACCARD_THRESHOLD = 0.3
# ponytail: bounds the linking scan to the most recent N constraints per
# tenant instead of the whole history, so add_session_node stays O(1)-ish
# per write regardless of how long a tenant has been running. Raise this or
# swap for an ANN/vector index if a real deployment needs recall beyond the
# last 200 constraints.
_RELATION_SCAN_LIMIT = 200

# Procedural memory (Phase D3, book §17.10.3): named repair strategies with
# success/failure counters, matched to a new failure by the same lexical
# Jaccard approach as constraint linking above -- reuses _tokenize rather
# than inventing a second similarity mechanism.
_PROCEDURE_MATCH_THRESHOLD = 0.3

# Semantic (HNSW cosine-similarity) match threshold for recommend_procedure's
# supplementary AgentDB search (HyphaeDB-inspired, July 2026 topic-page
# batch) -- a separate, stricter threshold from _PROCEDURE_MATCH_THRESHOLD
# since cosine similarity over embed_text's char-composition vectors is a
# different metric than Jaccard token overlap, not directly comparable.
_SEMANTIC_MATCH_THRESHOLD = 0.85
_PROCEDURE_SCAN_LIMIT = 200

# Sufficient-context gating (Google Research, "Sufficient Context: A New
# Lens on Retrieval Augmented Generation Systems", ICLR 2025; research.google
# survey, July 2026): passing the bare match threshold only means a
# candidate is *present*, not that it's a confident enough match to act on
# -- their finding is that flagging marginal retrieval and abstaining
# (or downgrading confidence) beats always trusting whatever cleared the
# cutoff. A candidate needs to clear the threshold by this extra margin to
# count as context_sufficient=True; candidates between the bare threshold
# and this margin are still returned (ranking is unchanged) but flagged so
# a caller can choose to treat a marginal match more like an UNKNOWN-tagged
# fact than a FACT-tagged one.
_SUFFICIENT_CONTEXT_MARGIN = 0.15

# Procedural memory extension (research synthesis of SwarmAgentic (EMNLP
# 2025), AgentNet (NeurIPS 2025), and a survey on LLM multi-agent systems
# (Vicinagearth 2024): three independent papers converge on the same gap in
# a flat success/failure counter -- no recency weighting, no attribution of
# *which* failure shape a strategy handles, no record of *why* an attempt
# failed beyond pass/fail.
#
# A fixed taxonomy (not free text) so per-category stats stay comparable
# across strategies, adapted from SwarmAgentic's role/step flaw categories
# to what actually fails in ASG's single-agent perspective-rotating attempt
# loop -- reusing benchmark.py's existing failure classes where they
# already exist, rather than inventing a parallel vocabulary.
FLAW_CATEGORIES = frozenset({
    "tests_failed", "no_files_written", "sandbox_error",
    "wrong_persona_order", "missing_requirement", "ambiguous_requirement",
    "unknown",
})

# Evidence-tagging for critique text (idea surveyed from
# 0xNyk/council-of-high-intelligence's evidence-labeled verdict protocol,
# July 2026 batch): a critique that's an unverified ASSUMPTION shouldn't move
# a strategy's score as confidently as one grounded in an observed FACT.
# Confidence weights blend the raw pass/fail outcome toward neutral (0.5)
# before the EMA update -- an ASSUMPTION-tagged pass nudges the score up less
# than a FACT-tagged one. Omitting the tag entirely (the default, and every
# pre-existing caller) gets full confidence: unchanged from before this
# feature existed.
EVIDENCE_TAGS = frozenset({"FACT", "INFERENCE", "ASSUMPTION", "UNKNOWN"})
_EVIDENCE_CONFIDENCE = {"FACT": 1.0, "INFERENCE": 0.85, "ASSUMPTION": 0.6, "UNKNOWN": 0.7}
_UNTAGGED_CONFIDENCE = 1.0
_UNRECOGNIZED_EVIDENCE_TAG = "UNKNOWN"
_UNKNOWN_FLAW_CATEGORY = "unknown"
_MAX_STORED_CRITIQUES = 5
# AgentNet eq. 2's decayed-edge-weight formula, applied to a strategy's
# score instead of a graph edge: recent outcomes matter more than old ones,
# without needing full history.
_EMA_ALPHA = 0.8
# Forgetting-curve decay rate (MemoryBank, Zhong et al. 2024, July 2026
# topic-page batch): applied per "touch" of logical staleness (see
# last_touch_index), not per wall-clock second -- a strategy that hasn't
# been recorded against in N other outcomes' worth of activity is treated
# as N units stale. This is a distinct, additive signal alongside the
# existing EMA score (which already weights recent outcomes more within a
# strategy's own history) rather than a replacement for it: EMA answers "is
# this strategy currently doing well", staleness answers "how long since we
# last checked".
_FORGETTING_DECAY_RATE = 0.05

# ExpeL-style insight extraction (Zhao et al. 2024, July 2026 topic-page
# batch): a recurring flaw category or blamed step is only worth surfacing
# as a cross-strategy insight once it's shown up more than once -- a single
# occurrence is just one strategy's flaw, not a systemic pattern.
_INSIGHT_MIN_STRATEGIES = 2
_INSIGHT_MIN_STEP_FAILURES = 2


def tokenize(text: str, stopwords: "set | None" = None) -> set:
    """Shared lexical tokenizer (ponytail-audit dedup): lowercases and
    splits on word characters, optionally dropping a stopword set.
    Reused by consensus.py's groupthink detection with stopwords=None
    (its exact prior behavior) so both modules share one definition."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    if stopwords:
        return {w for w in words if w not in stopwords}
    return set(words)


def jaccard(a: set, b: set) -> float:
    """Shared Jaccard-similarity helper (ponytail-audit dedup): the same
    |intersection|/|union| expression was inlined separately in this
    module (twice) and in consensus.py's groupthink detection."""
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)


def _tokenize(text: str) -> set:
    return tokenize(text, _RELATION_STOPWORDS)


class GraphMemoryEngine:
    """Engine for building and querying the GraphRAG memory."""

    def __init__(self, tenant_id: str, agent_db: Optional["AgentDB"] = None):
        """Args:
            tenant_id: Tenant scope for all graph operations.
            agent_db: Optional AgentDB (learning.py) instance for semantic
                procedure recall (HyphaeDB-inspired HNSW-as-recall-fabric,
                July 2026 topic-page batch): AgentDB's HNSWIndex already
                existed in this codebase but was never connected to
                procedural memory, which only ever did lexical (Jaccard
                token-overlap) trigger matching. When set,
                record_procedure_outcome also indexes each procedure's
                trigger_pattern into agent_db's "patterns" namespace, and
                recommend_procedure additionally does a nearest-neighbor
                search there -- surfacing procedures whose trigger text
                shares little vocabulary overlap with the query (so Jaccard
                misses them) but is structurally similar (embed_text is a
                char-composition vector, not true semantic embedding -- a
                complementary signal to Jaccard, not a semantic upgrade to
                it). None (default) preserves exact prior behavior:
                lexical-only recall, no AgentDB dependency.
        """
        self.tenant_id = tenant_id
        self.agent_db = agent_db

    def _get_session(self) -> Session:
        # Get a db session using the generator
        return next(get_db())

    def add_session_node(
        self,
        session_id: int,
        roster: List[str],
        features: List[str],
        constraints: List[str],
        dedup_threshold: Optional[float] = None,
    ) -> str:
        """Records a succession session and links it to the roster and injected constraints.

        Args:
            session_id: The succession session ID.
            roster: List of agent roles approved.
            features: List of feature names the session is building.
            constraints: List of injected constraints (God's Eye).
            dedup_threshold: Optional write-time constraint dedup (research.google
                survey, July 2026 topic-page batch, Tier 2): when a new
                constraint's Jaccard similarity against an existing constraint
                meets or exceeds this threshold, no new Constraint node is
                created -- the session links to the existing node instead
                (still gets its own CONSTRAINED_BY edge, so this session's
                relationship to that constraint is recorded either way).
                Distinct from _RELATION_JACCARD_THRESHOLD's RELATES_TO
                linking, which always creates a new node and only links it
                to related-but-distinct priors; this instead treats a
                near-identical constraint as the *same* constraint, so
                query_context and recommend_procedure don't have to reconcile
                N near-duplicate nodes as separate evidence. None (default)
                disables dedup entirely -- exact prior behavior, every
                constraint gets its own node.

        Returns:
            The session node ID.
        """
        db = self._get_session()
        try:
            session_node_id = f"session_{session_id}"
            
            # Create session node
            session_node = GraphNode(
                id=session_node_id,
                tenant_id=self.tenant_id,
                type="Session",
                properties=json.dumps({"session_id": session_id, "timestamp": datetime.now(timezone.utc).isoformat()})
            )
            db.merge(session_node)
            
            # Create Persona nodes and edges
            for role in roster:
                role_id = f"persona_{role.replace(' ', '_')}"
                role_node = GraphNode(id=role_id, tenant_id=self.tenant_id, type="Persona", properties=json.dumps({"role": role}))
                db.merge(role_node)
                
                edge = GraphEdge(tenant_id=self.tenant_id, source_id=session_node_id, target_id=role_id, type="APPROVED_BY")
                db.add(edge)
                
            # Create Feature nodes and edges
            for feature in features:
                feature_id = f"feature_{feature.replace(' ', '_')}"
                feature_node = GraphNode(id=feature_id, tenant_id=self.tenant_id, type="Feature", properties=json.dumps({"name": feature}))
                db.merge(feature_node)
                
                edge = GraphEdge(tenant_id=self.tenant_id, source_id=session_node_id, target_id=feature_id, type="BUILDS")
                db.add(edge)
                
            # Create Constraint nodes and edges, then link each to prior
            # constraints that share enough vocabulary to be about the same
            # concern (A-MEM-style dynamic linking, Phase C2b).
            prior_constraints = (
                db.query(GraphNode)
                .filter(GraphNode.tenant_id == self.tenant_id, GraphNode.type == "Constraint")
                .order_by(GraphNode.created_at.desc())
                .limit(_RELATION_SCAN_LIMIT)
                .all()
            )
            prior_tokens: list = [
                (str(n.id), _tokenize(json.loads(str(n.properties)).get("text", "")))
                for n in prior_constraints
            ]

            for constraint in constraints:
                new_tokens = _tokenize(constraint)

                dedup_target_id: Optional[str] = None
                if dedup_threshold is not None and new_tokens:
                    for prior_id, tokens in prior_tokens:
                        if not tokens:
                            continue
                        if jaccard(tokens, new_tokens) >= dedup_threshold:
                            dedup_target_id = prior_id
                            break

                if dedup_target_id is not None:
                    db.add(GraphEdge(
                        tenant_id=self.tenant_id, source_id=session_node_id,
                        target_id=dedup_target_id, type="CONSTRAINED_BY",
                    ))
                    continue

                constraint_id = f"constraint_{uuid.uuid4().hex[:8]}"
                constraint_node = GraphNode(id=constraint_id, tenant_id=self.tenant_id, type="Constraint", properties=json.dumps({"text": constraint}))
                db.merge(constraint_node)

                edge = GraphEdge(tenant_id=self.tenant_id, source_id=session_node_id, target_id=constraint_id, type="CONSTRAINED_BY")
                db.add(edge)

                for prior_id, tokens in prior_tokens:
                    if not tokens or not new_tokens:
                        continue
                    similarity = jaccard(tokens, new_tokens)
                    if similarity >= _RELATION_JACCARD_THRESHOLD:
                        db.add(GraphEdge(tenant_id=self.tenant_id, source_id=constraint_id, target_id=prior_id, type="RELATES_TO"))
                        db.add(GraphEdge(tenant_id=self.tenant_id, source_id=prior_id, target_id=constraint_id, type="RELATES_TO"))
                prior_tokens.append((constraint_id, new_tokens))

            db.commit()
            return session_node_id
        except Exception as e:
            db.rollback()
            logger.error(f"Failed to add session node to graph memory: {e}")
            raise
        finally:
            db.close()

    def query_context(self, current_features: List[str]) -> str:
        """Queries the graph for context relevant to the current features being built.
        
        Args:
            current_features: Features in the current requirement vector.
            
        Returns:
            A string summarizing past sessions, constraints, and personas related to these features.
        """
        db = self._get_session()
        try:
            G = nx.DiGraph()
            
            # Fetch all nodes and edges (in a real system, we'd limit this or do an ego-graph query)
            nodes = db.query(GraphNode).filter(GraphNode.tenant_id == self.tenant_id).all()
            for n in nodes:
                G.add_node(n.id, type=n.type, **json.loads(str(n.properties) if n.properties else "{}"))
                
            edges = db.query(GraphEdge).filter(GraphEdge.tenant_id == self.tenant_id).all()
            for e in edges:
                G.add_edge(e.source_id, e.target_id, type=e.type, **json.loads(str(e.properties) if e.properties else "{}"))
                
            # Find relevant past constraints and personas
            relevant_context = []
            seen_constraint_ids: set = set()
            for feature in current_features:
                feature_id = f"feature_{feature.replace(' ', '_')}"
                if feature_id in G:
                    # Find sessions that built this feature
                    sessions = [u for u, v, d in G.in_edges(feature_id, data=True) if d.get("type") == "BUILDS"]
                    for sess in sessions:
                        # Find constraints for those sessions
                        constraints = [v for u, v, d in G.out_edges(sess, data=True) if d.get("type") == "CONSTRAINED_BY"]
                        for c in constraints:
                            seen_constraint_ids.add(c)
                            constraint_text = G.nodes[c].get("text", "")
                            relevant_context.append(f"Past constraint applied to {feature}: {constraint_text}")

                            # A-MEM one-hop: surface related constraints even
                            # if they were filed under a different feature.
                            related = [v for u, v, d in G.out_edges(c, data=True) if d.get("type") == "RELATES_TO"]
                            for r in related:
                                if r in seen_constraint_ids:
                                    continue
                                seen_constraint_ids.add(r)
                                related_text = G.nodes[r].get("text", "")
                                relevant_context.append(f"Related past constraint (linked to a {feature} constraint): {related_text}")

            if not relevant_context:
                return "No specific past graph context found for these features."
                
            return "GraphRAG Context:\n- " + "\n- ".join(set(relevant_context))
        finally:
            db.close()

    def record_roster_outcome(
        self, roster: List[str], task_description: str, passed: bool, **kwargs
    ) -> str:
        """Team-composition analogue of record_procedure_outcome (July 2026
        topic-page batch, papers-of-papers research: Dynamic LLM-Agent
        Network's team-optimization idea and GPTSwarm/G-Designer's
        communication-topology idea both reduce, for ASG's actual
        architecture, to the same question -- which ordered roster works
        for which task shape).

        ASG's agent "topology" today is a flat ordered roster (see
        benchmark.py's perspective-rotating attempt loop), not a graph a
        GNN could learn structure over -- building GPTSwarm/G-Designer's
        full graph-topology optimizer, or Agent Symbolic Learning/Gödel
        Agent's self-rewriting strategy update, would be speculative
        complexity for an architecture that doesn't have graph-shaped
        agent communication yet, and self-rewriting introduces safety
        risk beyond the current PolicyEngine's scope. This reuses the
        existing, already-tested procedural-memory substrate instead:
        roster membership and order are recorded as `steps`, and
        `task_description` as the `trigger_pattern` to match future tasks
        against -- observational and recommend-only, like
        recommend_procedure, never auto-applied.

        Args:
            roster: Ordered agent role names used for this attempt.
            task_description: Text describing the task this roster was
                used for, matched lexically by recommend_roster.
            passed: Whether this roster's attempt succeeded.
            **kwargs: Forwarded to record_procedure_outcome (flaw_category,
                critique, evidence_tag, blamed_step -- blamed_step here
                names which specific agent role, not procedure step,
                carried the outcome).

        Returns:
            The underlying procedure node ID.
        """
        name = "roster_" + "_".join(sorted(roster))
        return self.record_procedure_outcome(
            name=name, trigger_pattern=task_description, steps=list(roster), passed=passed, **kwargs
        )

    def recommend_roster(self, task_description: str, **kwargs) -> "dict | None":
        """Recommends a previously-successful roster composition for a task
        shape -- see record_roster_outcome for why this reuses
        recommend_procedure rather than a separate graph/topology learner.

        Args:
            task_description: Text describing the current task.
            **kwargs: Forwarded to recommend_procedure (flaw_category,
                epsilon, rng).

        Returns:
            recommend_procedure's result dict with an added "roster" key
            (identical to "steps", named for this call site's semantics),
            or None if nothing matches.
        """
        result = self.recommend_procedure(task_description, **kwargs)
        if result is None:
            return None
        return {**result, "roster": result["steps"]}

    def record_procedure_outcome(
        self,
        name: str,
        trigger_pattern: str,
        steps: List[str],
        passed: bool,
        flaw_category: "str | None" = None,
        critique: "str | None" = None,
        evidence_tag: "str | None" = None,
        blamed_step: "str | None" = None,
    ) -> str:
        """Records an outcome for a named repair strategy (Phase D3, extended).

        Procedures are identified by name (deterministic node id per
        tenant), so repeated outcomes for the same named strategy
        accumulate on one node instead of creating a new one each time.

        Args:
            name: Stable identifier for the strategy (e.g. "qa_specialist_first").
            trigger_pattern: Text describing the failure shape this strategy
                targets, used for lexical matching in recommend_procedure.
            steps: Human-readable steps the strategy consists of.
            passed: Whether this attempt at the strategy succeeded.
            flaw_category: Optional fixed-taxonomy tag (see FLAW_CATEGORIES)
                describing what kind of failure this outcome addressed.
                Anything outside the fixed set is normalized to "unknown" --
                a fixed taxonomy only stays comparable across strategies if
                it can't silently grow free-text variants.
            critique: Optional short natural-language note on why this
                attempt passed or failed (Reflexion-style). The most recent
                _MAX_STORED_CRITIQUES are kept; older ones are dropped.
            evidence_tag: Optional confidence label (see EVIDENCE_TAGS) for
                how certain this outcome's pass/fail judgment is. An
                ASSUMPTION-tagged outcome moves the EMA score less than a
                FACT-tagged one; omitting the tag gives full confidence
                (identical to behavior before this parameter existed).
                Anything outside the fixed set is normalized to "UNKNOWN".
            blamed_step: Optional credit-attribution note (simplified,
                single-attribution version of ShapleyFlow's per-component
                credit idea, July 2026 topic-page batch): which one of
                `steps` actually caused this outcome, so a caller can later
                see which step in a multi-step strategy is carrying its
                performance instead of crediting/blaming the whole strategy
                uniformly. Not a true Shapley value (that needs marginal
                contribution across step subsets, which would require
                running ablated variants of the strategy -- out of scope
                here); this only tallies a single human/agent-asserted
                blamed step per outcome. Need not be a member of `steps`
                (steps can change between calls; the tally just accumulates
                by string).

        Returns:
            The procedure node ID.
        """
        db = self._get_session()
        try:
            procedure_id = f"procedure_{self.tenant_id}_{name.replace(' ', '_')}"
            existing = db.query(GraphNode).filter(GraphNode.id == procedure_id).first()
            if existing is not None:
                props = json.loads(str(existing.properties) if existing.properties else "{}")
            else:
                props = {
                    "name": name, "trigger_pattern": trigger_pattern, "steps": steps,
                    "success_count": 0, "failure_count": 0, "ema_success_score": None,
                    "flaw_category_counts": {}, "critiques": [], "step_credit": {},
                }

            props["trigger_pattern"] = trigger_pattern
            props["steps"] = steps
            props["success_count"] = props.get("success_count", 0) + (1 if passed else 0)
            props["failure_count"] = props.get("failure_count", 0) + (0 if passed else 1)

            outcome = 1.0 if passed else 0.0
            if evidence_tag is None:
                confidence = _UNTAGGED_CONFIDENCE
            else:
                normalized_tag = evidence_tag if evidence_tag in EVIDENCE_TAGS else _UNRECOGNIZED_EVIDENCE_TAG
                confidence = _EVIDENCE_CONFIDENCE[normalized_tag]
            weighted_outcome = 0.5 + confidence * (outcome - 0.5)
            prior_ema = props.get("ema_success_score")
            props["ema_success_score"] = weighted_outcome if prior_ema is None else _EMA_ALPHA * weighted_outcome + (1 - _EMA_ALPHA) * prior_ema

            category = flaw_category if flaw_category in FLAW_CATEGORIES else _UNKNOWN_FLAW_CATEGORY
            flaw_counts = props.get("flaw_category_counts", {})
            flaw_counts[category] = flaw_counts.get(category, 0) + 1
            props["flaw_category_counts"] = flaw_counts

            if blamed_step:
                step_credit = props.get("step_credit", {})
                counts = step_credit.get(blamed_step, {"success": 0, "failure": 0})
                counts["success" if passed else "failure"] += 1
                step_credit[blamed_step] = counts
                props["step_credit"] = step_credit

            if critique:
                critiques = props.get("critiques", [])
                if evidence_tag is not None:
                    tag_label = evidence_tag if evidence_tag in EVIDENCE_TAGS else _UNRECOGNIZED_EVIDENCE_TAG
                    critiques.append(f"[{tag_label}] {critique}")
                else:
                    critiques.append(critique)
                props["critiques"] = critiques[-_MAX_STORED_CRITIQUES:]

            # Forgetting-curve staleness tracking (MemoryBank, Zhong et al.
            # 2024, July 2026 topic-page batch): a tenant-wide logical touch
            # counter, persisted in the DB (not in-memory) so it survives
            # engine re-instantiation across requests. Each recorded outcome
            # for ANY of this tenant's procedures bumps the counter, and this
            # procedure's own last_touch_index is stamped with the new value
            # -- recommend_procedure uses the gap between "now" and a
            # candidate's last_touch_index to discount stale strategies.
            counter_id = f"_touch_counter_{self.tenant_id}"
            counter_node = db.query(GraphNode).filter(GraphNode.id == counter_id).first()
            counter_props = (
                json.loads(str(counter_node.properties)) if counter_node is not None and counter_node.properties else {}
            )
            touch_index = counter_props.get("value", 0) + 1
            db.merge(GraphNode(
                id=counter_id, tenant_id=self.tenant_id, type="_TouchCounter",
                properties=json.dumps({"value": touch_index}),
            ))
            props["last_touch_index"] = touch_index

            procedure_node = GraphNode(id=procedure_id, tenant_id=self.tenant_id, type="Procedure", properties=json.dumps(props))
            db.merge(procedure_node)
            db.commit()

            if self.agent_db is not None:
                self.agent_db.insert("patterns", procedure_id, embed_text(trigger_pattern))

            return procedure_id
        except Exception as e:
            db.rollback()
            logger.error(f"Failed to record procedure outcome in graph memory: {e}")
            raise
        finally:
            db.close()

    def recommend_procedure(
        self,
        trigger_pattern: str,
        flaw_category: "str | None" = None,
        epsilon: float = 0.0,
        rng: "random.Random | None" = None,
    ) -> "dict | None":
        """Recommends a known strategy for a failure shape.

        Matches trigger_pattern against recorded procedures' trigger
        patterns by lexical Jaccard similarity (same approach as A-MEM
        constraint linking). Ranks by recency-weighted EMA score rather
        than raw success rate, so a strategy's recent performance matters
        more than its full history (AgentNet eq. 2) -- ties broken by
        higher total attempt count (more evidence).

        Pure exploitation (the default, epsilon=0.0) always returns the top
        scorer -- but if every caller always follows that recommendation, a
        strategy that falls slightly behind the leader never gets tried
        again, so its score is frozen forever and an early leader can stay
        "best" long after it stops being best (Braga-Neto 2026's
        exploration-rate discussion of premature swarm convergence). Setting
        epsilon > 0 occasionally returns a different qualifying candidate
        instead, so alternatives keep collecting fresh evidence.

        Args:
            trigger_pattern: Text describing the current failure shape.
            flaw_category: If given, only consider strategies that have at
                least one recorded outcome tagged with this category
                (SwarmAgentic-style slicing: a strategy's aggregate score
                can hide that it only ever worked on a different kind of
                failure). If no candidate has this category represented,
                returns None rather than silently falling back to an
                unfiltered recommendation.
            epsilon: Probability (0.0-1.0) of returning a random
                non-top-scoring qualifying candidate instead of the best
                one. 0.0 (default) is pure exploitation, matching prior
                behavior exactly. Has no effect when 0 or 1 candidates
                qualify -- there's nothing to explore.
            rng: Optional `random.Random` instance for deterministic
                testing. Defaults to the module-level `random` functions.

        Returns:
            {"name": str, "steps": List[str], "success_count": int,
             "failure_count": int, "success_rate": float,
             "ema_success_score": float, "recency_decayed_score": float,
             "flaw_category_counts": dict, "critiques": List[str],
             "step_credit": dict, "context_sufficient": bool,
             "match_similarity": float} for the chosen match (step_credit
            maps a blamed step string to its own {"success": int,
            "failure": int} tally -- see record_procedure_outcome's
            blamed_step param; recency_decayed_score is ema_success_score
            discounted by logical staleness since this strategy was last
            recorded against -- see _FORGETTING_DECAY_RATE -- both
            informational only, ranking still uses ema_success_score;
            context_sufficient is False when the match only barely cleared
            _PROCEDURE_MATCH_THRESHOLD -- see _SUFFICIENT_CONTEXT_MARGIN --
            a caller that wants Google Research's "abstain on insufficient
            context" behavior should treat a context_sufficient=False
            recommendation the way it treats an UNKNOWN-tagged fact, not
            a confident match), or None if nothing matches above the
            threshold, every match has zero recorded attempts, or (with
            flaw_category set) nothing has that category.
        """
        db = self._get_session()
        try:
            procedures = (
                db.query(GraphNode)
                .filter(GraphNode.tenant_id == self.tenant_id, GraphNode.type == "Procedure")
                .order_by(GraphNode.created_at.desc())
                .limit(_PROCEDURE_SCAN_LIMIT)
                .all()
            )
            query_tokens = _tokenize(trigger_pattern)
            if not query_tokens:
                return None

            counter_node = db.query(GraphNode).filter(
                GraphNode.id == f"_touch_counter_{self.tenant_id}"
            ).first()
            current_touch_index = (
                json.loads(str(counter_node.properties)).get("value", 0)
                if counter_node is not None and counter_node.properties
                else 0
            )

            candidates = []
            for node in procedures:
                props = json.loads(str(node.properties) if node.properties else "{}")
                candidate_tokens = _tokenize(props.get("trigger_pattern", ""))
                if not candidate_tokens:
                    continue
                similarity = jaccard(query_tokens, candidate_tokens)
                if similarity < _PROCEDURE_MATCH_THRESHOLD:
                    continue
                success = props.get("success_count", 0)
                failure = props.get("failure_count", 0)
                total = success + failure
                if total == 0:
                    continue
                flaw_counts = props.get("flaw_category_counts", {})
                if flaw_category is not None and flaw_counts.get(flaw_category, 0) == 0:
                    continue
                ema_score = props.get("ema_success_score")
                if ema_score is None:
                    ema_score = success / total
                last_touch_index = props.get("last_touch_index", current_touch_index)
                staleness = max(0, current_touch_index - last_touch_index)
                recency_decayed_score = ema_score * math.exp(-_FORGETTING_DECAY_RATE * staleness)
                candidates.append({
                    "name": props.get("name"),
                    "steps": props.get("steps", []),
                    "success_count": success,
                    "failure_count": failure,
                    "success_rate": success / total,
                    "ema_success_score": ema_score,
                    "recency_decayed_score": recency_decayed_score,
                    "flaw_category_counts": flaw_counts,
                    "critiques": props.get("critiques", []),
                    "step_credit": props.get("step_credit", {}),
                    "context_sufficient": similarity >= _PROCEDURE_MATCH_THRESHOLD + _SUFFICIENT_CONTEXT_MARGIN,
                    "match_similarity": similarity,
                    "match_source": "lexical",
                    "_rank_key": (ema_score, total),
                })

            if self.agent_db is not None:
                seen_names = {c["name"] for c in candidates}
                query_vector = embed_text(trigger_pattern)
                for sim, node_id in self.agent_db.namespaces["patterns"].search(query_vector, k=5):
                    if sim < _SEMANTIC_MATCH_THRESHOLD:
                        continue
                    procedure_id = self.agent_db.id_to_key["patterns"].get(node_id)
                    if procedure_id is None:
                        continue
                    semantic_node = db.query(GraphNode).filter(GraphNode.id == procedure_id).first()
                    if semantic_node is None or not semantic_node.properties:
                        continue
                    props = json.loads(str(semantic_node.properties))
                    if props.get("name") in seen_names:
                        continue  # already found lexically, don't duplicate
                    success = props.get("success_count", 0)
                    failure = props.get("failure_count", 0)
                    total = success + failure
                    if total == 0:
                        continue
                    flaw_counts = props.get("flaw_category_counts", {})
                    if flaw_category is not None and flaw_counts.get(flaw_category, 0) == 0:
                        continue
                    ema_score = props.get("ema_success_score")
                    if ema_score is None:
                        ema_score = success / total
                    candidates.append({
                        "name": props.get("name"),
                        "steps": props.get("steps", []),
                        "success_count": success,
                        "failure_count": failure,
                        "success_rate": success / total,
                        "ema_success_score": ema_score,
                        "recency_decayed_score": ema_score,
                        "flaw_category_counts": flaw_counts,
                        "critiques": props.get("critiques", []),
                        "step_credit": props.get("step_credit", {}),
                        "context_sufficient": False,  # a semantic-only match never clears the lexical margin
                        "match_similarity": sim,
                        "match_source": "semantic",
                        "_rank_key": (ema_score, total),
                    })
                    seen_names.add(props.get("name"))

            if not candidates:
                return None

            best = max(candidates, key=lambda c: c["_rank_key"])
            chosen = best
            if epsilon > 0.0 and len(candidates) > 1:
                picker = rng or random
                if picker.random() < epsilon:
                    alternatives = [c for c in candidates if c is not best]
                    chosen = picker.choice(alternatives)

            return {k: v for k, v in chosen.items() if k != "_rank_key"}
        finally:
            db.close()

    def patch_procedure_if_stale(
        self,
        name: str,
        new_steps: List[str],
        staleness_threshold: float = 0.3,
    ) -> bool:
        """Versioned rewrite of a procedure's steps when it's gone stale
        (Swarm Skills' CREATE-USE-PATCH loop, July 2026 topic-page batch):
        Swarm Skills scores skills on Effectiveness, Utilization, and
        Freshness, and auto-patches ones whose Freshness has dropped rather
        than only ever adjusting a single scalar score. ASG's existing
        recency_decayed_score (see _FORGETTING_DECAY_RATE) already is a
        freshness signal -- the gap between it and the raw ema_success_score
        is exactly "how much has this procedure aged since it last proved
        itself," which this method uses as the patch trigger.

        Not invoked automatically anywhere -- ASG has no reliable signal
        for "the previously-recommended fix didn't work" to auto-trigger
        this from. A caller (e.g. a Verify Phase that notices its
        recommend_procedure suggestion didn't resolve a recurring failure)
        calls this explicitly with a regenerated set of steps.

        Args:
            name: The procedure's stable identifier (same as
                record_procedure_outcome's name param).
            new_steps: The regenerated steps to patch in.
            staleness_threshold: How much relative decay
                (1 - recency_decayed_score/ema_success_score) triggers a
                patch. Below this, the procedure is still fresh enough
                that new_steps is ignored and this returns False.

        Returns:
            True if the procedure was patched (old steps archived into
            step_history, new_steps installed, version incremented).
            False if the procedure doesn't exist, has no recorded
            attempts, or isn't stale enough to warrant patching.
        """
        db = self._get_session()
        try:
            procedure_id = f"procedure_{self.tenant_id}_{name.replace(' ', '_')}"
            node = db.query(GraphNode).filter(GraphNode.id == procedure_id).first()
            if node is None or not node.properties:
                return False

            props = json.loads(str(node.properties))
            ema_score = props.get("ema_success_score")
            total = props.get("success_count", 0) + props.get("failure_count", 0)
            if ema_score is None or total == 0:
                return False

            counter_node = db.query(GraphNode).filter(
                GraphNode.id == f"_touch_counter_{self.tenant_id}"
            ).first()
            current_touch_index = (
                json.loads(str(counter_node.properties)).get("value", 0)
                if counter_node is not None and counter_node.properties
                else 0
            )
            staleness = max(0, current_touch_index - props.get("last_touch_index", current_touch_index))
            recency_decayed_score = ema_score * math.exp(-_FORGETTING_DECAY_RATE * staleness)

            relative_decay = 1.0 - (recency_decayed_score / ema_score) if ema_score > 0 else 0.0
            if relative_decay < staleness_threshold:
                return False

            step_history = props.get("step_history", [])
            step_history.append({"steps": props.get("steps", []), "version": props.get("version", 1)})
            props["step_history"] = step_history[-_MAX_STORED_CRITIQUES:]
            props["steps"] = new_steps
            props["version"] = props.get("version", 1) + 1

            db.merge(GraphNode(id=procedure_id, tenant_id=self.tenant_id, type="Procedure", properties=json.dumps(props)))
            db.commit()
            return True
        except Exception as e:
            db.rollback()
            logger.error(f"Failed to patch procedure '{name}': {e}")
            raise
        finally:
            db.close()

    def extract_insights(self) -> List[str]:
        """ExpeL-style insight extraction (Zhao et al. 2024 "ExpeL: LLM
        Agents Are Experiential Learners", July 2026 topic-page batch):
        synthesizes generalizable, cross-strategy observations from
        accumulated procedural-memory episodes, rather than leaving flaw
        attribution siloed per-strategy the way record_procedure_outcome
        and recommend_procedure otherwise do.

        Purely lexical/statistical -- no LLM call, unlike ExpeL's own
        approach -- so this surfaces recurring flaw categories and blamed
        steps across ALL of this tenant's recorded strategies as short,
        templated observations, not free-form natural-language prose a
        model would need to generate.

        Returns:
            A list of human-readable insight strings. Empty if nothing in
            this tenant's procedural memory recurs across more than one
            strategy.
        """
        db = self._get_session()
        try:
            procedures = (
                db.query(GraphNode)
                .filter(GraphNode.tenant_id == self.tenant_id, GraphNode.type == "Procedure")
                .all()
            )
            category_strategies: Dict[str, set] = {}
            step_failure_counts: Dict[str, int] = {}
            for node in procedures:
                props = json.loads(str(node.properties) if node.properties else "{}")
                name = props.get("name", "unknown")
                for category, count in props.get("flaw_category_counts", {}).items():
                    if count > 0 and category != _UNKNOWN_FLAW_CATEGORY:
                        category_strategies.setdefault(category, set()).add(name)
                for step, credit in props.get("step_credit", {}).items():
                    step_failure_counts[step] = step_failure_counts.get(step, 0) + credit.get("failure", 0)

            insights = []
            for category, strategies in sorted(category_strategies.items()):
                if len(strategies) >= _INSIGHT_MIN_STRATEGIES:
                    insights.append(
                        f"'{category}' recurs across {len(strategies)} strategies "
                        f"({', '.join(sorted(strategies))}) -- likely a systemic "
                        "issue, not one strategy's flaw."
                    )
            for step, failures in sorted(step_failure_counts.items(), key=lambda kv: (-kv[1], kv[0])):
                if failures >= _INSIGHT_MIN_STEP_FAILURES:
                    insights.append(
                        f"step '{step}' has been blamed for {failures} failures across recorded strategies."
                    )
            return insights
        finally:
            db.close()


# NOTE on retrieval design (empirical RAG-for-code-gen study, research.google
# survey, July 2026 topic-page batch): that study found embedding-similarity
# "similar code" retrieval often *hurts* code-gen accuracy (up to -15%),
# while in-context API signatures/decomposed-step retrieval help. If ASG
# ever adds repo-level RAG on top of the procedural-memory recall above,
# retrieve by API/symbol signature match, not by raw code embedding
# similarity -- this module's own procedure recall already follows that
# spirit (lexical Jaccard over described triggers, not code-embedding
# nearest-neighbor).


def materialize_skill_card(procedure: Dict) -> str:
    """Renders a recommend_procedure() candidate dict as a standalone
    markdown "skill card" (Memento-Skills, research.google survey, July
    2026 topic-page batch): Memento-Skills stores skills as externalized
    markdown files an agent can load directly into context, with a
    separate router selecting which card to hand an agent, rather than
    ASG's current DB-query-only recall path.

    Not wired into recommend_procedure's return value or any dispatch
    path -- a caller that wants file-based skill cards (e.g. to write into
    a skills/ directory an agent's context loader reads) converts a
    recommendation to one with this function.

    Args:
        procedure: a candidate dict as returned by recommend_procedure()
            (must have at least "name" and "steps").

    Returns:
        A markdown string: title, precondition/trigger note, numbered
        steps, and an evidence footer (success rate, sample size).
    """
    name = procedure.get("name") or "unnamed-strategy"
    steps = procedure.get("steps") or []
    success = procedure.get("success_count", 0)
    failure = procedure.get("failure_count", 0)
    total = success + failure
    rate = procedure.get("success_rate", (success / total) if total else 0.0)

    lines = [f"# {name}", ""]
    if procedure.get("match_similarity") is not None:
        lines.append(f"_Matched trigger similarity: {procedure['match_similarity']:.2f}_")
        lines.append("")
    lines.append("## Steps")
    for i, step in enumerate(steps, start=1):
        lines.append(f"{i}. {step}")
    lines.append("")
    lines.append("## Evidence")
    lines.append(f"- Success rate: {rate:.0%} ({success}/{total} recorded attempts)")
    if procedure.get("context_sufficient") is False:
        lines.append("- ⚠ marginal match: recorded confidence below the sufficient-context margin")
    return "\n".join(lines)


def validate_procedure_against_heldout(
    procedure: Dict, heldout_outcomes: List[bool], min_success_rate: float = 0.6
) -> bool:
    """Held-out validation gate before procedure promotion (EvoSkill,
    research.google survey, July 2026 topic-page batch): EvoSkill validates
    candidate skills against a held-out task set before promotion, a
    stronger bar than trusting accumulated EMA score alone, which can
    overfit a procedure to one lucky run.

    Not wired into any promotion/confidence-tier transition in this module
    -- a caller that wants this gate (e.g. before promoting a procedure out
    of ASSUMPTION/INFERENCE confidence) replays the candidate against a
    small held-out set of past task outcomes and passes the pass/fail
    results here.

    Args:
        procedure: a candidate dict as returned by recommend_procedure()
            (used only for its name, in the raised-nothing case this stays
            silent -- kept for future logging/telemetry use).
        heldout_outcomes: a list of bool, one per held-out replay (True =
            procedure succeeded on that held-out task).
        min_success_rate: the bar the held-out replay must clear, in
            [0.0, 1.0].

    Returns:
        True if heldout_outcomes is non-empty and its success rate meets
        min_success_rate. False (not an exception) for an empty
        heldout_outcomes -- "no held-out evidence yet" is a legitimate,
        expected state, not an error.
    """
    if not heldout_outcomes:
        return False
    return (sum(1 for o in heldout_outcomes if o) / len(heldout_outcomes)) >= min_success_rate
