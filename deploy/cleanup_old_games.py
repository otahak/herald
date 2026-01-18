"""Cleanup script: Delete expired games older than 24 hours."""

import asyncio
from datetime import datetime, timedelta, timezone
from sqlalchemy import text, select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
import os
from pathlib import Path

# Load environment variables
def load_env():
    """Load .env file if it exists."""
    env_file = Path(__file__).parent.parent / ".env"
    if env_file.exists():
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


async def cleanup_expired_games():
    """Delete expired games older than 24 hours."""
    load_env()
    
    database_url = os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@localhost:5432/herald"
    )
    
    # If DATABASE_URL uses 'db' as hostname (Docker service), replace with localhost
    # This allows running migrations from host machine when Docker exposes port 5432
    if database_url and "@db:" in database_url:
        print("Note: DATABASE_URL uses 'db' hostname (Docker service name)")
        print("Replacing with 'localhost' for host machine access...")
        database_url = database_url.replace("@db:", "@localhost:")
        print(f"Using: {database_url.split('@')[0]}@localhost:5432/herald")
    
    engine = create_async_engine(database_url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    async with async_session() as session:
        try:
            # Find expired games older than 24 hours
            # Games are marked as expired when they meet expiration criteria
            # We want to delete games that have been expired for at least 24 hours
            cutoff_time = datetime.now(timezone.utc) - timedelta(hours=24)
            
            # Find expired games where status was set to expired before cutoff
            # We check last_activity_at to determine when they expired
            # For multiplayer: expired if all disconnected AND last_activity > 1 hour ago
            # For solo: expired if last_activity > 30 days ago
            # We delete if they've been expired for 24+ hours (i.e., last_activity_at is old enough)
            
            # Query for expired games that are old enough to delete
            # A game is expired if status = 'expired'
            # We delete if it's been expired for 24+ hours (check last_activity_at + expiration threshold)
            query = text("""
                SELECT id, code, name, status, is_solo, last_activity_at, created_at
                FROM games
                WHERE status = 'expired'
                AND (
                    (is_solo = false AND last_activity_at < :multiplayer_cutoff)
                    OR
                    (is_solo = true AND last_activity_at < :solo_cutoff)
                )
            """)
            
            # Multiplayer: expired after 1 hour, delete after 24 hours = 25 hours total
            multiplayer_cutoff = cutoff_time - timedelta(hours=1)
            # Solo: expired after 30 days, delete after 24 hours = 30 days + 24 hours total
            solo_cutoff = cutoff_time - timedelta(days=30)
            
            result = await session.execute(
                query,
                {
                    "multiplayer_cutoff": multiplayer_cutoff,
                    "solo_cutoff": solo_cutoff,
                }
            )
            expired_games = result.fetchall()
            
            if not expired_games:
                print("No expired games found to delete.")
                return
            
            print(f"Found {len(expired_games)} expired game(s) to delete:")
            for game in expired_games:
                print(f"  - {game.code} ({game.name}) - Last activity: {game.last_activity_at}")
            
            # Delete expired games (CASCADE will handle related records)
            game_ids = [game.id for game in expired_games]
            delete_query = text("""
                DELETE FROM games
                WHERE id = ANY(:game_ids)
            """)
            
            await session.execute(delete_query, {"game_ids": game_ids})
            await session.commit()
            
            print(f"✓ Successfully deleted {len(expired_games)} expired game(s)")
            
        except Exception as e:
            await session.rollback()
            print(f"✗ Cleanup failed: {e}")
            raise
        finally:
            await engine.dispose()


if __name__ == "__main__":
    asyncio.run(cleanup_expired_games())
