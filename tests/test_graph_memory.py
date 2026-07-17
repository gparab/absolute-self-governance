from unittest.mock import patch

import pytest
from self_governance.db import Base, engine, SessionLocal, GraphNode, GraphEdge
from self_governance.graph_memory import GraphMemoryEngine

@pytest.fixture(scope="function", autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield

def test_graph_memory_records_session_and_queries():
    tenant = "test_tenant_graph"
    engine = GraphMemoryEngine(tenant_id=tenant)
    
    # Act
    engine.add_session_node(
        session_id=999,
        roster=["Backend Wizard", "QA Specialist"],
        features=["Feature_0", "Feature_1"],
        constraints=["Do not use globals"]
    )
    
    # Assert nodes
    db = SessionLocal()
    nodes = db.query(GraphNode).filter_by(tenant_id=tenant).all()
    # 1 session + 2 roles + 2 features + 1 constraint = 6 nodes
    assert len(nodes) == 6
    
    edges = db.query(GraphEdge).filter_by(tenant_id=tenant).all()
    # 2 roles + 2 features + 1 constraint = 5 edges
    assert len(edges) == 5
    db.close()
    
    # Act query
    context = engine.query_context(["Feature_0"])
    
    # Assert query
    assert "GraphRAG Context:" in context
    assert "Do not use globals" in context


def test_query_context_returns_default_message_when_no_match():
    engine = GraphMemoryEngine(tenant_id="test_tenant_graph_empty")

    context = engine.query_context(["Feature_never_built"])

    assert context == "No specific past graph context found for these features."


def test_add_session_node_rolls_back_and_reraises_on_db_error():
    engine = GraphMemoryEngine(tenant_id="test_tenant_graph_error")

    with patch("sqlalchemy.orm.Session.commit", side_effect=RuntimeError("db exploded")):
        with pytest.raises(RuntimeError, match="db exploded"):
            engine.add_session_node(
                session_id=1,
                roster=["Backend Wizard"],
                features=["Feature_0"],
                constraints=[],
            )

    # No orphaned rows survive the rolled-back transaction.
    db = SessionLocal()
    nodes = db.query(GraphNode).filter_by(tenant_id="test_tenant_graph_error").all()
    db.close()
    assert nodes == []
