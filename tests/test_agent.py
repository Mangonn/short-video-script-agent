import io
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from src.agent import run
from src.business import BusinessTools
from src.models import DeepSeekModel, MockPolicy


ROOT = Path(__file__).resolve().parents[1]


class ScriptAcceptanceTests(unittest.TestCase):
    def request(self, name):
        return json.loads((ROOT / "examples/inputs" / f"{name}.json").read_text())

    def case(self, name, **options):
        return run(self.request(name), model=MockPolicy(), **options)

    def test_normal_script_has_cited_fact_and_scene_timing(self):
        result = self.case("normal")
        self.assertEqual(result["task_status"], "complete")
        self.assertEqual(result["estimated_total_seconds"], 38)
        self.assertEqual(result["material_source_ids"], ["P01"])
        self.assertEqual(result["material_citations"], [
            {"material_id": "P01", "fact_id": "F01", "quote": "支持字幕"}])
        self.assertEqual(sum(scene["estimated_seconds"] for scene in result["scenes"]), 38)
        self.assertEqual(result["scenes"][-1]["end_seconds"], 38)
        self.assertNotIn("80%", json.dumps(result["scenes"], ensure_ascii=False))

    def test_overlong_draft_is_revised_and_rechecked(self):
        result = self.case("overlong")
        self.assertEqual(result["task_status"], "complete")
        estimates = [step for step in result["trace"] if step["action"] == "estimate_duration"]
        self.assertEqual([step["observation"]["estimated_total_seconds"] for step in estimates], [38, 28])
        self.assertFalse(estimates[0]["observation"]["within_target"])
        self.assertTrue(estimates[1]["observation"]["within_target"])
        self.assertEqual(estimates[0]["next_action"], "estimate_duration")
        self.assertEqual(result["tool_results"]["check_rules"][result["script_id"]]["passed"], True)
        self.assertEqual(result["estimated_total_seconds"], sum(x["estimated_seconds"] for x in result["scenes"]))

    def test_missing_required_fields_stop_before_tools(self):
        for name, field in (("missing_audience", "audience"), ("missing_duration", "target_duration_seconds")):
            with self.subTest(name=name):
                result = self.case(name)
                self.assertEqual(result["task_status"], "needs_input")
                self.assertIn(field, result["missing_fields"])
                self.assertEqual(result["steps"], [])

    def test_no_material_rewrites_query_then_requests_source(self):
        result = self.case("no_material")
        self.assertEqual(result["task_status"], "needs_material")
        self.assertEqual([step["action"] for step in result["trace"]],
                         ["search_materials", "search_materials", "finish"])
        self.assertNotEqual(result["trace"][0]["arguments"]["query"],
                            result["trace"][1]["arguments"]["query"])
        self.assertEqual(result["material_source_ids"], [])

    def test_removing_retrieval_result_changes_next_action(self):
        result = self.case("overlong", corpus_path=ROOT / "data/empty_materials.json")
        self.assertEqual(result["task_status"], "needs_material")
        self.assertFalse(any(step["action"] == "estimate_duration" for step in result["trace"]))

    def test_replacing_source_fact_changes_script_and_quote(self):
        result = self.case("changed_material", corpus_path=ROOT / "data/changed_materials.json")
        self.assertEqual(result["task_status"], "complete")
        self.assertEqual(result["material_citations"][0]["quote"], "支持配音")
        self.assertIn("支持配音", json.dumps(result["scenes"], ensure_ascii=False))
        self.assertNotIn("支持字幕", json.dumps(result["scenes"], ensure_ascii=False))

    def test_search_failure_is_not_recorded_as_success(self):
        result = self.case("normal", fail_once={"search_materials"})
        self.assertEqual(result["task_status"], "complete")
        self.assertEqual(result["steps"][0]["status"], "failed")
        self.assertEqual(result["steps"][0]["tool_result"]["error"], "material_search_timeout")
        self.assertEqual(result["trace"][0]["next_action"], "search_materials")

    def test_duration_tool_failure_retries_before_completion(self):
        result = self.case("normal", fail_once={"estimate_duration"})
        self.assertEqual(result["task_status"], "complete")
        self.assertEqual(result["steps"][1]["status"], "failed")
        self.assertEqual(result["trace"][1]["next_action"], "estimate_duration")

    def test_rule_tool_failure_retries_before_completion(self):
        result = self.case("tool_failure", fail_once={"check_rules"})
        self.assertEqual(result["task_status"], "complete")
        checks = [step for step in result["steps"] if step["action"] == "check_rules"]
        self.assertEqual([step["status"] for step in checks], ["failed", "succeeded"])
        self.assertEqual(checks[0]["tool_result"]["error"], "rule_checker_timeout")

    def test_unsupported_eighty_percent_claim_is_removed_after_rule_check(self):
        result = self.case("unsafe_request")
        self.assertEqual(result["task_status"], "complete")
        checks = [step["tool_result"] for step in result["steps"] if step["action"] == "check_rules"]
        self.assertFalse(checks[0]["passed"])
        self.assertIn("unsupported_quantitative_claim", [risk["code"] for risk in checks[0]["risks"]])
        self.assertTrue(checks[-1]["passed"])
        self.assertNotIn("80%", json.dumps(result["scenes"], ensure_ascii=False))

    def test_estimator_does_not_trust_planned_seconds(self):
        request = self.request("normal")
        tools = BusinessTools()
        state = {"drafts": {}, "estimates": {}, "latest_script_id": None}
        script = MockPolicy._script(request, tools.materials[0], "micro")
        script["scenes"][0]["planned_seconds"] = 1
        script["scenes"][0]["narration"] = "支持字幕" * 15
        estimate = tools.estimate_duration(script, request, state)
        self.assertGreater(estimate["estimated_total_seconds"], 1)
        self.assertEqual(estimate["per_scene"][0]["estimated_seconds"], 20)

    def test_unretrieved_material_and_wrong_quote_are_rejected(self):
        request = self.request("normal")
        tools = BusinessTools()
        state = {"searches": [], "drafts": {}, "estimates": {}, "checks": {}, "latest_script_id": None}
        script = MockPolicy._script(request, tools.materials[0], "compact")
        estimate = tools.estimate_duration(script, request, state)
        check = tools.check_rules(estimate["script_id"], state)
        self.assertIn("material_not_retrieved", [x["code"] for x in check["risks"]])
        state["searches"].append(tools.search_materials(request["topic"]))
        script["scenes"][1]["source_refs"][0]["quote"] = "支持配音"
        estimate2 = tools.estimate_duration(script, request, state)
        check2 = tools.check_rules(estimate2["script_id"], state)
        self.assertIn("unsupported_fact_reference", [x["code"] for x in check2["risks"]])

    def test_estimated_draft_is_frozen_against_later_mutation(self):
        request = self.request("normal")
        tools = BusinessTools()
        state = {"searches": [tools.search_materials(request["topic"])], "drafts": {},
                 "estimates": {}, "checks": {}, "latest_script_id": None}
        script = MockPolicy._script(request, tools.materials[0], "compact")
        estimate = tools.estimate_duration(script, request, state)
        script["scenes"][1]["narration"] = "效率提升80%。"
        self.assertNotIn("80%", json.dumps(state["drafts"][estimate["script_id"]], ensure_ascii=False))
        state["drafts"][estimate["script_id"]]["scenes"][1]["narration"] = "效率提升80%。"
        self.assertEqual(tools.check_rules(estimate["script_id"], state)["error"], "draft_integrity_error")

    def test_fabricated_metric_cannot_be_finished(self):
        request = self.request("normal")
        tools = BusinessTools()
        state = {"searches": [tools.search_materials(request["topic"])], "drafts": {},
                 "estimates": {}, "checks": {}, "latest_script_id": None}
        script = MockPolicy._script(request, tools.materials[0], "compact")
        script["scenes"][1]["narration"] += "效率提升80%。"
        estimate = tools.estimate_duration(script, request, state)
        check = tools.check_rules(estimate["script_id"], state)
        self.assertFalse(check["passed"])
        rejected = tools.finish("complete", estimate["script_id"], request, state)
        self.assertEqual(rejected["error"], "rules_not_passed")

    def test_unsubstantiated_ease_and_ui_details_are_rejected(self):
        request = self.request("normal")
        tools = BusinessTools()
        state = {"searches": [tools.search_materials(request["topic"])], "drafts": {},
                 "estimates": {}, "checks": {}, "latest_script_id": None}
        script = MockPolicy._script(request, tools.materials[0], "compact")
        script["scenes"][1]["visual"] = "点击字幕轨道"
        script["scenes"][1]["narration"] += "新手也能上手。"
        estimate = tools.estimate_duration(script, request, state)
        check = tools.check_rules(estimate["script_id"], state)
        self.assertFalse(check["passed"])
        self.assertIn("unsupported_feature_claim", [risk["code"] for risk in check["risks"]])
        self.assertEqual(tools.finish("complete", estimate["script_id"], request, state)["error"],
                         "rules_not_passed")

    def test_finish_requires_both_checks_for_same_script(self):
        request = self.request("normal")
        tools = BusinessTools()
        state = {"searches": [tools.search_materials(request["topic"])], "drafts": {},
                 "estimates": {}, "checks": {}, "latest_script_id": None}
        script = MockPolicy._script(request, tools.materials[0], "compact")
        estimate = tools.estimate_duration(script, request, state)
        rejected = tools.finish("complete", estimate["script_id"], request, state)
        self.assertEqual(rejected["error"], "rule_check_required")

    def test_step_and_tool_limits_are_explicit(self):
        for options, expected in (({"max_steps": 2}, "step_limit"), ({"max_tools": 2}, "tool_limit")):
            with self.subTest(expected=expected):
                result = self.case("overlong", **options)
                self.assertEqual(result["task_status"], "incomplete")
                self.assertEqual(result["incomplete_reason"], expected)

    def test_platform_duration_boundary(self):
        request = self.request("normal")
        request["target_duration_seconds"] = 61
        result = run(request, model=MockPolicy())
        self.assertEqual(result["incomplete_reason"], "target_exceeds_platform_maximum")
        self.assertEqual(result["steps"], [])

    def test_deepseek_adapter_returns_tool_observation(self):
        responses = [
            {"choices": [{"message": {"content": None, "tool_calls": [
                {"id": "one", "type": "function", "function": {"name": "search_materials",
                 "arguments": '{"query":"星剪字幕","reason":"search"}'}}]}}]},
            {"choices": [{"message": {"content": None, "tool_calls": [
                {"id": "two", "type": "function", "function": {"name": "finish",
                 "arguments": '{"status":"needs_material","reason":"stop"}'}}]}}]},
        ]
        payloads = []

        def fake_urlopen(request, timeout):
            payloads.append(json.loads(request.data))
            return io.BytesIO(json.dumps(responses[len(payloads) - 1]).encode())

        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-only"}), patch(
            "urllib.request.urlopen", side_effect=fake_urlopen
        ):
            model = DeepSeekModel()
            first = model.decide(self.request("normal"), {})
            model.observe({"ok": True, "materials": []})
            second = model.decide(self.request("normal"), {})
        self.assertEqual(first["name"], "search_materials")
        self.assertEqual(second["name"], "finish")
        self.assertEqual(payloads[1]["messages"][-1]["role"], "tool")

    def test_parallel_model_calls_are_rejected_without_business_calls(self):
        response = {"choices": [{"message": {"content": None, "tool_calls": [
            {"id": "one", "type": "function", "function": {"name": "search_materials",
             "arguments": '{"query":"星剪字幕","reason":"search"}'}},
            {"id": "two", "type": "function", "function": {"name": "check_rules",
             "arguments": '{"script_id":"fake","reason":"check"}'}}
        ]}}]}
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-only"}), patch(
            "urllib.request.urlopen", return_value=io.BytesIO(json.dumps(response).encode())
        ):
            result = run(self.request("normal"), model=DeepSeekModel(), max_steps=1)
        self.assertEqual(result["tool_call_count"], 0)
        self.assertEqual(result["trace"][0]["action"], "parallel_calls_rejected")

    def test_cli_missing_key_has_no_silent_mock_fallback(self):
        env = os.environ.copy()
        env["DEEPSEEK_API_KEY"] = ""
        completed = subprocess.run(
            [sys.executable, "-m", "src.cli", "examples/inputs/normal.json", "--mode", "deepseek"],
            cwd=ROOT, env=env, capture_output=True, text=True)
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(json.loads(completed.stderr)["task_status"], "model_unavailable")


if __name__ == "__main__":
    unittest.main()
