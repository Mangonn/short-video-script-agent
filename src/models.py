"""DeepSeek function-call decisions and an explicitly non-model offline policy."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


class ModelUnavailableError(RuntimeError):
    pass


SCENE_SCHEMA = {"type": "object", "properties": {
    "scene_id": {"type": "string"}, "visual": {"type": "string"},
    "narration": {"type": "string"}, "subtitle": {"type": "string"},
    "planned_seconds": {"type": "integer"},
    "source_refs": {"type": "array", "items": {"type": "object", "properties": {
        "material_id": {"type": "string"}, "fact_id": {"type": "string"},
        "quote": {"type": "string"}},
        "required": ["material_id", "fact_id", "quote"], "additionalProperties": False}}},
    "required": ["scene_id", "visual", "narration", "subtitle", "planned_seconds", "source_refs"],
    "additionalProperties": False}
SCRIPT_SCHEMA = {"type": "object", "properties": {
    "title": {"type": "string"}, "scenes": {"type": "array", "items": SCENE_SCHEMA}},
    "required": ["title", "scenes"], "additionalProperties": False}


def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {"name": name, "description": description,
            "parameters": {"type": "object", "properties": {
                **properties, "reason": {"type": "string", "description": "Brief factual decision basis, not hidden reasoning."}},
                "required": [*required, "reason"], "additionalProperties": False}}}


TOOLS = [
    _tool("search_materials", "Search synthetic product materials and return exact approved facts with IDs.",
          {"query": {"type": "string"}}, ["query"]),
    _tool("estimate_duration", "Submit a complete editable script; tool estimates each scene and total time independently of planned seconds.",
          {"script": SCRIPT_SCHEMA}, ["script"]),
    _tool("check_rules", "Check the exact estimated script against source facts and brand rules.",
          {"script_id": {"type": "string"}}, ["script_id"]),
    _tool("finish", "Finish after both duration and rules pass for the same script, or report missing material / inability to complete.",
          {"status": {"type": "string", "enum": ["complete", "needs_material", "blocked"]},
           "script_id": {"type": "string"}}, ["status"]),
]


INSTRUCTIONS = """You are a short-video script agent. Choose exactly ONE function call per response.
Use the user's topic, audience, platform, target_duration_seconds, and style, but brand and evidence rules take precedence over unsupported style requests.
Search materials first. If no material is found, choose a better query or finish needs_material; never invent product facts.
Only use product facts returned by search. Copy exact material_id, fact_id, quote into source_refs for the scene that states the fact; put the exact quote in narration or subtitle.
Do not infer ease of use, speed, specific UI controls/areas, or a performance benefit from a generic 'supports feature' fact. Use a generic visual description of a synthetic product feature page unless the source explicitly supports a UI detail.
P01's only approved fact is '支持字幕' when that is what the tool returns. Never add efficiency gains, percentages, automatic features, or other product claims without source facts.
Build a three-scene first draft (hook, feature demonstration, closing prompt); a complete first draft often uses 12, 14, and 12 planned seconds. Submit it to estimate_duration.
The estimator may report a total above the user's limit even if planned times seem short. When over limit, shorten or restructure the script and call estimate_duration again.
Call check_rules on the SAME script_id returned by a passing duration estimate. If the checker fails or times out, observe the result and revise/retry as appropriate.
Finish complete only after both checks pass for the same script_id. The program will reject an unsupported finish and explain why.
If a tool persistently fails or the target cannot be met, finish blocked. Put only a short factual basis in reason. Never reveal hidden chain of thought.
"""


class MockPolicy:
    """Observation-driven offline workflow check, not a language model."""

    name = "mock_policy"

    @staticmethod
    def _script(request: dict, material: dict, variant: str) -> dict:
        fact = material["facts"][0]
        product = material["product_name"]
        reference = {"material_id": material["material_id"], "fact_id": fact["fact_id"],
                     "quote": fact["text"]}
        if variant == "micro":
            return {"title": f"{product}功能介绍", "scenes": [
                {"scene_id": "S1", "visual": "展示产品功能页面", "narration": fact["text"] + "。",
                 "subtitle": fact["text"], "planned_seconds": 3, "source_refs": [reference]}]}
        timings = (12, 14, 12) if variant == "detailed" else (8, 10, 10)
        narration = f"{product}{fact['text']}。"
        if variant == "detailed" and "80%" in request["style"].replace(" ", ""):
            narration += "效率提升80%。"
        return {"title": f"{product}功能介绍", "scenes": [
            {"scene_id": "S1", "visual": "展示创作场景", "narration": f"今天看一下{product}。",
             "subtitle": "开始创作", "planned_seconds": timings[0], "source_refs": []},
            {"scene_id": "S2", "visual": "展示产品功能页面", "narration": narration,
             "subtitle": fact["text"], "planned_seconds": timings[1], "source_refs": [reference]},
            {"scene_id": "S3", "visual": "收尾提问画面", "narration": "你想怎么表达下一条视频？",
             "subtitle": "欢迎分享想法", "planned_seconds": timings[2], "source_refs": []}]}

    def decide(self, request: dict, state: dict) -> dict:
        searches = state["searches"]
        if not searches or not searches[-1].get("ok"):
            return {"name": "search_materials", "arguments": {"query": request["topic"]},
                    "reason": "先检索主题对应的产品素材"}
        materials = [material for result in searches if result.get("ok") for material in result["materials"]]
        if not materials:
            if len([item for item in searches if item.get("ok")]) < 2:
                return {"name": "search_materials", "arguments": {
                    "query": request["topic"] + " 产品素材"},
                    "reason": "首次无结果，改写检索词补查"}
            return {"name": "finish", "arguments": {"status": "needs_material"},
                    "reason": "补查后仍无可核查素材，请用户补充"}
        material = materials[0]
        identifier = state["latest_script_id"]
        if identifier is None:
            return {"name": "estimate_duration", "arguments": {
                "script": self._script(request, material, "detailed")},
                "reason": "先生成完整分镜并交给独立工具估时"}
        estimate = state["estimates"].get(identifier)
        if estimate is None:
            return {"name": "estimate_duration", "arguments": {
                "script": self._script(request, material, "detailed")},
                "reason": "估时工具失败，重新提交草稿"}
        if not estimate["within_target"]:
            if len(state["drafts"]) < 3:
                variant = "compact" if len(state["drafts"]) == 1 else "micro"
                return {"name": "estimate_duration", "arguments": {
                    "script": self._script(request, material, variant)},
                    "reason": f"观察到超时 {estimate['over_by_seconds']} 秒，压缩脚本后重估"}
            return {"name": "finish", "arguments": {"status": "blocked"},
                    "reason": "多次压缩仍超过时长限制"}
        check = state["checks"].get(identifier)
        if check is None:
            return {"name": "check_rules", "arguments": {"script_id": identifier},
                    "reason": "估时已通过，核对素材事实和品牌规则"}
        if not check.get("ok"):
            return {"name": "check_rules", "arguments": {"script_id": identifier},
                    "reason": "规则工具失败，观察错误后重查同一草稿"}
        if not check["passed"]:
            if len(state["drafts"]) < 3:
                variant = "compact" if len(state["drafts"]) == 1 else "micro"
                return {"name": "estimate_duration", "arguments": {
                    "script": self._script(request, material, variant)},
                    "reason": "规则检查发现风险，删除无依据表述并重新估时"}
            return {"name": "finish", "arguments": {"status": "blocked"},
                    "reason": "无法消除规则风险"}
        return {"name": "finish", "arguments": {"status": "complete", "script_id": identifier},
                "reason": "同一草稿的时长与规则检查均已通过"}

    def observe(self, observation: dict) -> None:
        pass


class DeepSeekModel:
    name = "deepseek_chat"

    def __init__(self):
        self.api_key = os.getenv("DEEPSEEK_API_KEY")
        if not self.api_key:
            raise ModelUnavailableError("DEEPSEEK_API_KEY is required for --mode deepseek")
        self.model = os.getenv("DEEPSEEK_MODEL", "deepseek-flash")
        self.messages = None
        self.rejected_call_ids = []
        self.call_id = None
        self.turn = 0

    def decide(self, request: dict, state: dict) -> dict:
        if self.messages is None:
            self.messages = [{"role": "system", "content": INSTRUCTIONS},
                             {"role": "user", "content": json.dumps({"request": request}, ensure_ascii=False)}]
        payload = {"model": self.model, "messages": self.messages, "tools": TOOLS,
                   "tool_choice": "required", "thinking": {"type": "disabled"}, "max_tokens": 3500}
        http_request = urllib.request.Request(
            "https://api.deepseek.com/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode(),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(http_request, timeout=60) as response:
                data = json.load(response)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"model_http_{exc.code}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(f"model_transport_error:{type(exc).__name__}") from exc
        self.turn += 1
        calls = data["choices"][0]["message"].get("tool_calls") or []
        if not calls:
            raise RuntimeError("model_returned_no_tool_call")
        self.messages.append({"role": "assistant", "content": data["choices"][0]["message"].get("content"),
                              "tool_calls": calls})
        if len(calls) > 1:
            self.rejected_call_ids = [call["id"] for call in calls]
            return {"name": "parallel_calls_rejected", "arguments": {},
                    "reason": "并行动作会跳过逐次估时与规则观察", "model_turn": self.turn}
        call = calls[0]
        self.call_id = call["id"]
        try:
            arguments = json.loads(call["function"]["arguments"])
        except json.JSONDecodeError as exc:
            raise RuntimeError("invalid_model_tool_arguments") from exc
        if not isinstance(arguments, dict):
            raise RuntimeError("invalid_model_tool_arguments")
        return {"name": call["function"]["name"], "arguments": arguments,
                "reason": str(arguments.get("reason", "模型选择此动作"))[:200],
                "model_turn": self.turn}

    def observe(self, observation: dict) -> None:
        if self.rejected_call_ids:
            for call_id in self.rejected_call_ids:
                self.messages.append({"role": "tool", "tool_call_id": call_id,
                                      "content": json.dumps({"error": "parallel_calls_rejected"})})
            self.rejected_call_ids = []
            return
        self.messages.append({"role": "tool", "tool_call_id": self.call_id,
                              "content": json.dumps(observation, ensure_ascii=False)})
