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


class Judge:
    """Synthetic selector: fixed recall choices and feedback advice by lesson ID."""
    def __init__(self, choices=None, advice=None):
        self.choices, self.advice, self.seen = choices or {}, advice or {}, []

    def __call__(self, state, lessons):
        self.seen.append(("select", state, lessons))
        return {lesson["id"]: self.choices.get(lesson["id"], "keep") for lesson in lessons}

    def suggest_feedback(self, experience, lessons):
        self.seen.append(("advise", experience, lessons))
        if isinstance(self.advice, Exception):
            raise self.advice
        return {lesson["id"]: self.advice.get(lesson["id"], "unclear") for lesson in lessons}


class CandidateLoopTests(unittest.TestCase):
    setUp = fixtures.BrainTests.setUp
    call = fixtures.BrainTests.call
    evidence = fixtures.BrainTests.evidence
    episode = fixtures.BrainTests.episode
    lesson = fixtures.BrainTests.lesson

    def second_candidate(self):
        event = self.call("record", event_id="e2", problem="evidence omitted again", action="inspect stored evidence",
                          result="stored evidence was present", outcome="success",
                          evidence=self.evidence("second source output", "artifact:source-2"))
        return self.call("propose", lesson="Compare stored evidence with delivered evidence",
                         conditions="When model answers omit stored evidence", source_ids=[event["id"]])

    def use_candidate(self, task, outcome="helpful", advice="helpful"):
        """Normal work: recall, save the result, then confirm the advised report."""
        recall = self.call("recall", task_id=task, query="delivered evidence")
        [shown] = recall["candidates"]
        self.brain.selector.advice = {shown["id"]: advice}
        saved = self.call("record", task_id=task, event_id="work-" + task, problem="answer omitted evidence",
                          action="applied the recalled check", result="required source now present",
                          outcome="success", evidence=self.evidence("run output for " + task, "artifact:" + task))
        [due] = saved["feedback_due"]
        self.assertEqual((due["lesson_id"], due["receipt_id"]), (shown["id"], shown["receipt_id"]))
        self.assertEqual(due["suggested_outcome"], advice)
        return self.call("feedback", task_id=task, receipt_id=due["receipt_id"], lesson_id=due["lesson_id"],
                         outcome=outcome, evidence=saved["body"]["evidence"],
                         comparison="Before: evidence missing; after: required source present")

    def test_normal_work_tests_and_promotes_a_candidate(self):
        lesson = self.lesson()
        self.brain.selector = Judge()
        self.assertEqual(self.use_candidate("fresh-1")["lesson"]["status"], "candidate")
        self.assertEqual(self.use_candidate("fresh-2")["lesson"]["status"], "active")
        recall = self.call("recall", task_id="fresh-3", query="delivered evidence")
        self.assertEqual([r["id"] for r in recall["lessons"]], [lesson["id"]])
        self.assertEqual(recall["candidates"], [])

    def test_agent_report_overrides_advice_and_harm_retires(self):
        self.lesson()
        self.brain.selector = Judge()
        result = self.use_candidate("fresh-1", outcome="harmful", advice="neutral")
        self.assertEqual(result["lesson"]["status"], "retired")
        self.assertEqual(self.call("recall", task_id="fresh-2", query="delivered evidence")["candidates"], [])

    def test_only_a_keep_shows_one_candidate(self):
        first, second = self.lesson(), self.second_candidate()
        for choices, expected in (({first["id"]: "uncertain", second["id"]: "drop"}, []),
                                  ({first["id"]: "drop"}, [second["id"]]),
                                  ({}, None)):
            self.brain.selector = Judge(choices)
            shown = [r["id"] for r in self.call("recall", task_id="fresh", query="evidence")["candidates"]]
            if expected is None:
                self.assertEqual(len(shown), 1)
            else:
                self.assertEqual(shown, expected)
        for selector in (None, lambda state, rows: None, lambda state, rows: {}):
            self.brain.selector = selector
            self.assertEqual(self.call("recall", task_id="fresh", query="evidence")["candidates"], [])

    def test_lesson_source_task_is_never_offered_its_own_candidate(self):
        self.lesson()
        self.brain.selector = Judge()
        self.assertEqual(self.call("recall", task_id="origin", query="evidence")["candidates"], [])
        self.assertEqual(len(self.call("recall", task_id="fresh", query="evidence")["candidates"]), 1)

    def test_advice_failure_keeps_due_list_and_saves_nothing_extra(self):
        self.lesson()
        self.brain.selector = Judge()
        self.call("recall", task_id="fresh", query="evidence")
        self.brain.selector.advice = RuntimeError("private provider error")
        arguments = dict(task_id="fresh", event_id="work", problem="p", action="a", result="r",
                         outcome="inconclusive", evidence=self.evidence("fresh output", "artifact:fresh"))
        saved = self.call("record", **arguments)
        self.assertEqual(saved["feedback_advice"], {"status": "unavailable"})
        self.assertNotIn("suggested_outcome", saved["feedback_due"][0])
        self.assertNotIn("private provider", json.dumps(saved))
        self.brain.selector.advice = {}
        retry = self.call("record", **arguments)
        self.assertEqual(retry["id"], saved["id"])
        self.assertEqual(retry["feedback_advice"], {"status": "applied"})
        self.assertNotIn("suggested_outcome", retry["feedback_due"][0])
        with self.brain.db() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM feedback").fetchone()[0], 0)

    def test_reported_lessons_leave_the_due_list_and_complete_checkpoint_lists_the_rest(self):
        self.lesson()
        self.brain.selector = Judge()
        self.use_candidate("fresh-1")
        saved = self.call("record", task_id="fresh-1", event_id="later", problem="p", action="a", result="r",
                          outcome="success", evidence=self.evidence("later output", "artifact:later"))
        self.assertEqual(saved["feedback_due"], [])
        self.assertEqual(self.call("recall", task_id="fresh-1", query="evidence")["candidates"], [])
        self.call("recall", task_id="fresh-2", query="evidence")
        self.brain.selector = None
        checkpoint = self.call("checkpoint", task_id="fresh-2", expected_version=0, goal="g", state="s",
                               status="complete")
        self.assertEqual(len(checkpoint["feedback_due"]), 1)
        active = self.call("checkpoint", task_id="fresh-3", expected_version=0, goal="g", state="s", status="active")
        self.assertNotIn("feedback_due", active)

    def test_due_list_is_private_to_the_client_and_task(self):
        self.lesson()
        self.brain.selector = Judge()
        self.call("recall", task_id="fresh", query="evidence")
        for actor, task in (("claude", "fresh"), ("codex", "other")):
            saved = self.call("record", actor=actor, task_id=task, event_id="x-" + actor, problem="p", action="a",
                              result="r", outcome="success", evidence=self.evidence(actor + task, "artifact:" + actor))
            self.assertEqual(saved["feedback_due"], [])
