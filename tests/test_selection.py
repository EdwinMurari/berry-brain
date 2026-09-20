"""Selection must preserve storage rules and usable recall on failure."""
import json
import unittest
import test_engine as fixtures


class SelectionTests(unittest.TestCase):
    setUp = fixtures.BrainTests.setUp
    call = fixtures.BrainTests.call
    evidence = fixtures.BrainTests.evidence
    episode = fixtures.BrainTests.episode
    lesson = fixtures.BrainTests.lesson
    feedback = fixtures.BrainTests.feedback
    def active(self):
        lesson = self.lesson()
        self.feedback(lesson, "first")
        self.feedback(lesson, "second")
        return lesson

    def test_selection_runs_without_write_lock_and_only_final_lessons_get_receipts(self):
        lesson = self.active()
        def select(state, rows):
            self.assertEqual(state["current_context"], "Input already checked")
            with self.brain.db() as db:
                db.execute("PRAGMA busy_timeout=0")
                db.execute("BEGIN IMMEDIATE")
            return {row["id"]: "drop" for row in rows}
        self.brain.selector = select
        with self.brain.db() as db:
            before = db.execute("SELECT count(*) FROM receipts").fetchone()[0]
        result = self.call("recall", query="evidence", context="Input already checked")
        self.assertEqual(result["lessons"], [])
        self.assertEqual(result["selection"]["status"], "applied")
        with self.brain.db() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM receipts").fetchone()[0], before)
        self.assertEqual(self.call("trial", lesson_id=lesson["id"])["lesson"]["status"], "active")

    def test_disabled_errors_invalid_answers_and_uncertainty_keep_lessons(self):
        lesson = self.active()
        def broken(*args):
            raise RuntimeError("private provider error")
        for reply in (None, {}, {"invented": "drop"}, {lesson["id"]: "unknown"},
                      {lesson["id"]: "uncertain"}):
            self.brain.selector = lambda state, rows: reply
            result = self.call("recall", query="evidence")
            self.assertEqual([r["id"] for r in result["lessons"]], [lesson["id"]])
        self.brain.selector = broken
        result = self.call("recall", query="evidence")
        self.assertEqual(result["selection"]["status"], "unavailable")
        self.assertEqual(len(result["lessons"]), 1)
        self.assertNotIn("private provider", json.dumps(result))

    def test_retirement_during_selection_is_not_returned(self):
        lesson = self.active()
        def select(state, rows):
            current = self.call("trial", lesson_id=lesson["id"])["lesson"]
            self.call("revise", lesson_id=lesson["id"], expected_version=current["version"],
                      status="retired", reason="Current rules changed")
            return {row["id"]: "keep" for row in rows}
        self.brain.selector = select
        result = self.call("recall", query="evidence")
        self.assertEqual(result["lessons"], [])
        self.assertEqual(result["selection"]["status"], "state_changed")

    def test_empty_query_never_calls_selector(self):
        self.active()
        def unexpected(*args):
            self.fail("Empty query must make no selection call")
        self.brain.selector = unexpected
        self.assertEqual(self.call("recall", query="")["lessons"], [])

    def test_selector_cannot_see_another_room_or_inject_ids(self):
        allowed = self.active()
        foreign = dict(actor="chat-client", project="", room_id="!other")
        event = self.episode(**foreign)
        lesson = self.call("propose", **foreign, source_ids=[event["id"]],
                           lesson="Inspect evidence in another room", conditions="Missing evidence")
        for task in ("foreign-one", "foreign-two"):
            trial = self.call("trial", **foreign, task_id=task, lesson_id=lesson["id"])
            self.call("feedback", **foreign, task_id=task, lesson_id=lesson["id"],
                      receipt_id=trial["receipt_id"], outcome="helpful", comparison="Found input",
                      evidence=self.evidence(task, "artifact:" + task))
        seen = []
        def select(state, rows):
            seen.extend(rows)
            return {row["id"]: "keep" for row in rows}
        self.brain.selector = select
        result = self.call("recall", query="evidence")
        self.assertEqual({r["id"] for r in seen}, {allowed["id"]})
        self.assertEqual(len(result["lessons"]), 1)

    def test_changed_checkpoint_discards_old_selection(self):
        lesson = self.active()
        self.call("checkpoint", expected_version=0, goal="Check input", state="Unchecked", status="active")
        def select(state, rows):
            self.call("checkpoint", expected_version=1, goal="Check input", state="Checked", status="active")
            return {row["id"]: "drop" for row in rows}
        self.brain.selector = select
        result = self.call("recall", query="evidence")
        self.assertEqual(result["selection"]["status"], "state_changed")
        self.assertEqual(result["checkpoint"]["body"]["state"], "Checked")
        self.assertEqual(result["lessons"][0]["id"], lesson["id"])
