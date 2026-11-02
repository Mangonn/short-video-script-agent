"""CLI entry point. Run with python3 -m src.cli from the project root."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .agent import run
from .business import BusinessTools, preflight
from .models import DeepSeekModel, MockPolicy, ModelUnavailableError


def load_local_env() -> None:
    path = Path(".env")
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() in ("DEEPSEEK_API_KEY", "DEEPSEEK_MODEL"):
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a grounded short-video script within a duration limit")
    parser.add_argument("input", type=Path)
    parser.add_argument("--mode", choices=("mock", "deepseek"), default="mock")
    parser.add_argument("--corpus", type=Path, default=Path("data/materials.json"))
    parser.add_argument("--rules", type=Path, default=Path("data/brand_rules.json"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--fail-once", action="append", default=[],
                        choices=("search_materials", "estimate_duration", "check_rules"))
    parser.add_argument("--max-steps", type=int, default=12)
    parser.add_argument("--max-tools", type=int, default=10)
    args = parser.parse_args()
    try:
        request = json.loads(args.input.read_text(encoding="utf-8"))
        early = preflight(request, BusinessTools(corpus_path=args.corpus, rules_path=args.rules).rules)
        if early:
            result = early
        else:
            if args.mode == "deepseek":
                load_local_env()
            model = MockPolicy() if args.mode == "mock" else DeepSeekModel()
            result = run(request, model=model, corpus_path=args.corpus, rules_path=args.rules,
                         fail_once=set(args.fail_once), max_steps=args.max_steps,
                         max_tools=args.max_tools)
        content = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(content, encoding="utf-8")
        else:
            print(content, end="")
        return 0 if result["task_status"] in ("complete", "needs_input", "needs_material") else 2
    except ModelUnavailableError as exc:
        print(json.dumps({"task_status": "model_unavailable", "error": str(exc),
                          "hint": "Use --mode mock for offline workflow checks; Mock does not prove model decisions."},
                         ensure_ascii=False), file=sys.stderr)
        return 2
    except (ValueError, KeyError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"task_status": "invalid_input", "error": str(exc)},
                         ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
