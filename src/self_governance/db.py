import os
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
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

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
Base = declarative_base()


class Tenant(Base):
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
    __tablename__ = "succession_sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False, index=True)
    status = Column(String, default="PENDING")
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
    __tablename__ = "rate_limit_entries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(String, nullable=False, index=True)
    timestamp = Column(Float, nullable=False, index=True)


def init_db():
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
