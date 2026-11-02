"""Regenerate offline CLI outputs and full traces from synthetic cases."""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUTS = ROOT / "examples/outputs"
TRACES = ROOT / "examples/traces"
CASES = {
    "normal": "complete", "overlong": "complete", "missing_audience": "needs_input",
    "missing_duration": "needs_input", "no_material": "needs_material",
    "tool_failure": "complete", "unsafe_request": "complete",
    "changed_material": "complete", "empty_corpus": "needs_material",
    "search_failure": "complete", "estimate_failure": "complete",
    "step_limit": "incomplete", "tool_limit": "incomplete", "unsafe_blocked": "incomplete",
}


def invoke(case: str) -> dict:
    input_name = ("normal" if case in ("search_failure", "estimate_failure")
                  else "overlong" if case in ("empty_corpus", "step_limit", "tool_limit")
                  else "unsafe_request" if case == "unsafe_blocked" else case)
    command = [sys.executable, "-m", "src.cli", f"examples/inputs/{input_name}.json",
               "--mode", "mock"]
    if case == "changed_material":
        command += ["--corpus", "data/changed_materials.json"]
    if case == "empty_corpus":
        command += ["--corpus", "data/empty_materials.json"]
    if case == "tool_failure":
        command += ["--fail-once", "check_rules"]
    if case == "search_failure":
        command += ["--fail-once", "search_materials"]
    if case == "estimate_failure":
        command += ["--fail-once", "estimate_duration"]
    if case == "step_limit":
        command += ["--max-steps", "2"]
    if case == "tool_limit":
        command += ["--max-tools", "2"]
    if case == "unsafe_blocked":
        command += ["--max-steps", "3"]
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    if not completed.stdout:
        raise RuntimeError(f"{case}: {completed.stderr}")
    return json.loads(completed.stdout)


results = {case: invoke(case) for case in CASES}
for case, result in results.items():
    if result["task_status"] != CASES[case] or result["mode"] not in ("mock_policy", "preflight"):
        raise RuntimeError(f"{case}: unexpected result; artifacts were not replaced")
OUTPUTS.mkdir(exist_ok=True)
TRACES.mkdir(exist_ok=True)
for case, result in results.items():
    (OUTPUTS / f"{case}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    (TRACES / f"{case}.json").write_text(json.dumps(result["trace"], ensure_ascii=False, indent=2) + "\n")
print(f"Generated {len(results)} offline CLI outputs and traces")
