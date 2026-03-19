#!/usr/bin/env python3
"""
Migration: Add army book columns to players table.
- special_rules (JSONB): faction special rules from army book import
- faction_name (VARCHAR 100): army faction name
- army_book_version (VARCHAR 20): army book version string
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

PROJECT_ROOT = Path(__file__).parent.parent
database_url = os.getenv("DATABASE_URL")
if not database_url:
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and line.startswith("DATABASE_URL="):
                    database_url = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
if not database_url:
    database_url = "postgresql+asyncpg://postgres:postgres@localhost:5432/herald"

COLUMNS = [
    ("special_rules", "JSONB NULL"),
    ("faction_name", "VARCHAR(100) NULL"),
    ("army_book_version", "VARCHAR(20) NULL"),
]


async def migrate():
    engine = create_async_engine(database_url, echo=True)
    try:
        async with engine.begin() as conn:
            for col_name, col_type in COLUMNS:
                result = await conn.execute(text("""
                    SELECT column_name FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'players'
                      AND column_name = :col
                """), {"col": col_name})
                if result.fetchone():
                    print(f"Column '{col_name}' already exists. Skipping.")
                    continue
                # col_name/col_type come from the hardcoded COLUMNS list above — safe to interpolate
                await conn.execute(text(
                    f"ALTER TABLE players ADD COLUMN {col_name} {col_type}"
                ))
                print(f"Added '{col_name}' column to players table.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(migrate())
