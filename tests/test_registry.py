"""Tests for tool registry."""
from pdf_agent.tools.base import BaseTool, ToolResult
from pdf_agent.tools.registry import ToolRegistry
from pdf_agent.schemas.tool import ToolInputSpec, ToolManifest, ToolOutputSpec


class DummyTool(BaseTool):
    def manifest(self):
        return ToolManifest(
            name="dummy",
            label="Dummy",
            category="test",
            inputs=ToolInputSpec(),
            outputs=ToolOutputSpec(),
        )

    def validate(self, params):
        return params

    def run(self, inputs, params, workdir, reporter=None):
        return ToolResult(log="ok")


class TestToolRegistry:
    def test_register_and_get(self):
        reg = ToolRegistry()
        tool = DummyTool()
        reg.register(tool)
        assert "dummy" in reg
        assert reg.get("dummy") is tool
        assert len(reg) == 1

    def test_list_manifests(self):
        reg = ToolRegistry()
        reg.register(DummyTool())
        manifests = reg.list_manifests()
        assert len(manifests) == 1
        assert manifests[0]["name"] == "dummy"

    def test_get_missing(self):
        reg = ToolRegistry()
        assert reg.get("nonexistent") is None
