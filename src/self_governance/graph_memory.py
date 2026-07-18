"""Graph Memory Engine leveraging networkx and SQLAlchemy for Deep GraphRAG.

This module provides the GraphMemoryEngine, which records succession events,
decisions, constraints, and roles into a persistent global Knowledge Graph.
"""

import json
import logging
import random
import re
import uuid
import networkx as nx
from typing import List
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from self_governance.db import GraphNode, GraphEdge, get_db

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
_PROCEDURE_SCAN_LIMIT = 200

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
_UNKNOWN_FLAW_CATEGORY = "unknown"
_MAX_STORED_CRITIQUES = 5
# AgentNet eq. 2's decayed-edge-weight formula, applied to a strategy's
# score instead of a graph edge: recent outcomes matter more than old ones,
# without needing full history.
_EMA_ALPHA = 0.8


def _tokenize(text: str) -> set:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w for w in words if w not in _RELATION_STOPWORDS}


class GraphMemoryEngine:
    """Engine for building and querying the GraphRAG memory."""

    def __init__(self, tenant_id: str):
        self.tenant_id = tenant_id

    def _get_session(self) -> Session:
        # Get a db session using the generator
        return next(get_db())

    def add_session_node(self, session_id: int, roster: List[str], features: List[str], constraints: List[str]) -> str:
        """Records a succession session and links it to the roster and injected constraints.
        
        Args:
            session_id: The succession session ID.
            roster: List of agent roles approved.
            features: List of feature names the session is building.
            constraints: List of injected constraints (God's Eye).
            
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
                constraint_id = f"constraint_{uuid.uuid4().hex[:8]}"
                constraint_node = GraphNode(id=constraint_id, tenant_id=self.tenant_id, type="Constraint", properties=json.dumps({"text": constraint}))
                db.merge(constraint_node)

                edge = GraphEdge(tenant_id=self.tenant_id, source_id=session_node_id, target_id=constraint_id, type="CONSTRAINED_BY")
                db.add(edge)

                new_tokens = _tokenize(constraint)
                for prior_id, tokens in prior_tokens:
                    if not tokens or not new_tokens:
                        continue
                    jaccard = len(tokens & new_tokens) / len(tokens | new_tokens)
                    if jaccard >= _RELATION_JACCARD_THRESHOLD:
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

    def record_procedure_outcome(
        self,
        name: str,
        trigger_pattern: str,
        steps: List[str],
        passed: bool,
        flaw_category: "str | None" = None,
        critique: "str | None" = None,
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
                    "flaw_category_counts": {}, "critiques": [],
                }

            props["trigger_pattern"] = trigger_pattern
            props["steps"] = steps
            props["success_count"] = props.get("success_count", 0) + (1 if passed else 0)
            props["failure_count"] = props.get("failure_count", 0) + (0 if passed else 1)

            outcome = 1.0 if passed else 0.0
            prior_ema = props.get("ema_success_score")
            props["ema_success_score"] = outcome if prior_ema is None else _EMA_ALPHA * outcome + (1 - _EMA_ALPHA) * prior_ema

            category = flaw_category if flaw_category in FLAW_CATEGORIES else _UNKNOWN_FLAW_CATEGORY
            flaw_counts = props.get("flaw_category_counts", {})
            flaw_counts[category] = flaw_counts.get(category, 0) + 1
            props["flaw_category_counts"] = flaw_counts

            if critique:
                critiques = props.get("critiques", [])
                critiques.append(critique)
                props["critiques"] = critiques[-_MAX_STORED_CRITIQUES:]

            procedure_node = GraphNode(id=procedure_id, tenant_id=self.tenant_id, type="Procedure", properties=json.dumps(props))
            db.merge(procedure_node)
            db.commit()
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
             "ema_success_score": float, "flaw_category_counts": dict,
             "critiques": List[str]} for the chosen match, or None if
            nothing matches above the threshold, every match has zero
            recorded attempts, or (with flaw_category set) nothing has that
            category.
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

            candidates = []
            for node in procedures:
                props = json.loads(str(node.properties) if node.properties else "{}")
                candidate_tokens = _tokenize(props.get("trigger_pattern", ""))
                if not candidate_tokens:
                    continue
                jaccard = len(query_tokens & candidate_tokens) / len(query_tokens | candidate_tokens)
                if jaccard < _PROCEDURE_MATCH_THRESHOLD:
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
                candidates.append({
                    "name": props.get("name"),
                    "steps": props.get("steps", []),
                    "success_count": success,
                    "failure_count": failure,
                    "success_rate": success / total,
                    "ema_success_score": ema_score,
                    "flaw_category_counts": flaw_counts,
                    "critiques": props.get("critiques", []),
                    "_rank_key": (ema_score, total),
                })

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
