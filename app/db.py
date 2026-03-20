"""Database configuration for RG AST Analysis service."""

import os
from datetime import datetime
from sqlalchemy import create_engine, Column, String, DateTime, Integer, Boolean, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.pool import NullPool

# Database URL from environment
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    f"postgresql://{os.getenv('DB_USER', 'doadmin')}:"
    f"{os.getenv('DB_PASSWORD', '')}@"
    f"{os.getenv('DB_HOST', 'resonant-db-do-user-18031534-0.g.db.ondigitalocean.com')}:"
    f"{os.getenv('DB_PORT', '25060')}/"
    f"{os.getenv('DB_NAME', 'defaultdb')}?ssl=require"
)

# Convert to async URL for asyncpg
ASYNC_DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://")

# Create async engine
engine = create_async_engine(
    ASYNC_DATABASE_URL,
    poolclass=NullPool,
    echo=False,
)

# Create async session factory
async_session_maker = sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)

Base = declarative_base()


class SavedAnalysis(Base):
    """Persisted AST Analysis result, scoped per user."""
    __tablename__ = "cv_saved_analyses"

    analysis_id = Column(String(64), primary_key=True)
    user_id = Column(String(64), nullable=False, index=True)
    user_role = Column(String(32), default="user")
    is_superuser = Column(Boolean, default=False)
    project_name = Column(String(255), default="")
    source = Column(String(32), default="unknown")  # github, upload, analyze
    repo_url = Column(String(512), default="")
    stats_json = Column(JSONB, default=dict)     # quick stats for list view (small)
    meta_json = Column(JSONB, default=dict)      # metadata (path, source details)
    analysis_json = Column(JSONB, nullable=False)  # full analysis: nodes + connections
    storage_bytes = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    deleted_at = Column(DateTime, nullable=True)  # soft delete


async def get_db():
    """Dependency for getting async database session."""
    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db():
    """Initialize database tables."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
