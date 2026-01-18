"""Migration: Add unit action event types (UNIT_RUSHED, UNIT_ADVANCED, UNIT_HELD, UNIT_CHARGED, UNIT_ATTACKED)."""

import asyncio
import re
import socket
from sqlalchemy import text
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


async def migrate():
    """Add new unit action event types to eventtype enum."""
    load_env()
    
    database_url = os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@localhost:5432/herald"
    )
    
    # Check if we can resolve 'db' hostname (most reliable Docker detection)
    # If 'db' resolves, we're in Docker and should use it
    can_resolve_db = False
    try:
        socket.gethostbyname("db")
        can_resolve_db = True
    except (socket.gaierror, OSError):
        pass
    
    # Also check other Docker indicators
    has_dockerenv = os.path.exists("/.dockerenv")
    hostname = socket.gethostname()
    is_container_hostname = hostname in ["herald", "herald-db"]
    
    # If we can resolve 'db' OR we're clearly in Docker, use 'db' as hostname
    is_inside_docker = can_resolve_db or has_dockerenv or is_container_hostname
    
    print(f"Debug: DATABASE_URL (before): {database_url.split('@')[0]}@***")
    print(f"Debug: can_resolve_db: {can_resolve_db}, has_dockerenv: {has_dockerenv}, hostname: {hostname}")
    print(f"Debug: is_inside_docker: {is_inside_docker}")
    
    # Adjust database URL: Inside Docker always use 'db', on host use 'localhost'
    if database_url:
        if is_inside_docker:
            # Inside Docker: replace any localhost/127.0.0.1 with db
            if re.search(r'@(localhost|127\.0\.0\.1):', database_url):
                print("Note: Running inside Docker, replacing 'localhost'/'127.0.0.1' with 'db'")
                database_url = re.sub(r'@(localhost|127\.0\.0\.1):', r'@db:', database_url)
            # Also ensure 'db' is used if URL already has it
            elif "@db:" not in database_url:
                # If URL doesn't have db, replace whatever hostname with db
                database_url = re.sub(r'@[^:]+:', r'@db:', database_url)
                print(f"Note: Running inside Docker, forcing hostname to 'db'")
            print(f"Using: {database_url.split('@')[0]}@db:5432/herald")
        else:
            # On host: replace db with localhost if needed
            if "@db:" in database_url:
                print("Note: Running on host machine, replacing 'db' with 'localhost'")
                database_url = database_url.replace("@db:", "@localhost:")
                print(f"Using: {database_url.split('@')[0]}@localhost:5432/herald")
    
    print(f"Debug: Final DATABASE_URL: {database_url.split('@')[0]}@***")
    
    engine = create_async_engine(database_url, echo=True)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    try:
        # New event types to add (uppercase to match database enum pattern)
        new_event_types = [
            'UNIT_RUSHED',
            'UNIT_ADVANCED',
            'UNIT_HELD',
            'UNIT_CHARGED',
            'UNIT_ATTACKED',
        ]
        
        # Use engine.connect() because ALTER TYPE ... ADD VALUE cannot be used in a transaction block
        async with engine.connect() as conn:
            # Get the enum type OID
            get_enum_oid = text("""
                SELECT oid FROM pg_type WHERE typname = 'eventtype'
            """)
            result = await conn.execute(get_enum_oid)
            enum_oid = result.scalar_one_or_none()
            
            if not enum_oid:
                print("✗ Error: 'eventtype' enum type not found")
                raise Exception("eventtype enum not found")
            
            # Add each new enum value if it doesn't exist
            for event_type in new_event_types:
                check_enum = text("""
                    SELECT 1 
                    FROM pg_enum 
                    WHERE enumlabel = :label
                    AND enumtypid = :oid
                """)
                result = await conn.execute(check_enum, {"label": event_type, "oid": enum_oid})
                enum_exists = result.scalar_one_or_none() is not None
                
                if enum_exists:
                    print(f"Enum value '{event_type}' already exists in 'eventtype' enum. Skipping.")
                else:
                    # Add the enum value
                    try:
                        await conn.execute(text(f"""
                            ALTER TYPE eventtype ADD VALUE '{event_type}'
                        """))
                        await conn.commit()
                        print(f"✓ Successfully added '{event_type}' value to 'eventtype' enum")
                    except Exception as e:
                        error_str = str(e).lower()
                        if 'duplicate' in error_str or 'already exists' in error_str:
                            print(f"Enum value '{event_type}' already exists (detected during add). Skipping.")
                            await conn.commit()
                        else:
                            raise
        
        print("✓ Migration completed successfully")
        
    except Exception as e:
        print(f"✗ Migration failed: {e}")
        import traceback
        traceback.print_exc()
        raise
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(migrate())
