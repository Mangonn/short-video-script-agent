"""Capture genuine model feedback paths, retaining only verified outputs."""

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUTS = ROOT / "examples/model_outputs"
TRACES = ROOT / "examples/model_traces"
CASES = ("normal", "overlong", "no_material", "tool_failure", "unsafe_request",
         "changed_material", "empty_corpus")


def verify(case: str, result: dict) -> None:
    if result["mode"] != "deepseek_chat":
        raise RuntimeError(f"{case}: not a real model run")
    trace = result["trace"]
    if case in ("normal", "overlong", "tool_failure", "unsafe_request", "changed_material"):
        if result["task_status"] != "complete":
            raise RuntimeError(f"{case}: expected a completed script")
        if result["estimated_total_seconds"] > result["target_duration_seconds"]:
            raise RuntimeError(f"{case}: final script exceeds limit")
        if result["material_source_ids"] != ["P01"] or not result["material_citations"]:
            raise RuntimeError(f"{case}: P01 source citation missing")
        if sum(scene["estimated_seconds"] for scene in result["scenes"]) != result["estimated_total_seconds"]:
            raise RuntimeError(f"{case}: scene timings do not sum to total")
        if "80%" in json.dumps(result["scenes"], ensure_ascii=False):
            raise RuntimeError(f"{case}: unsupported efficiency claim survived")
    if case == "overlong":
        estimates = [item for item in trace if item["action"] == "estimate_duration" and item["observation"].get("ok")]
        if (len(estimates) < 2 or estimates[0]["observation"]["within_target"]
                or not any(item["observation"]["within_target"] for item in estimates[1:])):
            raise RuntimeError("overlong: real model did not revise after an over-limit observation")
    if case == "tool_failure":
        failures = [index for index, item in enumerate(trace)
                    if item["action"] == "check_rules" and item["observation"].get("error") == "rule_checker_timeout"]
        if not failures or not any(item["action"] == "check_rules" and item["observation"].get("passed")
                                   for item in trace[failures[0] + 1:]):
            raise RuntimeError("tool_failure: no observed recovery from rule-check timeout")
    if case in ("no_material", "empty_corpus"):
        if result["task_status"] != "needs_material" or result["material_source_ids"]:
            raise RuntimeError(f"{case}: material gap was not reported")
    if case == "changed_material":
        if result["material_citations"][0]["quote"] != "支持配音":
            raise RuntimeError("changed_material: stale or invented fact")


parser = argparse.ArgumentParser()
parser.add_argument("--cases", nargs="+", choices=CASES, default=CASES)
selected = parser.parse_args().cases
OUTPUTS.mkdir(exist_ok=True)
TRACES.mkdir(exist_ok=True)
for case in selected:
    input_name = "overlong" if case == "empty_corpus" else case
    command = [sys.executable, "-m", "src.cli", f"examples/inputs/{input_name}.json",
               "--mode", "deepseek"]
    if case == "tool_failure":
        command += ["--fail-once", "check_rules"]
    if case == "changed_material":
        command += ["--corpus", "data/changed_materials.json"]
    if case == "empty_corpus":
        command += ["--corpus", "data/empty_materials.json"]
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    if not completed.stdout:
        raise RuntimeError(f"{case}: {completed.stderr}")
    result = json.loads(completed.stdout)
    verify(case, result)
    (OUTPUTS / f"deepseek_{case}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    (TRACES / f"deepseek_{case}.json").write_text(json.dumps(result["trace"], ensure_ascii=False, indent=2) + "\n")
    print(f"{case}: {result['task_status']} ({len(result['trace'])} steps)", flush=True)
