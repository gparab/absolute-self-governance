"""Database schemas, engine setup, and memory branching module.

Defines the SQLAlchemy tables for multitenancy, succession history, billing,
milestones, agent memory, and a Copy-on-Write memory branching shim.
"""

import os
import json
from typing import Optional
from datetime import datetime, timezone
from sqlalchemy import (
    create_engine,
    Column,
    Index,
    Integer,
    String,
    Float,
    DateTime,
    ForeignKey,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, sessionmaker, relationship
from self_governance.models import SessionStatus

if os.getenv("TESTING") == "True":
    import tempfile

    DATABASE_URL = f"sqlite:///{os.path.join(tempfile.gettempdir(), 'sg_test_' + str(os.getpid()) + '.db')}"
else:
    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///self_governance.db")

if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    # pool_pre_ping recovers from stale connections after DB failover/restart.
    engine = create_engine(
        DATABASE_URL, pool_pre_ping=True, pool_size=5, max_overflow=10
    )
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    """Base declarative class for all SQLAlchemy ORM models."""
    pass


class Tenant(Base):
    """Tenant model representing an isolated client space.

    Attributes:
        id: Unique identifier string for the tenant.
        name: Human-readable tenant name.
        api_key_hash: Hashed API authorization secret.
        created_at: Creation timestamp.
    """
    __tablename__ = "tenants"

    id = Column(String, primary_key=True, index=True)
    name = Column(String, nullable=False)
    api_key_hash = Column(String, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    # No delete cascades: usage and session history is audit data and must
    # survive tenant removal (soft-delete tenants instead of dropping rows).
    sessions = relationship("SuccessionSession", back_populates="tenant")
    token_usages = relationship("TokenUsage", back_populates="tenant")


class SuccessionSession(Base):
    """SuccessionSession model tracking the status of succession processes.

    Attributes:
        id: Autoincremented primary key.
        tenant_id: ForeignKey reference to tenants.id.
        status: Succession status string (e.g. pending, approved).
        approved_roster: Serialized roster string of roles.
        temperature: Temperature at which succession consensus was run.
        threshold: Consensus score threshold target.
        created_at: Creation timestamp.
        updated_at: Updates timestamp.
    """
    __tablename__ = "succession_sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False, index=True)
    status = Column(String, default=SessionStatus.PENDING.value)
    approved_roster = Column(Text, nullable=True)  # JSON or comma-separated string
    temperature = Column(Float, default=1.0)
    threshold = Column(Float, default=8.0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    tenant = relationship("Tenant", back_populates="sessions")


class TokenUsage(Base):
    """TokenUsage model tracking LLM tokens consumption.

    Attributes:
        id: Autoincremented primary key.
        tenant_id: ForeignKey reference to tenants.id.
        prompt_tokens: Quantity of prompt tokens processed.
        completion_tokens: Quantity of completion tokens generated.
        cost_usd: Estimated cost of the execution in USD.
        created_at: Creation timestamp.
    """
    __tablename__ = "token_usages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False, index=True)
    prompt_tokens = Column(Integer, default=0)
    completion_tokens = Column(Integer, default=0)
    cost_usd = Column(Float, default=0.0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    tenant = relationship("Tenant", back_populates="token_usages")

    __table_args__ = (
        Index("ix_token_usages_tenant_created", "tenant_id", "created_at"),
    )


class RateLimitEntry(Base):
    """RateLimitEntry model tracking API call timestamps for a tenant.

    Attributes:
        id: Autoincremented primary key.
        tenant_id: Tenant ID string.
        timestamp: Epoch timestamp float.
    """
    __tablename__ = "rate_limit_entries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(String, nullable=False, index=True)
    timestamp = Column(Float, nullable=False, index=True)


def init_db():
    """Initializes the database by creating all defined metadata tables."""
    Base.metadata.create_all(bind=engine)


def get_db():
    """Yields a database session instance, closing it on completion.

    Yields:
        SessionLocal: SQLAlchemy session database connection.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class Milestone(Base):
    """Milestone model tracking execution progress goals.

    Attributes:
        id: Autoincremented primary key.
        name: Descriptive milestone name.
        status: Execution status string (e.g. pending, completed).
        dependencies: JSON list of prior milestone dependency IDs.
        created_at: Creation timestamp.
    """
    __tablename__ = "milestones"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    status = Column(String, default="PENDING")
    dependencies = Column(Text, nullable=True)  # JSON list of IDs
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class AgentMemory(Base):
    """AgentMemory model representing persistent storage keys for agents.

    Attributes:
        key: Primary key string identifier.
        agent_id: Primary key string referencing the agent.
        value: Text value content stored.
        updated_at: Updates timestamp.
    """
    __tablename__ = "agent_memories"

    key = Column(String, primary_key=True)
    agent_id = Column(String, primary_key=True)
    value = Column(Text, nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


def add_milestone(db, name: str, status: str = "PENDING", dependencies: Optional[list] = None) -> Milestone:
    """Inserts a new milestone into the tracking system.

    Args:
        db: Database session.
        name: Descriptive milestone name.
        status: Active status (defaults to "PENDING").
        dependencies: Optional dependency list of IDs.

    Returns:
        Milestone: The created Milestone record.
    """
    deps_str = json.dumps(dependencies) if dependencies is not None else "[]"
    milestone = Milestone(name=name, status=status, dependencies=deps_str)
    db.add(milestone)
    db.commit()
    db.refresh(milestone)
    return milestone


def update_milestone_status(db, milestone_id: int, status: str) -> Milestone | None:
    """Updates the status of an existing milestone.

    Args:
        db: Database session.
        milestone_id: Target milestone primary key.
        status: New status string to apply.

    Returns:
        The updated Milestone, or None if not found.
    """
    milestone = db.query(Milestone).filter(Milestone.id == milestone_id).first()
    if milestone:
        milestone.status = status
        db.commit()
        db.refresh(milestone)
    return milestone


def get_milestones(db) -> list[Milestone]:
    """Retrieves all stored milestones.

    Args:
        db: Database session.

    Returns:
        List of Milestone records.
    """
    return db.query(Milestone).all()


def prune_completed_milestones(db) -> int:
    """Deletes all milestones with status 'COMPLETED'.

    Args:
        db: Database session.

    Returns:
        The count of removed milestones.
    """
    deleted_count = db.query(Milestone).filter(Milestone.status == "COMPLETED").delete()
    db.commit()
    return deleted_count


class SovereignMemory:
    """Sovereign memory agent interface manager.

    Provides direct methods to set, query, and list memories for specific agent keys.
    """

    def __init__(self, db=None):
        """Initializes SovereignMemory with an optional database session.

        Args:
            db: Optional database session.
        """
        self.db = db

    def set(self, key: str, value: str, agent_id: str, db=None, auto_commit: bool = True) -> AgentMemory:
        """Sets or inserts a memory key-value pair for an agent.

        Args:
            key: Target memory key.
            value: Memory text payload.
            agent_id: Identifies the owner agent.
            db: Optional database session to override class session.
            auto_commit: Commits immediately when True (default -- prior
                behavior, unchanged for every existing caller). A caller
                batching several set() calls into one all-or-nothing
                transaction (see COWMemoryBranch.merge) passes False and
                commits once itself after every call succeeds, instead of
                each individual set() committing separately -- the
                original per-call commit meant a batch that failed halfway
                through had already durably persisted the keys before the
                failure point (peer-review batch, July 2026).

        Returns:
            The created or updated AgentMemory record. When auto_commit is
            False, the record reflects the pending (uncommitted) write --
            callers needing generated defaults (e.g. updated_at) populated
            from the DB should flush the session themselves before reading.
        """
        session = db or self.db
        if not session:
            session = SessionLocal()
            close_session = True
        else:
            close_session = False
        try:
            mem = session.query(AgentMemory).filter(AgentMemory.key == key, AgentMemory.agent_id == agent_id).first()
            if mem:
                mem.value = value
                mem.updated_at = datetime.now(timezone.utc)
            else:
                mem = AgentMemory(key=key, value=value, agent_id=agent_id)
                session.add(mem)
            if auto_commit:
                session.commit()
                session.refresh(mem)
            else:
                session.flush()
            return mem
        finally:
            if close_session and auto_commit:
                session.close()

    def get(self, key: str, agent_id: str, db=None) -> str | None:
        """Retrieves a memory key value for an agent.

        Args:
            key: Target memory key.
            agent_id: Identifies the owner agent.
            db: Optional database session.

        Returns:
            The text memory value, or None if not found.
        """
        session = db or self.db
        if not session:
            session = SessionLocal()
            close_session = True
        else:
            close_session = False
        try:
            mem = session.query(AgentMemory).filter(AgentMemory.key == key, AgentMemory.agent_id == agent_id).first()
            return mem.value if mem else None
        finally:
            if close_session:
                session.close()

    def list_keys(self, agent_id: str, db=None) -> list[str]:
        """Lists all keys populated for a given agent.

        Args:
            agent_id: Identifies the owner agent.
            db: Optional database session.

        Returns:
            A list of key strings.
        """
        session = db or self.db
        if not session:
            session = SessionLocal()
            close_session = True
        else:
            close_session = False
        try:
            mems = session.query(AgentMemory).filter(AgentMemory.agent_id == agent_id).all()
            return [m.key for m in mems]
        finally:
            if close_session:
                session.close()


class COWMemoryBranch:
    """Copy-On-Write memory branching manager.

    Isolates parallel subagent writes and merges changes back on success.
    Enforces graceful fallback to legacy copying if dependencies or DB handles are missing.
    """

    def __init__(self, parent_memory: Optional[SovereignMemory] = None, db = None):
        """Initializes the COWMemoryBranch.

        Args:
            parent_memory: Optional parent SovereignMemory instance.
            db: Optional database session.
        """
        import logging
        self.logger = logging.getLogger("self_governance.db.cow")
        self.parent_memory = parent_memory
        self.db = db
        self.write_buffer: dict[tuple[str, str], str] = {}  # (agent_id, key) -> value
        self.fallback_storage: dict[tuple[str, str], str] = {}

        # Legacy copy fallback initialization
        if self.parent_memory:
            try:
                session = self.db
                if session:
                    mems = session.query(AgentMemory).all()
                    for m in mems:
                        self.fallback_storage[(m.agent_id, m.key)] = m.value
            except Exception as e:
                self.logger.warning(f"Could not perform legacy copy on initialization: {e}. Defaulting to empty fallback.")

    def get(self, key: str, agent_id: str) -> Optional[str]:
        """Gets memory value for an agent, checking the buffer first.

        Args:
            key: Target memory key.
            agent_id: Identifies the owner agent.

        Returns:
            The memory value string or None.
        """
        # Check isolated write buffer first
        if (agent_id, key) in self.write_buffer:
            return self.write_buffer[(agent_id, key)]

        # Read from parent memory or db
        if self.parent_memory:
            try:
                val = self.parent_memory.get(key, agent_id, db=self.db)
                return val
            except Exception as e:
                self.logger.warning(f"Error reading from parent memory, falling back to legacy/in-memory store: {e}")

        # Fallback to local dict
        return self.fallback_storage.get((agent_id, key))

    def set(self, key: str, value: str, agent_id: str):
        """Isolates a memory write within the branch buffer.

        Args:
            key: Target memory key.
            value: Memory value string.
            agent_id: Identifies the owner agent.
        """
        self.write_buffer[(agent_id, key)] = value

    def merge(self, dest_db = None) -> bool:
        """Merges isolated changes back to parent database storage on success.

        Batches every key into a single transaction (peer-review batch,
        July 2026): SovereignMemory.set() used to commit on every call, so
        a batch that failed partway through had already durably persisted
        the keys before the failure point -- a COW branch's whole promise
        is all-or-nothing isolation, which a partial commit breaks. Now
        every set() in the loop passes auto_commit=False and the session
        commits once at the end; an exception before that point means
        nothing in this batch was ever committed, and the session is
        rolled back explicitly for good measure.

        Args:
            dest_db: Optional destination database session.

        Returns:
            True on successful merge, False otherwise.
        """
        target_db = dest_db or self.db
        if self.parent_memory:
            owns_session = target_db is None
            session = target_db or SessionLocal()
            try:
                for (agent_id, key), value in self.write_buffer.items():
                    self.parent_memory.set(key, value, agent_id, db=session, auto_commit=False)
                session.commit()
                self.write_buffer.clear()
                return True
            except Exception as e:
                self.logger.error(f"Failed to merge COW branch back to database: {e}")
                session.rollback()
                # Try merging to fallback storage as a fail-safe, then clear
                # the buffer -- leaving it populated after a handled failure
                # meant the same (now fallback-stored) entries would be
                # attempted again on any future merge() call.
                for (agent_id, key), value in self.write_buffer.items():
                    self.fallback_storage[(agent_id, key)] = value
                self.write_buffer.clear()
                return False
            finally:
                if owns_session:
                    session.close()
        else:
            for (agent_id, key), value in self.write_buffer.items():
                self.fallback_storage[(agent_id, key)] = value
            self.write_buffer.clear()
            return True


class GraphNode(Base):
    """GraphNode model for storing GraphRAG memory nodes.

    Attributes:
        id: Unique identifier string for the node.
        tenant_id: Tenant ID this node belongs to.
        type: Type of node (e.g. Session, Decision, Persona).
        properties: JSON payload of node properties.
        created_at: Creation timestamp.
    """
    __tablename__ = "graph_nodes"

    id = Column(String, primary_key=True)
    tenant_id = Column(String, nullable=False, index=True)
    type = Column(String, nullable=False, index=True)
    properties = Column(Text, nullable=True)  # JSON representation
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class GraphEdge(Base):
    """GraphEdge model for storing GraphRAG memory edges.

    Attributes:
        id: Autoincremented primary key.
        tenant_id: Tenant ID this edge belongs to.
        source_id: Source node ID.
        target_id: Target node ID.
        type: Type of edge (e.g. APPROVED_BY, RESULTED_IN).
        properties: JSON payload of edge properties.
    """
    __tablename__ = "graph_edges"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(String, nullable=False, index=True)
    source_id = Column(String, ForeignKey("graph_nodes.id"), nullable=False, index=True)
    target_id = Column(String, ForeignKey("graph_nodes.id"), nullable=False, index=True)
    type = Column(String, nullable=False, index=True)
    properties = Column(Text, nullable=True)  # JSON representation
