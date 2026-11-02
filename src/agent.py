"""Bounded model-action / tool-observation loop for grounded video scripts."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from .business import BusinessTools, preflight


def _record(state: dict, action: str, arguments: dict, observation: dict,
            reason: str, actor: str, model_turn: int | None = None) -> None:
    item = {"step": len(state["trace"]) + 1, "actor": actor, "action": action,
            "arguments": arguments, "observation": observation,
            "decision_basis": reason}
    if model_turn is not None:
        item["model_turn"] = model_turn
    state["trace"].append(item)
    state["steps"].append({"step": item["step"], "action": action,
                           "status": "succeeded" if observation.get("ok") else "failed",
                           "tool_result": observation})


def _result(request: dict, state: dict, mode: str,
            outcome: dict | None = None, forced_reason: str | None = None) -> dict:
    trace = deepcopy(state["trace"])
    for index, item in enumerate(trace):
        item["next_action"] = trace[index + 1]["action"] if index + 1 < len(trace) else None
    result = {"topic": request["topic"], "audience": request["audience"],
              "platform": request["platform"], "style": request["style"],
              "target_duration_seconds": request["target_duration_seconds"],
              "task_status": "incomplete", "incomplete_reason": forced_reason or "not_finished",
              "title": None, "scenes": [], "estimated_total_seconds": None,
              "material_source_ids": [], "material_citations": [],
              "material_gaps": [], "risk_reasons": state["tools"]._current_risks(state),
              "tool_results": {"search_materials": state["searches"],
                               "estimate_duration": state["estimates"],
                               "check_rules": state["checks"]},
              "draft_candidates": state["drafts"], "latest_script_id": state["latest_script_id"],
              "tool_call_count": state["tool_call_count"], "mode": mode,
              "corpus_revision": state["tools"].revision,
              "steps": state["steps"], "trace": trace}
    if outcome:
        for key in ("task_status", "incomplete_reason", "title", "scenes",
                    "estimated_total_seconds", "material_source_ids", "material_citations",
                    "material_gaps", "risk_reasons", "script_id"):
            if key in outcome:
                result[key] = outcome[key]
    return result


def run(request: dict, *, model, corpus_path: Path | None = None,
        rules_path: Path | None = None, fail_once: set[str] | None = None,
        max_steps: int = 12, max_tools: int = 10) -> dict:
    tools = BusinessTools(corpus_path=corpus_path, rules_path=rules_path, fail_once=fail_once)
    early = preflight(request, tools.rules)
    if early:
        return early
    if max_steps < 1 or max_tools < 1:
        raise ValueError("max_steps and max_tools must be positive")
    state = {"tools": tools, "searches": [], "drafts": {}, "estimates": {}, "checks": {},
             "latest_script_id": None, "last_tool_error": None,
             "tool_call_count": 0, "steps": [], "trace": []}
    for _ in range(max_steps):
        try:
            decision = model.decide(request, state)
        except Exception as exc:
            _record(state, "model_error", {}, {"ok": False, "error": str(exc)},
                    "模型不可用或响应格式异常", model.name)
            return _result(request, state, model.name, forced_reason="model_error")
        action = decision.get("name")
        arguments = decision.get("arguments", {})
        reason = str(decision.get("reason", ""))[:200]
        if not isinstance(arguments, dict):
            _record(state, "invalid_model_arguments", {},
                    {"ok": False, "error": "invalid_model_arguments"}, reason,
                    model.name, decision.get("model_turn"))
            return _result(request, state, model.name, forced_reason="invalid_model_arguments")
        clean_arguments = {key: value for key, value in arguments.items() if key != "reason"}
        if action == "parallel_calls_rejected":
            observation = {"ok": False, "error": "parallel_calls_rejected"}
        elif action == "finish":
            observation = tools.finish(arguments.get("status"), arguments.get("script_id"), request, state)
        elif state["tool_call_count"] >= max_tools:
            _record(state, "tool_limit", {}, {"ok": False, "error": "tool_limit"},
                    "业务工具调用次数已到上限", "system")
            return _result(request, state, model.name, forced_reason="tool_limit")
        elif action == "search_materials" and isinstance(arguments.get("query"), str):
            state["tool_call_count"] += 1
            observation = tools.search_materials(arguments["query"])
            state["searches"].append(observation)
        elif action == "estimate_duration" and isinstance(arguments.get("script"), dict):
            state["tool_call_count"] += 1
            observation = tools.estimate_duration(arguments["script"], request, state)
        elif action == "check_rules" and isinstance(arguments.get("script_id"), str):
            state["tool_call_count"] += 1
            observation = tools.check_rules(arguments["script_id"], state)
        else:
            observation = {"ok": False, "error": "unknown_tool_or_arguments"}
        if action in ("search_materials", "estimate_duration", "check_rules"):
            state["last_tool_error"] = None if observation.get("ok") else observation.get("error")
        _record(state, action, clean_arguments, observation, reason, model.name,
                decision.get("model_turn"))
        if action == "finish" and observation.get("accepted"):
            return _result(request, state, model.name, outcome=observation)
        model.observe(observation)
    return _result(request, state, model.name, forced_reason="step_limit")
