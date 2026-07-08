import os
from datetime import datetime, timezone
from sqlalchemy import (
    create_engine,
    Column,
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

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False}
    if DATABASE_URL.startswith("sqlite")
    else {},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Tenant(Base):
    __tablename__ = "tenants"

    id = Column(String, primary_key=True, index=True)
    name = Column(String, nullable=False)
    api_key_hash = Column(String, nullable=False)
    stripe_customer_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    sessions = relationship(
        "SuccessionSession", back_populates="tenant", cascade="all, delete-orphan"
    )
    token_usages = relationship(
        "TokenUsage", back_populates="tenant", cascade="all, delete-orphan"
    )


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


class RateLimitEntry(Base):
    __tablename__ = "rate_limit_entries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(String, nullable=False, index=True)
    timestamp = Column(Float, nullable=False, index=True)


def init_db():
    Base.metadata.create_all(bind=engine)


def get_db():
    init_db()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
