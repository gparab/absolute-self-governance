"""Graph Memory Engine leveraging networkx and SQLAlchemy for Deep GraphRAG.

This module provides the GraphMemoryEngine, which records succession events,
decisions, constraints, and roles into a persistent global Knowledge Graph.
"""

import json
import logging
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
            prior_constraints = db.query(GraphNode).filter(
                GraphNode.tenant_id == self.tenant_id, GraphNode.type == "Constraint"
            ).all()
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
