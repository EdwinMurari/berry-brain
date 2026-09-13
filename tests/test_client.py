"""Local installation, real MCP processes, privacy, and shared-engine regressions."""

import json
import io
import importlib.metadata
import urllib.error
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from berry_brain.client import Client, MAX_BYTES, dispatch
from berry_brain.local import LocalClient, default_directory, private_database
from berry_brain import configure as configure_client


class LocalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "private data"

    def client(self, identity="codex", projects=None):
        return LocalClient(self.root, identity, projects or ["demo"])

    def test_empty_install_and_cross_client_resume_without_network(self):
        with patch("socket.socket", side_effect=AssertionError("network access")):
            codex = self.client()
            args = {"project": "demo", "task_id": "shared"}
            empty = codex.request("recall", args)
            self.assertIsNone(empty["checkpoint"])
            self.assertEqual(empty["lessons"], [])
            self.assertEqual(codex.request("history", args)["events"], [])
            saved = codex.request("checkpoint", {**args, "expected_version": 0, "goal": "Fix build",
                "state": "Reproduced missing input", "status": "active"})
            claude = self.client("claude")
            self.assertEqual(claude.request("recall", args)["checkpoint"], saved)
            self.assertEqual(len(claude.request("tools")["tools"]), 8)
            with self.assertRaises(RuntimeError):
                claude.request("checkpoint", {**args, "expected_version": 0, "goal": "Fix build",
                    "state": "Overwrite stale state", "status": "active"})

    def test_project_and_install_isolation(self):
        args = {"project": "demo", "task_id": "shared"}
        self.client().request("checkpoint", {**args, "expected_version": 0, "goal": "Fix build",
            "state": "Saved", "status": "active"})
        with self.assertRaises(RuntimeError):
            self.client("claude", ["other"]).request("recall", args)
        independent = LocalClient(self.root.parent / "other install", "codex", ["demo"])
        self.assertIsNone(independent.request("recall", args)["checkpoint"])
        with self.assertRaises(ValueError):
            LocalClient(self.root, "invalid client", ["demo"])
        with self.assertRaises(ValueError):
            LocalClient(self.root, "codex", ["../private"])

    def test_validation_error_does_not_echo_payload(self):
        reply = dispatch(self.client(), {"method": "tools/call", "params": {
            "name": "brain_recall", "arguments": {"project": "demo", "task_id": "one", "query": {"private": "do not echo"}}}})
        self.assertTrue(reply["isError"])
        self.assertNotIn("do not echo", json.dumps(reply))

    def test_malformed_tool_calls_cannot_become_catalogue_requests(self):
        client = self.client()
        for params in (None, [], {"name": "brain_tools", "arguments": None},
                       {"name": "brain_record", "arguments": []}):
            with self.subTest(params=params), self.assertRaises(ValueError):
                dispatch(client, {"method": "tools/call", "params": params})
        reply = dispatch(client, {"method": "tools/call", "params": {"name": "brain_tools"}})
        self.assertTrue(reply["isError"])
        self.assertEqual(len(dispatch(client, {"method": "tools/list"})["tools"]), 8)

    def process(self, identity, messages):
        command = [sys.executable, "-I", "-m", "berry_brain.client",
                   "--local", "--data-dir", str(self.root), "--identity", identity, "--project", "demo"]
        result = subprocess.run(command, input="".join(json.dumps(m) + "\n" for m in messages),
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return [json.loads(line) for line in result.stdout.splitlines()]

    def test_real_mcp_lifecycle_and_restart(self):
        args = {"project": "demo", "task_id": "shared", "expected_version": 0,
                "goal": "Fix input", "state": "Input checked", "status": "active"}
        replies = self.process("codex", [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "brain_checkpoint", "arguments": args}},
        ])
        self.assertEqual([r["id"] for r in replies], [1, 2, 3])
        self.assertEqual(len(replies[1]["result"]["tools"]), 8)
        self.assertFalse(replies[2]["result"]["isError"])
        recalled = self.process("claude", [{"jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {"name": "brain_recall", "arguments": {"project": "demo", "task_id": "shared"}}}])
        self.assertEqual(json.loads(recalled[0]["result"]["content"][0]["text"])["checkpoint"]["body"]["state"], args["state"])

    def test_oversized_frame_cannot_execute_its_tail(self):
        args = {"project": "demo", "task_id": "injected", "expected_version": 0,
                "goal": "Should not save", "state": "Rejected", "status": "active"}
        mutation = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": "brain_checkpoint", "arguments": args}}
        payload = " " * (MAX_BYTES + 1) + json.dumps(mutation) + '\n{"jsonrpc":"2.0","id":2,"method":"ping"}\n'
        command = [sys.executable, "-I", "-m", "berry_brain.client",
                   "--local", "--data-dir", str(self.root), "--project", "demo"]
        result = subprocess.run(command, input=payload, capture_output=True, text=True, check=True, timeout=30)
        replies = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(len(replies), 2)
        self.assertIn("error", replies[0])
        self.assertEqual(replies[1], {"jsonrpc": "2.0", "id": 2, "result": {}})
        self.assertIsNone(self.client().request("recall", {"project": "demo", "task_id": "injected"})["checkpoint"])

    def test_mcp_output_is_utf8_even_with_ascii_process_encoding(self):
        args = {"project": "demo", "task_id": "unicode", "expected_version": 0,
                "goal": "Check café labels ☕", "state": "Checked", "status": "active"}
        command = [sys.executable, "-I", "-m", "berry_brain.client",
                   "--local", "--data-dir", str(self.root), "--project", "demo", "--call", "checkpoint"]
        result = subprocess.run(command, input=json.dumps(args).encode(), capture_output=True, check=True,
                                env={**os.environ, "PYTHONIOENCODING": "ascii"}, timeout=30)
        self.assertEqual(json.loads(result.stdout)["body"]["goal"], args["goal"])

    def test_mcp_reports_installed_version(self):
        result = self.process("codex", [{"jsonrpc": "2.0", "id": 1, "method": "initialize"}])[0]
        self.assertEqual(result["result"]["serverInfo"]["version"], importlib.metadata.version("berry-brain"))

    def test_invalid_requests_do_not_save_and_stream_recovers(self):
        args = {"project": "demo", "task_id": "invalid-id", "expected_version": 0,
                "goal": "Must not save", "state": "Rejected", "status": "active"}
        messages = [
            {"jsonrpc": "2.0", "id": None, "method": "tools/call", "params": {"name": "brain_checkpoint", "arguments": args}},
            {"jsonrpc": "2.0", "id": True, "method": "tools/call", "params": {"name": "brain_checkpoint", "arguments": args}},
            {"jsonrpc": "2.0", "id": 3, "method": "initialize", "params": None},
            {"jsonrpc": "2.0", "id": 4, "method": "missing-method"},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 5, "method": "ping"},
        ]
        replies = self.process("codex", messages)
        self.assertEqual([r["error"]["code"] for r in replies[:-1]], [-32600, -32600, -32602, -32601])
        self.assertIsNone(replies[0]["id"])
        self.assertEqual(replies[-1]["result"], {})
        self.assertIsNone(self.client().request("recall", {"project": "demo", "task_id": "invalid-id"})["checkpoint"])

    def test_parse_error_does_not_stop_next_request(self):
        command = [sys.executable, "-I", "-m", "berry_brain.client", "--local", "--data-dir", str(self.root)]
        result = subprocess.run(command, input='not json\n{"jsonrpc":"2.0","id":1,"method":"ping"}\n',
                                capture_output=True, text=True, check=True, timeout=30)
        replies = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(replies[0]["error"]["code"], -32700)
        self.assertEqual(replies[1]["result"], {})

    def test_http_errors_do_not_return_reflected_credentials(self):
        token = self.root.parent / "test.token"
        token.write_text("private-example-token")
        token.chmod(0o600)
        client = Client({"url": "https://brain.example.org", "identity": "test", "token_file": str(token)})
        error = urllib.error.HTTPError(client.base, 403, "reflected credential", {}, io.BytesIO(b"private-example-token"))
        with patch.object(client.http, "open", side_effect=error), self.assertRaises(RuntimeError) as caught:
            client.request("tools")
        self.assertNotIn(client.token, str(caught.exception))
        self.assertIn("403", str(caught.exception))

    def test_http_config_rejects_bad_headers_without_echoing_token(self):
        token = self.root.parent / "test.token"
        token.write_text("private-token\ninjected-header")
        token.chmod(0o600)
        config = {"url": "https://brain.example.org", "identity": "test", "token_file": str(token)}
        with self.assertRaises(ValueError) as caught:
            Client(config)
        self.assertNotIn("private-token", str(caught.exception))
        token.write_text("valid-example-token")
        for url in ("https:///missing-host", "https://user:password@example.org", "http://example.org"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                Client({**config, "url": url})

    def test_windows_http_token_uses_account_permissions(self):
        token = self.root.parent / "test.token"
        token.write_text("test-token")
        token.chmod(0o644)
        config = {"url": "http://localhost:1234", "identity": "test", "token_file": str(token)}
        with patch("berry_brain.client.os", SimpleNamespace(name="nt")):
            self.assertEqual(Client(config).token, "test-token")
        if os.name != "nt":
            with self.assertRaises(ValueError):
                Client(config)

    def test_concurrent_process_start_and_writes(self):
        def write(index):
            return self.process("client-" + str(index), [{"jsonrpc": "2.0", "id": index, "method": "tools/call",
                "params": {"name": "brain_checkpoint", "arguments": {"project": "demo", "task_id": str(index),
                    "expected_version": 0, "goal": "Check input", "state": "Checked", "status": "active"}}}])
        with ThreadPoolExecutor(max_workers=4) as pool:
            for replies in pool.map(write, range(8)):
                self.assertFalse(replies[0]["result"]["isError"])
        self.assertEqual(len(self.client().request("history", {"project": "demo", "task_id": "audit"})["events"]), 8)

    @unittest.skipIf(os.name == "nt", "POSIX mode checks; Windows uses account ACLs")
    def test_private_permissions_and_link_rejection(self):
        path = private_database(self.root)
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.root.chmod(0o755)
        with self.assertRaises(ValueError):
            private_database(self.root)
        self.root.chmod(0o700)
        path.chmod(0o644)
        with self.assertRaises(ValueError):
            private_database(self.root)
        path.chmod(0o600)
        alias = self.root.parent / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            private_database(alias)
        sidecar = Path(str(path) + "-wal")
        sidecar.symlink_to(path)
        with self.assertRaises(ValueError):
            private_database(self.root)

    def test_platform_data_paths(self):
        with patch("berry_brain.local.sys.platform", "darwin"):
            self.assertEqual(default_directory(), Path.home() / "Library/Application Support/berry-brain")
        with patch("berry_brain.local.sys.platform", "win32"), patch.dict(os.environ, {"LOCALAPPDATA": str(self.root)}):
            self.assertEqual(default_directory(), self.root / "berry-brain")
        with patch("berry_brain.local.sys.platform", "win32"), patch.dict(os.environ, {"LOCALAPPDATA": ""}):
            self.assertEqual(default_directory(), Path.home() / "AppData/Local/berry-brain")
        with patch("berry_brain.local.sys.platform", "linux"), patch.dict(os.environ, {"XDG_DATA_HOME": str(self.root)}):
            self.assertEqual(default_directory(), self.root / "berry-brain")

    def test_registration_uses_persistent_absolute_paths(self):
        for client in ("codex", "claude"):
            with self.subTest(client=client), patch.object(sys, "argv", ["configure_client.py", client,
                    "--local", "--data-dir", str(self.root), "--project", "demo"]), \
                    patch("berry_brain.configure.subprocess.run") as run, \
                    patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.root.parent / "claude-config")}), \
                    patch("berry_brain.configure.global_instructions", return_value=self.root.parent / (client + ".md")):
                run.return_value.stdout = json.dumps({"tools": [{"name": "brain_recall"}]})
                configure_client.main()
                probe = run.call_args_list[0].args[0]
                if client == "claude":
                    config = json.loads((self.root.parent / "claude-config/.claude.json").read_text())
                    entry = config["mcpServers"]["berry-brain"]
                    adapter = [entry["command"], *entry["args"]]
                    self.assertEqual(run.call_count, 1)
                else:
                    registration = run.call_args_list[1].args[0]
                    adapter = registration[registration.index("--") + 1:]
                self.assertEqual(probe, [*adapter, "--call", "tools"])
                self.assertEqual(adapter[0], sys.executable)
                self.assertEqual(adapter[adapter.index("--identity") + 1], client)
                self.assertEqual(adapter[adapter.index("--data-dir") + 1], str(self.root))
                self.assertIn(configure_client.REMINDER, (self.root.parent / (client + ".md")).read_text())

    def test_remote_setup_requires_explicit_path_and_quotes_it(self):
        with self.assertRaises(ValueError):
            configure_client.command("/private/client.json", "my-server")
        command = configure_client.command("/private/client.json", "my-server",
            remote_adapter="/opt/project with spaces/brain_client.py")
        self.assertEqual(shlex.split(command[-1]), ["python3", "/opt/project with spaces/brain_client.py",
                                                   "--config", "/private/client.json"])
        self.assertFalse(any("/srv/" in part for part in command))


if __name__ == "__main__":
    unittest.main()
