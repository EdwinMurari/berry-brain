"""Check evaluation integrity without a model, credentials or production data."""

import json
import unittest
from unittest.mock import patch

from evaluate_brain import CASES, evaluate, run_case


class Gateway:
    model = "test/model"

    def check_capabilities(self, required):
        assert required == {"chat", "tools"}

    def complete(self, request):
        items = request["input"]
        task = items[0]["content"][0]["text"]
        case = next(case for case in CASES if case["task"] == task)
        if items[-1].get("type") == "function_call_output":
            assert items[-1]["output"] == next(iter(case["facts"].values()))
            name, args = "finish", {"action": case["expected"]}
        else:
            name, args = "inspect", {"field": next(iter(case["facts"]))}
        return {"output": [{"type": "function_call", "name": name, "arguments": json.dumps(args), "call_id": "call-1"}],
                "usage": {"input_tokens": 30, "output_tokens": 5}}


class EvaluationTests(unittest.TestCase):
    def test_paired_check_preserves_results_and_scores_no_gain_honestly(self):
        with patch("socket.socket", side_effect=AssertionError("network")):
            rows = list(evaluate(Gateway()))
        report = rows[-1]
        self.assertEqual(report["pairs"], 6)
        self.assertEqual(report["regressions"], [])
        for mode in ("without", "with"):
            self.assertEqual(report["summary"][mode]["correct"], 6)
            self.assertEqual(report["summary"][mode]["grounded"], 6)
            self.assertEqual(report["summary"][mode]["tool_calls"], 12)
            self.assertEqual(report["summary"][mode]["input_tokens"], 360)
        for row in rows[1:-1]:
            self.assertNotIn("feedback", row)
            for mode in ("without", "with"):
                request = row[mode]["trace"][0]["request"]
                self.assertNotIn('"expected"', json.dumps(request))
                self.assertEqual(len(request["input"]), 1 if mode == "without" else 2)
                self.assertEqual(row[mode]["trace"][1]["request"]["input"][-1]["call_id"], "call-1")

    def test_correct_guess_without_current_evidence_fails(self):
        gateway = Gateway()
        gateway.complete = lambda request: {"output": [{"type": "function_call", "name": "finish",
            "arguments": '{"action":"report_failure"}', "call_id": "guess"}], "usage": {"input_tokens": 1, "output_tokens": 1}}
        result = run_case(gateway, CASES[-1], None)
        self.assertEqual(result["action"], "report_failure")
        self.assertFalse(result["grounded"])
        self.assertFalse(result["correct"])

    def test_invalid_tool_identity_or_token_usage_cannot_pass(self):
        gateway = Gateway()
        valid = {"output": [{"type": "function_call", "name": "finish", "arguments": '{"action":"read_result"}'}],
                 "usage": {"input_tokens": 1, "output_tokens": 1}}
        gateway.complete = lambda request: valid
        result = run_case(gateway, CASES[0], None)
        self.assertFalse(result["correct"])
        valid["usage"]["input_tokens"] = True
        with self.assertRaises(ValueError):
            run_case(gateway, CASES[0], None)

    def test_loop_is_bounded_and_incomplete_choice_is_not_success(self):
        gateway = Gateway()
        gateway.complete = lambda request: {"output": [{"type": "function_call", "name": "inspect",
            "arguments": '{"field":"job"}', "call_id": "inspect"}], "usage": {"input_tokens": 1, "output_tokens": 1}}
        result = run_case(gateway, CASES[0], None)
        self.assertEqual(result["model_calls"], 4)
        self.assertFalse(result["correct"])
        self.assertIsNone(result["action"])

    def test_provider_failure_is_not_a_completed_comparison(self):
        gateway = Gateway()
        gateway.complete = lambda request: (_ for _ in ()).throw(RuntimeError("failed provider"))
        with self.assertRaises(RuntimeError):
            list(evaluate(gateway))


if __name__ == "__main__":
    unittest.main()
