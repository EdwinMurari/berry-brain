"""Ownership, provenance and retry regressions. No live model or storage writes."""

import copy
import hashlib
import importlib.util
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import server
from migrate_ownership import validate_plan


class FakeMemory:
    def __init__(self):
        self.rows = []
        self.fail_text = None

    def get_all(self, *, filters, top_k):
        def matches(row):
            return all(row.get(key, row["metadata"].get(key)) == value for key, value in filters.items())
        return {"results": [row for row in self.rows if matches(row)][:top_k]}

    def add(self, messages, *, user_id, agent_id, metadata, infer):
        assert infer is False
        text = messages[0]["content"]
        if self.fail_text == text:
            self.fail_text = None
            raise RuntimeError("simulated interruption before second write")
        row = {"id": f"fact-{len(self.rows)}", "memory": text, "event": "ADD",
               "user_id": user_id, "agent_id": agent_id,
               "attributed_to": metadata["attributed_to"],
               "metadata": {**metadata, "hash": hashlib.md5(text.encode(), usedforsecurity=False).hexdigest()}}
        self.rows.append(row)
        return {"results": [copy.deepcopy(row)]}


class OwnershipTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.receipts = server.IngestionStore(Path(self.tmp.name) / "receipts.db")
        self.backend = object.__new__(server.Mem0Backend)
        self.backend.mem = FakeMemory()
        self.backend.receipts = self.receipts
        self.backend.write_lock = threading.Lock()
        self.meta = {"room_id": "!work:test", "thread_id": "$thread", "profile_id": "sample-user"}
    def test_explicit_save_rejects_conflicting_owner_and_reserved_receipt(self):
        with patch.object(server.HEALTH_HTTP, "open", side_effect=AssertionError("generation request")):
            for changed in [{"profile_id": "second-user"}, {"profile_id": "agent-fast"},
                            {"scope": "room"}, {"attributed_to": "other"}]:
                with self.subTest(changed=changed), self.assertRaises(ValueError):
                    self.backend.add(user_id="profile:sample-user", app_id="berry-agents",
                                     messages=[{"role": "user", "content": "Example item"}],
                                     metadata={**self.meta, "scope": "profile", "explicit": True, **changed})
            with self.assertRaises(ValueError):
                self.backend.add(user_id="profile:sample-user", app_id="berry-agents",
                                 source_run_id="extract:run-1",
                                 messages=[{"role": "user", "content": "Example item"}],
                                 metadata={**self.meta, "scope": "profile", "explicit": True})
        self.assertEqual(self.backend.mem.rows, [])

    def test_explicit_save_recovers_after_lost_reply_without_model_extraction(self):
        operation = lambda: self.backend.add(
            user_id="profile:sample-user", app_id="berry-agents", source_run_id="fact-1",
            session_id="matrix:!work:test:$thread",
            messages=[{"role": "user", "content": "Example item was recommended to the sample user"}],
            metadata={**self.meta, "scope": "profile", "explicit": True, "attributed_to": "assistant"})
        with patch.object(server.HEALTH_HTTP, "open", side_effect=AssertionError("generation request")):
            first = self.receipts.execute("berry-agents", "fact-1", operation)
            retry = self.receipts.execute("berry-agents", "fact-1", operation)
            # Also cover a crash after the vector write but before receipt commit.
            recovered = operation()
        self.assertEqual(first, retry)
        self.assertEqual(first, recovered)
        self.assertEqual(len(self.backend.mem.rows), 1)
        self.assertEqual(first["results"][0]["attributed_to"], "assistant")

    def test_exact_text_dedup_does_not_merge_different_owners_or_sources(self):
        for owner, source in [("profile:sample-user", "user"), ("profile:second-user", "user"),
                              ("profile:sample-user", "assistant")]:
            self.backend._store_fact(owner, "berry-agents", "Example item", {"app_id": "berry-agents", "attributed_to": source})
        self.assertEqual(len(self.backend.mem.rows), 3)

    def test_mem0_promoted_source_is_preserved_and_validated_on_read(self):
        row = {"id": "fact-1", "memory": "Example item was recommended", "user_id": "profile:sample-user",
               "agent_id": "berry-agents", "attributed_to": "assistant",
               "metadata": {"app_id": "berry-agents"}}
        result = server._mem0_results({"results": [row]}, "berry-agents", user_id="profile:sample-user", require_owner=True)
        self.assertEqual(result[0]["attributed_to"], "assistant")
        with self.assertRaises(server.BackendContractError):
            server._normalized_mem0_result({**row, "attributed_to": "unknown"}, "berry-agents", 0)

    def test_migration_requires_reviewed_content_and_known_owner(self):
        ident = "29424a3f-485c-4c4f-a86d-ec5cfe73edb6"
        plan = [{"id": ident, "expected_owner": "room:!work:test", "new_owner": "profile:sample-user", "expected_hash": "same"}]
        payload = {"user_id": "room:!work:test", "scope": "room", "profile_id": "sample-user", "hash": "same", "agent_id": "berry-agents", "app_id": "berry-agents"}
        self.assertEqual(validate_plan(plan, [{"id": ident, "payload": payload}], ["sample-user"]), plan)
        self.assertEqual(validate_plan(plan, [{"id": ident, "payload": {**payload, "user_id": "profile:sample-user", "scope": "profile"}}], ["sample-user"]), [])
        for fields in [{"profile_id": "second-user"}, {"hash": "changed"}, {"app_id": "other"}, {"user_id": "room:!other:test"}]:
            with self.assertRaises(ValueError):
                validate_plan(plan, [{"id": ident, "payload": {**payload, **fields}}], ["sample-user"])


    def test_conversation_inputs_are_rejected_without_storage_or_generation(self):
        for explicit in [None, False, "true"]:
            with self.subTest(explicit=explicit), self.assertRaises(ValueError):
                self.backend.add(user_id="room:!work:test", app_id="berry-agents",
                                 source_run_id="old-turn", messages=[{"role":"user","content":"I own Calico"}],
                                 metadata={**self.meta, "explicit":explicit})
        with self.assertRaises(ValueError):
            self.backend.add(user_id="profile:sample-user", app_id="berry-agents",
                             messages=[{"role":"user","content":"one"},{"role":"assistant","content":"two"}],
                             metadata={**self.meta,"explicit":True,"scope":"profile"})
        self.assertEqual(self.backend.mem.rows, [])

    def test_raw_save_preserves_conditions_numbers_negation_and_unicode(self):
        samples = [
            "Use the sample adapter only for format version 3; it does not support version 2.",
            "The sample input does not include field A. It includes field B.",
            "Example café’s label costs EUR 12.50. ☕",
        ]
        for index, text in enumerate(samples):
            result = self.backend.add(user_id="profile:sample-user", app_id="berry-agents",
                                      source_run_id=f"fact-{index}",
                                      messages=[{"role":"user","content":text}],
                                      metadata={**self.meta,"explicit":True,"scope":"profile","attributed_to":"assistant"})
            self.assertEqual(result["results"][0]["memory"], text)
            self.assertEqual(result["results"][0]["owner_id"], "profile:sample-user")
            self.assertEqual(result["results"][0]["attributed_to"], "assistant")

    def test_generation_provider_fails_closed(self):
        with self.assertRaisesRegex(server.BackendContractError, "generation is disabled"):
            server.DisabledLlm().generate_response(messages=[])
        self.assertEqual(server._mem0_config()["llm"]["provider"], "berry_disabled")

    @unittest.skipUnless(importlib.util.find_spec("mem0"), "Mem0 contract runs in the Docker test stage")
    def test_registered_provider_uses_mem0_public_factory_and_config_contract(self):
        from mem0.configs.base import MemoryConfig
        from mem0.utils.factory import LlmFactory
        LlmFactory.register_provider("berry_disabled", "server.DisabledLlm")
        config = MemoryConfig(**server._mem0_config())
        generation = LlmFactory.create(config.llm.provider, config.llm.config)
        self.assertIsInstance(generation, server.DisabledLlm)
        with self.assertRaises(server.BackendContractError):
            generation.generate_response(messages=[])

    def test_receipts_survive_restart_without_repeating_a_confirmed_write(self):
        operation = lambda: self.backend.add(
            user_id="profile:sample-user", app_id="berry-agents",
            messages=[{"role":"user","content":"Example item was recommended to the sample user."}],
            metadata={**self.meta,"explicit":True,"scope":"profile","attributed_to":"assistant"})
        first = self.receipts.execute("berry-agents", "confirmed-fact", operation)
        restarted = server.IngestionStore(self.receipts.path)
        with patch.object(self.backend, "add", side_effect=AssertionError("repeated write")):
            self.assertEqual(restarted.execute("berry-agents", "confirmed-fact", operation), first)


if __name__ == "__main__":
    unittest.main()
