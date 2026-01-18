"""Cleanup script: Delete expired games older than 24 hours (logs retention period)."""

import asyncio
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, delete
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


async def cleanup():
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
            from app.models.game import Game, GameStatus
            
            # Find expired games older than 24 hours
            cutoff_time = datetime.now(timezone.utc) - timedelta(hours=24)
            
            # Get expired games that have been expired for more than 24 hours
            # We check updated_at since that's when the status was set to EXPIRED
            stmt = select(Game).where(
                Game.status == GameStatus.EXPIRED,
                Game.updated_at < cutoff_time
            )
            result = await session.execute(stmt)
            expired_games = result.scalars().all()
            
            if not expired_games:
                print("No expired games found that are older than 24 hours.")
                return
            
            game_codes = [game.code for game in expired_games]
            print(f"Found {len(expired_games)} expired game(s) older than 24 hours:")
            for game in expired_games:
                print(f"  - {game.code} ({game.name}) - Expired: {game.updated_at}")
            
            # Delete expired games (CASCADE will handle related data)
            for game in expired_games:
                await session.delete(game)
            
            await session.commit()
            print(f"✓ Successfully deleted {len(expired_games)} expired game(s)")
            print(f"  Game codes: {', '.join(game_codes)}")
            
        except Exception as e:
            await session.rollback()
            print(f"✗ Cleanup failed: {e}")
            raise
        finally:
            await engine.dispose()


if __name__ == "__main__":
    asyncio.run(cleanup())
