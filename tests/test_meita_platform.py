import json
import os
import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import HTTPException

from backend import meita_platform


class MeitaPlatformTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {
            "AI_ENGINE_ENABLED": "true",
            "MEITA_PLATFORM_API_KEY": "local-test-placeholder",
            "MEITA_PLATFORM_AGENT_ID": "agent_test",
        }, clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.create_session = Mock()
        client = SimpleNamespace(
            beta=SimpleNamespace(agents=SimpleNamespace(
                sessions=SimpleNamespace(create=self.create_session),
            )),
        )
        self.factory = Mock(return_value=nullcontext(client))

    def mock_turn(self, status, text):
        class Stream(list):
            def get_final_result(self):
                return SimpleNamespace(output_text=text)

        stream = Stream([SimpleNamespace(type=f"agent.session.turn.{status}")])
        self.create_session.return_value.with_result_collection.return_value = nullcontext(stream)

    def test_disabled_default_and_no_billable_retry_or_tools(self):
        self.assertFalse(meita_platform.enabled())
        self.mock_turn("completed", json.dumps({
            "reply": "  A draft for CEO review.  ", "intent": "chat",
            "title": "", "jobs": [],
        }))
        reply = meita_platform.respond("Review tasks", {"recent_tasks": []}, self.factory)
        self.assertEqual(reply["reply"], "A draft for CEO review.")
        self.assertEqual(self.factory.call_args.kwargs["max_retries"], 0)
        self.create_session.assert_called_once()
        config = self.create_session.call_args.kwargs
        self.assertEqual(config["environment"], {"type": "none"})
        self.assertEqual(config["agent"]["tools"], [])
        self.assertEqual(config["agent"]["multi_agent"], {"enabled": False})
        self.assertEqual(config["agent"]["text"]["format"]["type"], "json_schema")
        self.assertEqual(config["agent_id"], "agent_test")

    def test_failed_turn_rejects_partial_text(self):
        self.mock_turn("failed", "Partial reply")
        with self.assertRaises(HTTPException) as error:
            meita_platform.respond("Review tasks", {}, self.factory)
        self.assertEqual(error.exception.status_code, 502)
        self.create_session.assert_called_once()

    def test_missing_agent_fails_before_creating_client(self):
        del os.environ["MEITA_PLATFORM_AGENT_ID"]
        with self.assertRaises(HTTPException) as error:
            meita_platform.respond("Review tasks", {}, self.factory)
        self.assertEqual(error.exception.status_code, 503)
        self.factory.assert_not_called()

    def test_chat_cannot_carry_jobs(self):
        self.mock_turn("completed", json.dumps({
            "reply": "Routed", "intent": "chat", "title": "",
            "jobs": [{"staff_id": "unauthorized"}],
        }))
        with self.assertRaises(HTTPException) as error:
            meita_platform.respond("Discuss an idea", {}, self.factory)
        self.assertEqual(error.exception.status_code, 502)
