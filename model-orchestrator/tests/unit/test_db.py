"""Migration files: discovery and ordering. Applying them needs Postgres and is an end-to-end matter."""

from __future__ import annotations

from pathlib import Path

import pytest

from model_orchestrator.db import MigrationError, discover_migrations, pending_migrations
from model_orchestrator.settings import Settings


def write(directory: Path, *names: str) -> None:
    for name in names:
        (directory / name).write_text("SELECT 1;\n")


def test_the_shipped_migrations_are_found_in_order() -> None:
    migrations = discover_migrations(Settings.from_env({}).migrations_dir)
    assert [(m.version, m.name) for m in migrations[:4]] == [
        (1, "baseline"),
        (2, "key_extras"),
        (3, "usage_hourly"),
        (4, "admin_audit"),
    ]
    # Later files (the admin website adds its own) continue the numbering without gaps.
    assert [m.version for m in migrations] == list(range(1, len(migrations) + 1))
    assert all(m.path.is_file() for m in migrations)


def test_the_baseline_is_idempotent_sql() -> None:
    """Both live databases adopt 0001 in place, so it must only create what is missing."""
    for migration in discover_migrations(Settings.from_env({}).migrations_dir):
        sql = migration.path.read_text()
        for line in sql.splitlines():
            statement = line.strip().upper()
            if statement.startswith("CREATE TABLE") or statement.startswith("CREATE INDEX"):
                assert "IF NOT EXISTS" in statement, f"{migration.path.name}: {line}"
            if "ADD COLUMN" in statement:
                assert "IF NOT EXISTS" in statement, f"{migration.path.name}: {line}"
        assert "BEGIN" not in sql.upper().split() and "COMMIT" not in sql.upper().split()


def test_order_is_numeric_not_alphabetic(tmp_path: Path) -> None:
    write(tmp_path, "0010_ten.sql", "0002_two.sql", "0001_one.sql", "100_hundred.sql", "notes.txt")
    assert [m.version for m in discover_migrations(tmp_path)] == [1, 2, 10, 100]


def test_duplicate_version_is_an_error(tmp_path: Path) -> None:
    write(tmp_path, "0001_one.sql", "001_again.sql")
    with pytest.raises(MigrationError, match="share version 1"):
        discover_migrations(tmp_path)


def test_bad_file_name_is_an_error(tmp_path: Path) -> None:
    write(tmp_path, "0001_one.sql", "baseline.sql")
    with pytest.raises(MigrationError, match="0001_name.sql"):
        discover_migrations(tmp_path)


def test_missing_directory_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(MigrationError, match="not found"):
        discover_migrations(tmp_path / "nope")


def test_pending_skips_what_is_applied_and_ignores_unknown_versions(tmp_path: Path) -> None:
    write(tmp_path, "0001_one.sql", "0002_two.sql", "0003_three.sql")
    available = discover_migrations(tmp_path)
    assert [m.version for m in pending_migrations(available, set())] == [1, 2, 3]
    assert [m.version for m in pending_migrations(available, {1, 2})] == [3]
    assert pending_migrations(available, {1, 2, 3, 99}) == [], "a database migrated by a newer build is fine"
