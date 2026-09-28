"""Provider-independent selection and real local MCP-to-HTTP integration."""

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from berry_brain.evaluation_http import configured_selector, MAX_RESPONSE_BYTES
from berry_brain.local import LocalClient
from berry_brain.selection import LessonSelector


LESSONS = [{"id": "waiting", "lesson": "Wait for a running job", "conditions": "Job is still running"}]
STATE = {"query": "job", "current_context": "Job completed", "saved_checkpoint": None}


class SelectorTests(unittest.TestCase):
    def test_one_selector_uses_supplied_context_and_typed_choices(self):
        seen = []
        class Evaluator:
            def evaluate(self, state, questions):
                seen.append((state, questions))
                return {key: {"type": "choice", "choice": "drop"} for key in questions}
        self.assertEqual(LessonSelector(Evaluator())(STATE, LESSONS), {"waiting": "drop"})
        self.assertEqual(seen[0][0], {**STATE, "memories": LESSONS})
        self.assertEqual(set(seen[0][1]["waiting"]["criteria"]), {"keep", "drop", "uncertain"})
        self.assertEqual(STATE["current_context"], "Job completed")

    def test_disabled_uncertain_and_invalid_replies_have_distinct_meanings(self):
        class Evaluator:
            def evaluate(self, state, questions):
                return reply
        select = LessonSelector(Evaluator())
        for reply in (None, {"waiting": {"type": "choice", "choice": "uncertain"}}):
            self.assertEqual(select(STATE, LESSONS), None if reply is None else {"waiting": "uncertain"})
        for reply in ({}, [], {"other": {"type": "choice", "choice": "drop"}},
                      {"waiting": {"type": "score", "choice": "drop"}},
                      {"waiting": {"type": "choice", "choice": "invented"}},
                      {"waiting": {"type": "choice", "choice": []}}):
            with self.subTest(reply=reply), self.assertRaises(ValueError):
                select(STATE, LESSONS)

    def test_empty_or_oversized_input_never_calls_provider(self):
        class Evaluator:
            def evaluate(self, *args):
                raise AssertionError("unexpected provider call")
        select = LessonSelector(Evaluator())
        self.assertEqual(select(STATE, []), {})
        with self.assertRaises(ValueError):
            select({**STATE, "current_context": "x" * 24001}, LESSONS)


class HttpEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.token = self.root / "model.token"
        self.token.write_text("synthetic-private-credential", encoding="utf-8")
        self.token.chmod(0o600)
        self.calls = []
        self.status = 200
        self.raw = None
        self.model = "fixture/choice-model"
        self.choice = "drop"
        self.extra = {}
        test = self
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                test.calls.append((self.path, dict(self.headers), body))
                result = {"model": test.model, "usage": {"input_tokens": 15, "output_tokens": 3},
                          "answers": {key: {"type": "choice", "choice": test.choice} for key in body["questions"]},
                          **test.extra}
                raw = test.raw if test.raw is not None else json.dumps(result).encode()
                self.send_response(test.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                if test.status == 302:
                    self.send_header("Location", test.url + "/must-not-receive-key")
                self.end_headers()
                try:
                    self.wfile.write(raw)
                except (ConnectionError, BrokenPipeError):
                    pass
            def log_message(self, *args):
                pass
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        self.url = f"http://127.0.0.1:{self.server.server_port}/evaluate"
        self.settings = {"url": self.url, "model": self.model, "token_file": "model.token"}
        self.config = self.root / "evaluation.json"
        self.config.write_text(json.dumps(self.settings), encoding="utf-8")

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def selector(self):
        return configured_selector(self.config)

    def test_http_contract_exact_model_credential_and_custom_endpoint(self):
        self.assertEqual(self.selector()(STATE, LESSONS), {"waiting": "drop"})
        path, headers, body = self.calls[0]
        self.assertEqual(path, "/evaluate")
        self.assertEqual(headers["Authorization"], "Bearer synthetic-private-credential")
        self.assertEqual(body["model"], self.model)
        self.assertEqual(body["state"]["current_context"], STATE["current_context"])
        self.assertNotIn("synthetic-private-credential", json.dumps(body))
        self.assertEqual(len(self.calls), 1)

    def test_alias_requires_explicit_expected_response_model(self):
        self.model = "fixture/resolved-model"
        with self.assertRaisesRegex(ValueError, "model differs"):
            self.selector()(STATE, LESSONS)
        self.config.write_text(json.dumps({**self.settings, "response_model": self.model}), encoding="utf-8")
        self.assertEqual(self.selector()(STATE, LESSONS), {"waiting": "drop"})

    def test_http_failures_and_redirects_never_echo_credentials_or_retry(self):
        self.raw = self.token.read_bytes()
        for self.status in (302, 401, 429, 500):
            before = len(self.calls)
            with self.subTest(status=self.status), self.assertRaises(ValueError) as caught:
                self.selector()(STATE, LESSONS)
            self.assertNotIn(self.token.read_text(), str(caught.exception))
            self.assertEqual(len(self.calls), before + 1)
            self.assertEqual(self.calls[-1][0], "/evaluate")

    def test_broken_or_oversized_responses_fail_without_content(self):
        for self.raw in (b'not JSON synthetic-private-credential', b'\xff', b'x' * (MAX_RESPONSE_BYTES + 1)):
            with self.subTest(size=len(self.raw)), self.assertRaises(ValueError) as caught:
                self.selector()(STATE, LESSONS)
            self.assertNotIn("synthetic-private-credential", str(caught.exception))
        self.raw = None
        for self.extra in ({"answers": None}, {"usage": {}}, {"usage": {"input_tokens": True, "output_tokens": 3}},
                           {"usage": {"input_tokens": -1, "output_tokens": 3}}, {"model": "unrequested"}):
            with self.subTest(extra=self.extra), self.assertRaises(ValueError):
                self.selector()(STATE, LESSONS)

    def test_timeout_is_bounded_and_never_returns_transport_details(self):
        selector = self.selector()
        with patch.object(selector.evaluator.http, "open", side_effect=TimeoutError("synthetic-private-credential")) as send:
            with self.assertRaisesRegex(ValueError, "evaluation request failed"):
                selector(STATE, LESSONS)
            self.assertEqual(send.call_args.kwargs["timeout"], 5)
            self.assertEqual(send.call_count, 1)

    def test_environment_reference_and_key_rotation(self):
        self.config.write_text(json.dumps({"url": self.url, "model": self.model, "token_env": "BRAIN_TEST_KEY"}), encoding="utf-8")
        with patch.dict(os.environ, {"BRAIN_TEST_KEY": "first-synthetic-key"}):
            selector = self.selector()
            selector(STATE, LESSONS)
            os.environ["BRAIN_TEST_KEY"] = "second-synthetic-key"
            selector(STATE, LESSONS)
        self.assertEqual([r[1]["Authorization"] for r in self.calls],
                         ["Bearer first-synthetic-key", "Bearer second-synthetic-key"])

    def test_config_rejects_unsafe_urls_inline_keys_and_missing_credentials(self):
        configs = [{**self.settings, "url": value} for value in (
            "http://external.example/evaluate", "https://user:private@example.org", "https://example.org?token=private",
            "https://example.org/#fragment", "https:///missing-host", "https://example.org/\ninjected")]
        configs += [{**self.settings, "api_key": "private-inline-value"}, {**self.settings, "token_env": "ALSO_SET"},
                    {"url": self.url, "model": self.model}, {**self.settings, "token_file": "absent.token"}]
        for config in configs:
            self.config.write_text(json.dumps(config), encoding="utf-8")
            with self.subTest(config=config), self.assertRaises(ValueError) as caught:
                self.selector()
            self.assertNotIn("private-inline-value", str(caught.exception))
        self.assertEqual(self.calls, [])

    @unittest.skipIf(os.name == "nt", "Windows uses account ACLs")
    def test_public_or_linked_token_files_are_rejected(self):
        self.token.chmod(0o644)
        with self.assertRaises(ValueError):
            self.selector()
        self.token.chmod(0o600)
        link = self.root / "linked.token"
        link.symlink_to(self.token)
        self.config.write_text(json.dumps({**self.settings, "token_file": str(link)}), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.selector()

    def seed(self):
        client = LocalClient(self.root / "data", "test", ["demo"])
        def call(operation, **kwargs):
            return client.request(operation, {"project": "demo", "task_id": "source", **kwargs})
        event = call("record", event_id="one", problem="Job running", action="Checked job state",
                     result="Waiting was correct", outcome="success",
                     evidence=[{"source": "tool", "reference": "test:source", "excerpt": "Running job confirmed"}])
        lesson = call("propose", lesson="Wait for a running job", conditions="Job is still running", source_ids=[event["id"]])
        for task in ("first", "second"):
            trial = call("trial", task_id=task, lesson_id=lesson["id"])
            call("feedback", task_id=task, lesson_id=lesson["id"], receipt_id=trial["receipt_id"],
                 outcome="helpful", comparison="Waiting avoided a duplicate job", evidence=[
                     {"source": "tool", "reference": "test:" + task, "excerpt": "Confirmed running job in " + task}])
        return client, lesson

    def recall_process(self, query="job"):
        args = [sys.executable, "-I", "-m", "berry_brain.client", "--local", "--data-dir", str(self.root / "data"),
                "--identity", "test", "--project", "demo", "--selection-config", str(self.config), "--call", "recall"]
        process = subprocess.run(args, input=json.dumps({"project": "demo", "task_id": "current", "query": query,
                                 "context": "Job completed"}), capture_output=True, text=True, timeout=15)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertNotIn("synthetic-private-credential", process.stdout + process.stderr)
        return json.loads(process.stdout)

    def test_installed_local_client_selects_and_preserves_data_on_provider_failure(self):
        client, lesson = self.seed()
        result = self.recall_process()
        self.assertEqual(result["selection"]["status"], "applied")
        self.assertEqual(result["lessons"], [])
        self.choice = "uncertain"
        self.assertEqual(self.recall_process()["lessons"][0]["id"], lesson["id"])
        self.status = 503
        result = self.recall_process()
        self.assertEqual(result["selection"]["status"], "unavailable")
        self.assertEqual(result["lessons"][0]["id"], lesson["id"])
        stored = client.request("trial", {"project": "demo", "task_id": "check", "lesson_id": lesson["id"]})
        self.assertEqual(stored["lesson"]["status"], "active")

    def test_no_search_or_no_matches_makes_no_evaluation_request(self):
        self.seed()
        self.recall_process("")
        self.recall_process("unrelated")
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
