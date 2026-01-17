"""Tests for deploy/run_migrations.py migration runner."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path
import sys

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from deploy.run_migrations import (
    load_env_file,
    find_migration_scripts,
    _validate_identifier,
    _parse_database_url,
    reset_database,
    _build_script_command,
    main,
)


def test_validate_identifier_valid():
    """Test identifier validation with valid identifiers."""
    assert _validate_identifier("herald", "database name") == "herald"
    assert _validate_identifier("postgres", "database user") == "postgres"
    assert _validate_identifier("test_db_123", "database name") == "test_db_123"


def test_validate_identifier_invalid():
    """Test identifier validation rejects invalid identifiers."""
    with pytest.raises(ValueError, match="Invalid database name"):
        _validate_identifier("test-db", "database name")
    
    with pytest.raises(ValueError, match="Invalid database user"):
        _validate_identifier("user@name", "database user")
    
    with pytest.raises(ValueError, match="Invalid database name"):
        _validate_identifier("", "database name")
    
    with pytest.raises(ValueError, match="Invalid database user"):
        _validate_identifier(None, "database user")


def test_parse_database_url():
    """Test database URL parsing."""
    url = "postgresql+asyncpg://postgres:password@localhost:5432/herald"
    db_name, user, admin_url = _parse_database_url(url)
    
    assert db_name == "herald"
    assert user == "postgres"
    assert admin_url == "postgresql+asyncpg://postgres:password@localhost:5432/postgres"


def test_parse_database_url_missing_db():
    """Test parsing fails when database name is missing."""
    url = "postgresql+asyncpg://postgres:password@localhost:5432/"
    with pytest.raises(ValueError, match="missing a database name"):
        _parse_database_url(url)


def test_parse_database_url_missing_user():
    """Test parsing fails when username is missing."""
    url = "postgresql+asyncpg://:password@localhost:5432/herald"
    with pytest.raises(ValueError, match="missing a username"):
        _parse_database_url(url)


def test_find_migration_scripts(tmp_path):
    """Test finding migration scripts."""
    deploy_dir = tmp_path / "deploy"
    deploy_dir.mkdir()
    
    # Create some migration files
    (deploy_dir / "migrate_add_table.py").touch()
    (deploy_dir / "migrate_add_column.py").touch()
    (deploy_dir / "run_migrations.py").touch()  # Should be excluded
    (deploy_dir / "other_file.py").touch()  # Should be excluded
    
    # Mock DEPLOY_DIR
    with patch("deploy.run_migrations.DEPLOY_DIR", deploy_dir):
        migrations = find_migration_scripts()
    
    assert len(migrations) == 2
    assert all("migrate_" in m.name for m in migrations)
    assert not any("run_migrations" in m.name for m in migrations)


@pytest.mark.asyncio
async def test_reset_database_success():
    """Test database reset executes successfully."""
    database_url = "postgresql+asyncpg://postgres:postgres@localhost:5432/herald"
    
    with patch("deploy.run_migrations.create_async_engine") as mock_engine:
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()
        mock_engine_instance = MagicMock()
        mock_engine_instance.connect.return_value.__aenter__.return_value = mock_conn
        mock_engine_instance.dispose = AsyncMock()
        mock_engine.return_value = mock_engine_instance
        
        await reset_database(database_url)
        
        # Verify engine was created with admin URL
        call_args = mock_engine.call_args
        assert "postgres" in call_args[0][0]  # Admin URL should connect to postgres DB
        
        # Verify terminate, drop, and create were called
        assert mock_conn.execute.call_count == 3


@pytest.mark.asyncio
async def test_main_without_reset_flag(tmp_path, monkeypatch):
    """Test main() runs migrations without reset when --reset-db is not provided."""
    deploy_dir = tmp_path / "deploy"
    deploy_dir.mkdir()
    (deploy_dir / "migrate_test.py").touch()
    
    monkeypatch.setattr("deploy.run_migrations.DEPLOY_DIR", deploy_dir)
    monkeypatch.setattr("deploy.run_migrations.load_env_file", lambda: {"DATABASE_URL": "test"})
    monkeypatch.setattr("deploy.run_migrations.run_migration", AsyncMock(return_value=True))
    
    exit_code = await main(reset_db=False)
    
    assert exit_code == 0


@pytest.mark.asyncio
async def test_main_with_reset_flag(tmp_path, monkeypatch):
    """Test main() resets database when --reset-db flag is provided."""
    deploy_dir = tmp_path / "deploy"
    deploy_dir.mkdir()
    (deploy_dir / "migrate_test.py").touch()
    (deploy_dir / "init_db.py").touch()
    
    monkeypatch.setattr("deploy.run_migrations.DEPLOY_DIR", deploy_dir)
    monkeypatch.setattr("deploy.run_migrations.load_env_file", lambda: {"DATABASE_URL": "postgresql+asyncpg://postgres:postgres@localhost:5432/herald"})
    monkeypatch.setattr("deploy.run_migrations.reset_database", AsyncMock())
    monkeypatch.setattr("deploy.run_migrations._build_script_command", lambda path, env: (["python", str(path)], env))
    monkeypatch.setattr("deploy.run_migrations.subprocess.run", MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr="")))
    monkeypatch.setattr("deploy.run_migrations.run_migration", AsyncMock(return_value=True))
    
    exit_code = await main(reset_db=True)
    
    assert exit_code == 0
    # Verify reset_database was called
    from deploy.run_migrations import reset_database
    reset_database.assert_called_once()


def test_build_script_command_venv_exists(tmp_path):
    """Test _build_script_command prefers venv python."""
    venv_python = tmp_path / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.touch()
    venv_python.chmod(0o755)
    
    script_path = tmp_path / "test_script.py"
    env = {"TEST": "value"}
    
    with patch("deploy.run_migrations.PROJECT_ROOT", tmp_path):
        cmd, cmd_env = _build_script_command(script_path, env)
    
    assert str(venv_python) in cmd
    assert str(script_path) in cmd
    assert cmd_env == env
