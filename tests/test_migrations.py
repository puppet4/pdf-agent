"""Tests for Alembic migration baseline consistency."""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


def _load_module(path: Path):
    spec = spec_from_file_location(path.stem, path)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestMigrations:
    def test_only_single_baseline_migration_exists(self):
        versions = sorted(
            path.name
            for path in Path("alembic/versions").glob("*.py")
            if path.name != "__init__.py"
        )
        assert versions == ["0001_initial_schema.py"]

    def test_baseline_migration_is_root_revision(self):
        module = _load_module(Path("alembic/versions/0001_initial_schema.py"))
        assert module.revision == "0001"
        assert module.down_revision is None

    def test_baseline_migration_only_contains_current_files_schema(self):
        content = Path("alembic/versions/0001_initial_schema.py").read_text()
        assert "create_table(" in content
        assert "'files'" in content
        assert "job_steps" not in content
        assert "jobs" not in content
        assert "artifacts" not in content
        assert "users" not in content
        assert "thread_ownership" not in content

    def test_baseline_migration_has_files_index(self):
        content = Path("alembic/versions/0001_initial_schema.py").read_text()
        assert "ix_files_sha256" in content
