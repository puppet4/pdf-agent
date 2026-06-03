from __future__ import annotations

from pathlib import Path
import tomllib


def _runtime_dependency_names() -> set[str]:
    data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    dependencies = data["project"]["dependencies"]
    names: set[str] = set()
    for dependency in dependencies:
        normalized = dependency.split(";", 1)[0].split("[", 1)[0]
        for marker in (">=", "==", "~=", "<=", ">", "<"):
            normalized = normalized.split(marker, 1)[0]
        names.add(normalized.strip().lower().replace("_", "-"))
    return names


def _pdf_agent_lock_package() -> dict:
    data = tomllib.loads(Path("uv.lock").read_text(encoding="utf-8"))
    return next(package for package in data["package"] if package["name"] == "pdf-agent")


def _lock_entry_names(entries: list[dict]) -> set[str]:
    return {entry["name"].strip().lower() for entry in entries}


def test_registered_tool_runtime_imports_are_packaged_as_runtime_dependencies():
    runtime_dependencies = _runtime_dependency_names()

    expected = {
        "python-barcode",
        "qrcode",
        "python-docx",
        "openpyxl",
        "python-pptx",
        "pdfminer.six",
    }

    assert expected.issubset(runtime_dependencies)


def test_lockfile_keeps_registered_tool_dependencies_on_base_runtime():
    package = _pdf_agent_lock_package()
    expected = {
        "python-barcode",
        "qrcode",
        "python-docx",
        "openpyxl",
        "python-pptx",
        "pdfminer-six",
    }

    base_dependency_names = _lock_entry_names(package["dependencies"])
    dev_dependency_names = _lock_entry_names(package["optional-dependencies"]["dev"])
    metadata_entries = package["metadata"]["requires-dist"]

    assert expected.issubset(base_dependency_names)
    assert expected.isdisjoint(dev_dependency_names)

    for entry in metadata_entries:
        if entry["name"] in expected:
            assert entry.get("marker") != "extra == 'dev'"
