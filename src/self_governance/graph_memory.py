"""Graph Memory Engine leveraging networkx and SQLAlchemy for Deep GraphRAG.

This module provides the GraphMemoryEngine, which records succession events,
decisions, constraints, and roles into a persistent global Knowledge Graph.
"""

import json
import logging
import uuid
import networkx as nx
from typing import List
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from self_governance.db import GraphNode, GraphEdge, get_db

logger = logging.getLogger("self_governance.graph")


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
                
            # Create Constraint nodes and edges
            for constraint in constraints:
                constraint_id = f"constraint_{uuid.uuid4().hex[:8]}"
                constraint_node = GraphNode(id=constraint_id, tenant_id=self.tenant_id, type="Constraint", properties=json.dumps({"text": constraint}))
                db.merge(constraint_node)
                
                edge = GraphEdge(tenant_id=self.tenant_id, source_id=session_node_id, target_id=constraint_id, type="CONSTRAINED_BY")
                db.add(edge)
                
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
            for feature in current_features:
                feature_id = f"feature_{feature.replace(' ', '_')}"
                if feature_id in G:
                    # Find sessions that built this feature
                    sessions = [u for u, v, d in G.in_edges(feature_id, data=True) if d.get("type") == "BUILDS"]
                    for sess in sessions:
                        # Find constraints for those sessions
                        constraints = [v for u, v, d in G.out_edges(sess, data=True) if d.get("type") == "CONSTRAINED_BY"]
                        for c in constraints:
                            constraint_text = G.nodes[c].get("text", "")
                            relevant_context.append(f"Past constraint applied to {feature}: {constraint_text}")
                            
            if not relevant_context:
                return "No specific past graph context found for these features."
                
            return "GraphRAG Context:\n- " + "\n- ".join(set(relevant_context))
        finally:
            db.close()
