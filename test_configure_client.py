"""Check global reminder setup without touching real client settings."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import configure_client as setup


class InstructionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / "AGENTS.md"

    def test_new_install_and_repeat_leave_one_block(self):
        setup.install_instructions(self.path)
        first = self.path.read_bytes()
        modified = self.path.stat().st_mtime_ns
        setup.install_instructions(self.path)
        self.assertEqual(self.path.read_bytes(), first)
        self.assertEqual(self.path.stat().st_mtime_ns, modified)
        self.assertEqual(first.count(setup.BEGIN.encode()), 1)
        self.assertIn(setup.REMINDER.encode(), first)

    def test_preserves_existing_text_newlines_and_mode(self):
        original = "# User rules\r\n\r\nKeep café labels.\r\n".encode()
        self.path.write_bytes(original)
        self.path.chmod(0o640)
        mode = self.path.stat().st_mode
        setup.install_instructions(self.path)
        self.assertTrue(self.path.read_bytes().startswith(original))
        self.assertEqual(self.path.stat().st_mode, mode)
        self.assertNotIn(b"\n", self.path.read_bytes().replace(b"\r\n", b""))

    def test_updates_only_owned_section(self):
        prefix, suffix = "My rules.\n\n", "\n\nMore rules.\n"
        self.path.write_text(prefix + setup.BEGIN + "\nOld reminder.\n" + setup.END + suffix)
        setup.install_instructions(self.path)
        text = self.path.read_text()
        self.assertTrue(text.startswith(prefix))
        self.assertTrue(text.endswith(suffix))
        self.assertNotIn("Old reminder.", text)

    def test_refuses_broken_or_duplicate_markers(self):
        for text in (setup.BEGIN, setup.END, setup.END + setup.BEGIN,
                     setup.BEGIN + setup.END + setup.BEGIN + setup.END,
                     "Example: " + setup.BEGIN + "\nMy rules\n" + setup.END):
            with self.subTest(text=text):
                self.path.write_text(text)
                with self.assertRaises(ValueError):
                    setup.install_instructions(self.path)
                self.assertEqual(self.path.read_text(), text)

    @unittest.skipIf(os.name == "nt", "symlink creation can require Windows developer mode")
    def test_shared_symlink_and_claude_import_remain_intact(self):
        self.path.write_text("# Shared rules\n")
        link = self.root / "linked.md"
        link.symlink_to(self.path)
        wrapper = self.root / "CLAUDE.md"
        wrapper.write_text("@linked.md\n")
        setup.install_instructions(link)
        first = self.path.read_bytes()
        setup.install_instructions(wrapper, follow_import=True)
        self.assertTrue(link.is_symlink())
        self.assertEqual(wrapper.read_text(), "@linked.md\n")
        self.assertEqual(self.path.read_bytes(), first)

    def test_import_cycles_and_missing_targets_are_not_changed(self):
        other = self.root / "other.md"
        self.path.write_text("@other.md\n")
        other.write_text("@AGENTS.md\n")
        with self.assertRaises(ValueError):
            setup.install_instructions(self.path, follow_import=True)
        self.assertEqual(self.path.read_text(), "@other.md\n")
        other.unlink()
        with self.assertRaises(ValueError):
            setup.install_instructions(self.path, follow_import=True)
        self.assertFalse(other.exists())

    def test_custom_client_directories_and_codex_override(self):
        with patch.dict(os.environ, {"CODEX_HOME": str(self.root), "CLAUDE_CONFIG_DIR": str(self.root)}):
            self.assertEqual(setup.global_instructions("codex"), self.path)
            self.assertEqual(setup.global_instructions("claude"), self.root / "CLAUDE.md")
            override = self.root / "AGENTS.override.md"
            override.write_text("Override rules.\n")
            self.assertEqual(setup.global_instructions("codex"), override)
            override.write_text("")
            self.assertEqual(setup.global_instructions("codex"), self.path)

    def test_empty_config_variables_use_home_defaults(self):
        with patch.dict(os.environ, {"CODEX_HOME": "", "CLAUDE_CONFIG_DIR": ""}), \
                patch("configure_client.Path.home", return_value=self.root):
            self.assertEqual(setup.global_instructions("codex"), self.root / ".codex/AGENTS.md")
            self.assertEqual(setup.global_instructions("claude"), self.root / ".claude/CLAUDE.md")

    def test_claude_registration_repeats_and_preserves_other_settings(self):
        path = self.root / ".claude.json"
        original = {"theme": "dark", "mcpServers": {"other": {"command": "other-tool"}}, "custom": [1, 2]}
        path.write_text(json.dumps(original))
        with patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.root)}):
            setup.register_client("claude", [sys.executable, "adapter.py", "--local"])
            first = path.read_bytes()
            setup.register_client("claude", [sys.executable, "adapter.py", "--local"])
            self.assertEqual(path.read_bytes(), first)
            setup.register_client("claude", [sys.executable, "new-adapter.py", "--local"])
        result = json.loads(path.read_bytes())
        self.assertEqual(result["mcpServers"].pop("berry-brain")["args"], ["new-adapter.py", "--local"])
        self.assertEqual(result, original)

    def test_broken_instructions_fail_before_registration(self):
        self.path.write_text(setup.BEGIN)
        with patch.object(sys, "argv", ["configure_client.py", "codex", "--config", "client.json"]), \
                patch("configure_client.global_instructions", return_value=self.path), \
                patch("configure_client.subprocess.run") as run:
            with self.assertRaises(ValueError):
                setup.main()
            run.assert_not_called()

    def test_failed_atomic_replace_preserves_original(self):
        self.path.write_text("My rules.\n")
        with patch("configure_client.os.replace", side_effect=OSError("write failed")):
            with self.assertRaises(OSError):
                setup.install_instructions(self.path)
        self.assertEqual(self.path.read_text(), "My rules.\n")
        self.assertEqual(list(self.root.glob(".berry-brain-*")), [])

    def test_cli_installs_reminder_after_successful_registration(self):
        args = ["configure_client.py", "codex", "--config", str(self.root / "client.json")]
        for failure_at in (0, 1):
            with self.subTest(failure_at=failure_at), patch.object(sys, "argv", args), \
                    patch("configure_client.subprocess.run") as run, \
                    patch("configure_client.global_instructions", return_value=self.path):
                ok = subprocess.CompletedProcess([], 0, stdout=json.dumps({"tools": [{}]}))
                run.side_effect = [ok] * failure_at + [subprocess.CalledProcessError(1, "probe or register")]
                with self.assertRaises(subprocess.CalledProcessError):
                    setup.main()
                self.assertFalse(self.path.exists())
        with patch.object(sys, "argv", args), \
                patch("configure_client.subprocess.run") as run, \
                patch("configure_client.global_instructions", return_value=self.path):
            run.return_value.stdout = json.dumps({"tools": [{}]})
            setup.main()
            setup.main()
            self.assertEqual(self.path.read_text().count(setup.BEGIN), 1)


if __name__ == "__main__":
    unittest.main()
