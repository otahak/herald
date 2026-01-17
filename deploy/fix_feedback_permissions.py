#!/usr/bin/env python3
"""Fix permissions on feedback table for the database user."""

import asyncio
import os
import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker


async def main():
    """Grant permissions on feedback table."""
    # Load DATABASE_URL from environment or .env file
    database_url = os.getenv("DATABASE_URL")
    
    if not database_url:
        # Try loading from .env file
        possible_locations = [
            PROJECT_ROOT / ".env",
            Path("/opt/herald/.env"),
            PROJECT_ROOT.parent / ".env",
        ]
        
        for env_file in possible_locations:
            if env_file.exists() and env_file.is_file():
                try:
                    print(f"Attempting to read .env from: {env_file}")
                    with open(env_file, "r") as f:
                        for line in f:
                            line = line.strip()
                            if not line or line.startswith("#"):
                                continue
                            if line.startswith("DATABASE_URL="):
                                database_url = line.split("=", 1)[1].strip()
                                if database_url.startswith('"') and database_url.endswith('"'):
                                    database_url = database_url[1:-1]
                                elif database_url.startswith("'") and database_url.endswith("'"):
                                    database_url = database_url[1:-1]
                                print(f"✓ Loaded DATABASE_URL from .env file: {env_file}")
                                break
                    if database_url:
                        break
                except Exception as e:
                    print(f"Warning: Could not read .env file at {env_file}: {e}")
    
    if not database_url or database_url.strip() == "":
        print("ERROR: DATABASE_URL not found in environment or .env file")
        sys.exit(1)
    
    print(f"Using DATABASE_URL (length: {len(database_url)})")
    
    # Extract username from DATABASE_URL
    db_user = "herald"  # default
    if "@" in database_url:
        try:
            user_part = database_url.split("://")[1].split("@")[0]
            db_user = user_part.split(":")[0]
            print(f"Detected database user: {db_user}")
        except Exception:
            print(f"Could not parse username from DATABASE_URL, using default: {db_user}")
    
    try:
        engine = create_async_engine(database_url, echo=False)
        print("✓ Database engine created successfully")
    except Exception as e:
        print(f"ERROR: Failed to create database engine: {e}")
        sys.exit(1)
    
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    try:
        async with async_session() as session:
            # Grant permissions on feedback table
            grant_stmt = text(f"""
                GRANT ALL PRIVILEGES ON TABLE feedback TO {db_user};
            """)
            await session.execute(grant_stmt)
            await session.commit()
            print(f"✓ Granted permissions on feedback table to user: {db_user}")
            
            # Also grant on all tables in public schema (for future tables)
            grant_all_stmt = text(f"""
                GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO {db_user};
                GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO {db_user};
                ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO {db_user};
                ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO {db_user};
            """)
            await session.execute(grant_all_stmt)
            await session.commit()
            print(f"✓ Granted default permissions on all tables/sequences to user: {db_user}")
            
    except Exception as e:
        print(f"ERROR: Failed to grant permissions: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
