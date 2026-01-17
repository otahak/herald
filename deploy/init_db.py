#!/usr/bin/env python3
"""
Initialize database schema for production.
Run this once after setting up PostgreSQL.
"""
import asyncio
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.models import Base

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/herald"
)


async def init_db():
    """Create all tables."""
    engine = create_async_engine(DATABASE_URL, echo=True)
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    print("Database schema initialized successfully!")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(init_db())
