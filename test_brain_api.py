"""Exercise the authenticated API and the actual MCP adapter together."""

import json
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient

from brain import install
from brain_client import dispatch
from brain_local import LocalClient
from test_brain import POLICY


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / "policy.json").write_text(json.dumps(POLICY))
        def auth(authorization: str = Header(default="")):
            if authorization not in {"codex", "claude", "reader", "outsider"}:
                raise HTTPException(401)
            return authorization
        app = FastAPI()
        install(app, auth, root / "brain.sqlite3", root / "policy.json", start_worker=False)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def test_local_and_server_share_contract_and_learning_rules(self):
        root = Path(self.tmp.name)
        (root / "brain.sqlite3").chmod(0o600)
        local = LocalClient(root, "codex", ["demo"])
        headers = {"Authorization": "codex"}
        self.assertEqual(local.request("tools"), self.client.get("/v1/brain/tools", headers=headers).json())
        args = {"project": "demo", "task_id": "source"}
        event = local.request("record", {**args, "event_id": "one", "problem": "Missing input",
            "action": "Check input", "result": "Found missing input", "outcome": "success",
            "evidence": [{"source": "tool", "reference": "test:source", "excerpt": "Missing input reproduced"}]})
        lesson = self.client.post("/v1/brain/propose", headers=headers, json={**args,
            "lesson": "Check input", "conditions": "Missing input errors", "source_ids": [event["id"]]}).json()
        self.assertEqual(local.request("recall", {**args, "query": "input"})["lessons"], [])
        for task, expected in (("later-one", "candidate"), ("later-two", "active")):
            trial = local.request("trial", {**args, "task_id": task, "lesson_id": lesson["id"]})
            reply = self.client.post("/v1/brain/feedback", headers=headers, json={**args, "task_id": task,
                "lesson_id": lesson["id"], "receipt_id": trial["receipt_id"], "outcome": "helpful",
                "comparison": "Input check found the cause", "evidence": [{"source": "tool",
                    "reference": "test:" + task, "excerpt": "Cause checked in " + task}]}).json()
            self.assertEqual(reply["lesson"]["status"], expected)
        self.assertEqual(local.request("recall", {**args, "query": "input"})["lessons"][0]["id"], lesson["id"])
        trial = local.request("trial", {**args, "task_id": "harm", "lesson_id": lesson["id"]})
        local.request("feedback", {**args, "task_id": "harm", "lesson_id": lesson["id"],
            "receipt_id": trial["receipt_id"], "outcome": "harmful", "comparison": "Check delayed diagnosis",
            "evidence": [{"source": "tool", "reference": "test:harm", "excerpt": "Unrelated error; check wasted time"}]})
        recalled = self.client.post("/v1/brain/recall", headers=headers, json={**args, "query": "input"}).json()
        self.assertEqual(recalled["lessons"], [])

    def test_auth_schema_and_scope(self):
        self.assertEqual(self.client.get("/v1/brain/tools").status_code, 401)
        self.assertEqual(self.client.get("/v1/brain/tools", headers={"Authorization": "outsider"}).status_code, 403)
        tools = self.client.get("/v1/brain/tools", headers={"Authorization": "reader"}).json()["tools"]
        self.assertEqual({t["name"] for t in tools}, {"brain_recall", "brain_history"})
        self.assertEqual(self.client.post("/v1/brain/recall", headers={"Authorization": "codex"}, json={"project": "secret", "task_id": "one"}).status_code, 403)
        self.assertEqual(self.client.post("/v1/brain/recall", headers={"Authorization": "codex"}, json={"project": "demo", "task_id": "one", "unknown": True}).status_code, 422)

    def test_mcp_checkpoint_from_codex_recalled_by_claude(self):
        class Adapter:
            def __init__(inner, actor):
                inner.actor = actor
            def request(inner, path, data=None):
                headers = {"Authorization": inner.actor}
                result = self.client.get("/v1/brain/" + path, headers=headers) if data is None else self.client.post("/v1/brain/" + path, headers=headers, json=data)
                if result.is_error:
                    raise RuntimeError(str(result.status_code))
                return result.json()
        codex, claude = Adapter("codex"), Adapter("claude")
        initialized = dispatch(codex, {"method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
        self.assertIn("demo", initialized["instructions"])
        data = {"project": "demo", "task_id": "shared-task", "expected_version": 0,
                "goal": "Fix missing evidence", "state": "Source gap reproduced", "status": "active"}
        saved = dispatch(codex, {"method": "tools/call", "params": {"name": "brain_checkpoint", "arguments": data}})
        self.assertFalse(saved["isError"])
        read = dispatch(claude, {"method": "tools/call", "params": {"name": "brain_recall", "arguments": {"project": "demo", "task_id": "shared-task"}}})
        self.assertEqual(json.loads(read["content"][0]["text"])["checkpoint"]["body"]["state"], data["state"])
        failed = dispatch(claude, {"method": "tools/call", "params": {"name": "brain_checkpoint", "arguments": {**data, "state": "Stale edit"}}})
        self.assertTrue(failed["isError"])

    def test_mcp_calls_engine_once_and_still_checks_current_permissions(self):
        paths = []
        class Adapter:
            def request(inner, path, data=None):
                paths.append(path)
                response = self.client.post("/v1/brain/" + path, headers={"Authorization": "reader"}, json=data)
                if response.is_error:
                    raise RuntimeError(str(response.status_code))
                return response.json()
        client = Adapter()
        args = {"project": "demo", "task_id": "one"}
        result = dispatch(client, {"method": "tools/call", "params": {"name": "brain_recall", "arguments": args}})
        self.assertFalse(result["isError"])
        self.assertEqual(paths, ["recall"])
        record = {**args, "event_id": "denied", "problem": "Input", "action": "Check", "result": "Checked",
                  "outcome": "success", "evidence": [{"source": "tool", "reference": "test:one", "excerpt": "Checked input"}]}
        result = dispatch(client, {"method": "tools/call", "params": {"name": "brain_record", "arguments": record}})
        self.assertTrue(result["isError"])
        self.assertEqual(result["content"][0]["text"], "403")
        for name in ("brain_missing", "brain_tools"):
            result = dispatch(client, {"method": "tools/call", "params": {"name": name, "arguments": args}})
            self.assertTrue(result["isError"])


if __name__ == "__main__":
    unittest.main()
