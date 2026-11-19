import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import HTTPException

from backend import staff_agents


class StaffPlatformAgentTests(unittest.TestCase):
    def setUp(self):
        self.payload = {
            "model": "model-test",
            "instructions": "Work only as this staff member.",
            "input": [{"role": "user", "content": "Synthetic task"}],
        }
        self.staff = {"id": "staff-a"}

    def run_staff(self):
        return staff_agents.response(
            self.staff, self.payload, operation="workforce", timeout=75,
        )

    @patch.dict(os.environ, {}, clear=True)
    @patch("backend.staff_agents.ai_provider.response")
    def test_disabled_uses_existing_provider(self, legacy):
        self.assertIs(self.run_staff(), legacy.return_value)

    @patch.dict(os.environ, {
        "STAFF_PLATFORM_ENABLED": "true",
        "STAFF_PLATFORM_API_KEY": "test-placeholder",
        "STAFF_PLATFORM_AGENT_IDS": json.dumps({
            "staff-a": "agent_research", "staff-b": "agent_design",
        }),
    }, clear=True)
    @patch("backend.staff_agents.meita_platform.run_session")
    def test_routes_saved_agents_and_inline_profiles_with_scoped_tools(self, run):
        message = {"type": "message", "content": [{
            "type": "output_text", "text": "Draft", "annotations": [],
        }]}
        run.return_value = SimpleNamespace(messages=[Mock(
            model_dump=Mock(return_value=message),
        )])
        self.payload["tools"] = [{"type": "web_search"}]
        self.assertEqual(self.run_staff()["output"], [message])
        self.assertEqual(run.call_args.kwargs["agent_id"], "agent_research")
        self.assertEqual(run.call_args.args[1]["tools"], [
            {"type": "web_search", "mode": "live"},
        ])
        del self.payload["tools"]
        self.staff = {"id": "staff-b"}
        self.run_staff()
        self.assertEqual(run.call_args.kwargs["agent_id"], "agent_design")
        self.assertEqual(run.call_args.args[1]["tools"], [])
        self.staff = {"id": "staff-b", "platform_agent_id": "agent_bound"}
        self.run_staff()
        self.assertEqual(run.call_args.kwargs["agent_id"], "agent_bound")
        self.staff = {"id": "staff-c"}
        self.run_staff()
        self.assertEqual(run.call_args.kwargs["agent_id"], "")

    @patch.dict(os.environ, {
        "STAFF_PLATFORM_ENABLED": "true",
        "STAFF_PLATFORM_API_KEY": "test-placeholder",
        "STAFF_PLATFORM_AGENT_IDS": '{"staff-a":"agent_same","staff-b":"agent_same"}',
    }, clear=True)
    @patch("backend.staff_agents.meita_platform.run_session")
    def test_duplicate_bindings_are_rejected_before_inference(self, run):
        with self.assertRaises(HTTPException) as error:
            self.run_staff()
        self.assertEqual(error.exception.status_code, 503)
        run.assert_not_called()
