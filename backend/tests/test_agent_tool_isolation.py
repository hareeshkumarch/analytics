import tempfile
import unittest
from pathlib import Path

from app.application.ports.providers import ModelRequest, ModelResponse, ToolCallResult
from app.application.services.orchestration_service import InvestigationOrchestrator
from app.bootstrap.container import build_container
from app.domain.models import RunMode
from app.domain.providers import ModelProfile, ProviderId, ProviderProfile
from tests.test_tool_loop import ToolCallingGateway, _publish_orders

HIDDEN = ("execute", "read_file", "ls", "write_file", "edit_file", "glob", "grep")


class HostileGateway(ToolCallingGateway):
    def __init__(self, target: Path) -> None:
        super().__init__()
        self.target = target
        self.offered: set[str] = set()
        self.replies: dict[str, str] = {}

    def invoke(self, request: ModelRequest, provider_profile: ProviderProfile, model_profile: ModelProfile, api_key: str) -> ModelResponse:
        self.requests.append(request)
        self.offered.update(t.name for t in request.tools or ())
        tool_msgs = [m for m in request.messages if m.get("role") == "tool"]
        self.replies.update({str(m.get("name")): str(m.get("content")) for m in tool_msgs})
        if request.tools and not tool_msgs:
            args = {"command": f"touch {self.target}", "file_path": str(self.target), "content": "x", "path": "/", "pattern": "*"}
            calls = tuple(ToolCallResult(f"h{i}", name, args) for i, name in enumerate(HIDDEN))
            return ModelResponse(provider_id=self.provider_id, model_id=model_profile.model_id, text="", tool_calls=calls)
        return ModelResponse(provider_id=self.provider_id, model_id=model_profile.model_id, text="done")


class TestHiddenHarnessToolsCannotBeCalled(unittest.TestCase):
    def test_filesystem_and_shell_tools_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "written_by_agent"
            c = build_container(artifact_root=td)
            gateway = HostileGateway(target)
            c.providers.register(gateway)
            c.routing_service._api_keys[ProviderId.OPENAI] = "test-key"
            InvestigationOrchestrator(c).execute_local(
                run_id="run_hostile", workspace_id="ws1", question="q", mode=RunMode.QUICK,
                snapshot_id="snap1", snapshot=_publish_orders(c),
            )
            self.assertFalse(target.exists())
            self.assertFalse(gateway.offered & set(HIDDEN))
            self.assertTrue(gateway.replies)
            for name in HIDDEN:
                if name in gateway.replies:
                    self.assertTrue(gateway.replies[name].startswith("Error:"), gateway.replies[name])


if __name__ == "__main__":
    unittest.main()
