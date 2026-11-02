"""Synthetic material search, independent duration estimate, and brand/fact checks."""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_INPUT = ("topic", "audience", "platform", "target_duration_seconds", "style")


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in value if character.isalnum())


def script_id(script: dict) -> str:
    payload = json.dumps(script, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()[:12]


def preflight(request: dict, rules: dict) -> dict | None:
    if not isinstance(request, dict):
        raise ValueError("input must be a JSON object")
    missing = [key for key in REQUIRED_INPUT if request.get(key) is None or request.get(key) == ""]
    if missing:
        return {"task_status": "needs_input", "missing_fields": missing,
                "incomplete_reason": "missing_required_input", "risk_reasons": [],
                "title": None, "scenes": [], "estimated_total_seconds": None,
                "material_source_ids": [], "material_citations": [],
                "tool_results": {}, "steps": [], "trace": [], "mode": "preflight"}
    for key in ("topic", "audience", "platform", "style"):
        if not isinstance(request[key], str) or not request[key].strip():
            raise ValueError(f"{key} must be a nonempty string")
    duration = request["target_duration_seconds"]
    if isinstance(duration, bool) or not isinstance(duration, int) or duration <= 0:
        raise ValueError("target_duration_seconds must be a positive integer")
    if request["platform"] not in rules["platforms"]:
        raise ValueError("platform is not configured in brand_rules.json")
    platform_max = rules["platforms"][request["platform"]]["maximum_duration_seconds"]
    if duration > platform_max or duration < rules["minimum_scene_seconds"]:
        reason = "target_exceeds_platform_maximum" if duration > platform_max else "target_below_minimum_scene"
        return {"task_status": "incomplete", "missing_fields": [], "incomplete_reason": reason,
                "risk_reasons": [reason], "title": None, "scenes": [],
                "estimated_total_seconds": None, "material_source_ids": [],
                "material_citations": [], "tool_results": {}, "steps": [],
                "trace": [], "mode": "preflight"}
    return None


class BusinessTools:
    def __init__(self, *, corpus_path: Path | None = None, rules_path: Path | None = None,
                 fail_once: set[str] | None = None):
        source = corpus_path or ROOT / "data/materials.json"
        self.materials = json.loads(source.read_text(encoding="utf-8"))
        self.rules = json.loads((rules_path or ROOT / "data/brand_rules.json").read_text(encoding="utf-8"))
        if not isinstance(self.materials, list):
            raise ValueError("materials corpus must be a JSON list")
        self.by_id = {}
        for material in self.materials:
            material_id = material["material_id"]
            if material_id in self.by_id:
                raise ValueError(f"duplicate material_id: {material_id}")
            self.by_id[material_id] = material
            if not material["facts"] or not material["aliases"]:
                raise ValueError(f"material {material_id} needs facts and aliases")
        self.revision = hashlib.sha256(source.read_bytes()).hexdigest()[:12]
        self.fail_once = set(fail_once or ())

    def search_materials(self, query: str) -> dict:
        if "search_materials" in self.fail_once:
            self.fail_once.remove("search_materials")
            return {"ok": False, "error": "material_search_timeout", "query": query}
        normalized = normalize(query)
        found = [material for material in self.materials
                 if normalized and any(normalize(alias) in normalized for alias in material["aliases"])]
        return {"ok": True, "query": query, "materials": found,
                "corpus_revision": self.revision}

    def _validate_script(self, script: dict) -> str | None:
        if not isinstance(script, dict) or not isinstance(script.get("title"), str) or not script["title"].strip():
            return "script_title_required"
        if set(script) != {"title", "scenes"}:
            return "unexpected_script_fields"
        scenes = script.get("scenes")
        if not isinstance(scenes, list) or not 1 <= len(scenes) <= self.rules["maximum_scenes"]:
            return "invalid_scene_count"
        seen = set()
        for scene in scenes:
            if not isinstance(scene, dict):
                return "invalid_scene"
            if set(scene) != {"scene_id", "visual", "narration", "subtitle", "planned_seconds", "source_refs"}:
                return "unexpected_scene_fields"
            for key in ("scene_id", "visual", "narration", "subtitle"):
                if not isinstance(scene.get(key), str):
                    return f"invalid_{key}"
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,16}", scene["scene_id"]) or scene["scene_id"] in seen:
                return "duplicate_or_empty_scene_id"
            seen.add(scene["scene_id"])
            if not scene["visual"].strip() or not (scene["narration"].strip() or scene["subtitle"].strip()):
                return "scene_needs_visual_and_voice_or_subtitle"
            seconds = scene.get("planned_seconds")
            if isinstance(seconds, bool) or not isinstance(seconds, int) or seconds < 1:
                return "invalid_planned_seconds"
            refs = scene.get("source_refs")
            if not isinstance(refs, list):
                return "source_refs_must_be_list"
            for ref in refs:
                if (not isinstance(ref, dict)
                        or set(ref) != {"material_id", "fact_id", "quote"}
                        or any(not isinstance(ref.get(key), str) for key in ("material_id", "fact_id", "quote"))):
                    return "invalid_source_ref"
        return None

    def estimate_duration(self, script: dict, request: dict, state: dict) -> dict:
        if "estimate_duration" in self.fail_once:
            self.fail_once.remove("estimate_duration")
            return {"ok": False, "error": "duration_estimator_timeout"}
        error = self._validate_script(script)
        if error:
            return {"ok": False, "error": error}
        identifier = script_id(script)
        per_scene = []
        cursor = 0
        for scene in script["scenes"]:
            spoken = math.ceil(len(normalize(scene["narration"])) / self.rules["spoken_chars_per_second"])
            subtitle = math.ceil(len(normalize(scene["subtitle"])) / self.rules["subtitle_chars_per_second"])
            seconds = max(scene["planned_seconds"], spoken, subtitle, self.rules["minimum_scene_seconds"])
            per_scene.append({"scene_id": scene["scene_id"], "planned_seconds": scene["planned_seconds"],
                              "spoken_minimum_seconds": spoken, "subtitle_minimum_seconds": subtitle,
                              "estimated_seconds": seconds, "start_seconds": cursor,
                              "end_seconds": cursor + seconds})
            cursor += seconds
        state["drafts"][identifier] = deepcopy(script)
        result = {"ok": True, "script_id": identifier, "estimated_total_seconds": cursor,
                  "target_duration_seconds": request["target_duration_seconds"],
                  "within_target": cursor <= request["target_duration_seconds"],
                  "over_by_seconds": max(0, cursor - request["target_duration_seconds"]),
                  "per_scene": per_scene}
        state["estimates"][identifier] = result
        state["latest_script_id"] = identifier
        return result

    def check_rules(self, identifier: str, state: dict) -> dict:
        script = state["drafts"].get(identifier)
        if script is None:
            return {"ok": False, "error": "estimated_script_required", "script_id": identifier}
        if script_id(script) != identifier:
            return {"ok": False, "error": "draft_integrity_error", "script_id": identifier}
        if "check_rules" in self.fail_once:
            self.fail_once.remove("check_rules")
            return {"ok": False, "error": "rule_checker_timeout", "script_id": identifier}
        retrieved = {item["material_id"] for search in state["searches"] if search.get("ok")
                     for item in search["materials"]}
        risks = []
        citations = []
        for scene in script["scenes"]:
            combined = " ".join((scene["visual"], scene["narration"], scene["subtitle"]))
            for ref in scene["source_refs"]:
                mid = ref["material_id"]
                material = self.by_id.get(mid)
                if mid not in retrieved or material is None:
                    risks.append({"code": "material_not_retrieved", "scene_id": scene["scene_id"],
                                  "material_id": mid})
                    continue
                fact = next((item for item in material["facts"] if item["fact_id"] == ref["fact_id"]), None)
                if fact is None or ref["quote"] != fact["text"]:
                    risks.append({"code": "unsupported_fact_reference", "scene_id": scene["scene_id"],
                                  "material_id": mid, "fact_id": ref["fact_id"]})
                    continue
                if ref["quote"] not in scene["narration"] + scene["subtitle"]:
                    risks.append({"code": "quote_not_used_in_scene", "scene_id": scene["scene_id"],
                                  "material_id": mid, "fact_id": ref["fact_id"]})
                citations.append({"material_id": mid, "fact_id": ref["fact_id"],
                                  "quote": fact["text"]})
            approved = {ref["quote"] for ref in scene["source_refs"]
                        if any(c["material_id"] == ref["material_id"] and c["fact_id"] == ref["fact_id"]
                               for c in citations)}
            for phrase in re.findall(r"支持[\u4e00-\u9fffA-Za-z0-9]{1,10}", combined):
                if phrase not in approved and not any(phrase == quote + "功能" for quote in approved):
                    risks.append({"code": "unsupported_product_claim", "scene_id": scene["scene_id"],
                                  "claim": phrase})
        if not citations:
            risks.append({"code": "missing_material_citation"})
        full_text = " ".join([script["title"], *(scene[key] for scene in script["scenes"]
                                                    for key in ("visual", "narration", "subtitle"))])
        cited_facts = [item["quote"] for item in citations]
        metric_pattern = r"\d+(?:\.\d+)?\s*%|百分之[\d一二三四五六七八九十百]+|(?:提升|节省)\s*\d+(?:\.\d+)?\s*倍?"
        for metric in sorted(set(re.findall(metric_pattern, full_text))):
            if not any(normalize(metric) in normalize(fact) for fact in cited_facts):
                risks.append({"code": "unsupported_quantitative_claim", "claim": metric})
        for term in self.rules["forbidden_absolute_terms"]:
            if term in full_text:
                risks.append({"code": "forbidden_absolute_claim", "term": term})
        for marker in self.rules["unsupported_feature_markers"]:
            if marker in full_text and not any(marker in fact for fact in cited_facts):
                risks.append({"code": "unsupported_feature_claim", "term": marker})
        title_claims = re.findall(r"支持[\u4e00-\u9fffA-Za-z0-9]{1,10}", script["title"])
        approved_all = {item["quote"] for item in citations}
        for phrase in title_claims:
            if phrase not in approved_all and not any(phrase == quote + "功能" for quote in approved_all):
                risks.append({"code": "unsupported_title_claim", "claim": phrase})
        unique_citations = list({(item["material_id"], item["fact_id"]): item for item in citations}.values())
        result = {"ok": True, "script_id": identifier, "passed": not risks,
                  "risks": risks, "citations": unique_citations,
                  "checked_scene_ids": [scene["scene_id"] for scene in script["scenes"]]}
        state["checks"][identifier] = result
        return result

    def finish(self, status: str, identifier: str | None, request: dict, state: dict) -> dict:
        if status == "needs_material":
            successful = [item for item in state["searches"] if item.get("ok")]
            if not successful:
                return {"ok": False, "accepted": False, "error": "successful_search_required"}
            if any(item["materials"] for item in successful):
                return {"ok": False, "accepted": False, "error": "materials_available"}
            return {"ok": True, "accepted": True, "task_status": "needs_material",
                    "incomplete_reason": "no_relevant_material", "risk_reasons": [],
                    "material_gaps": ["未找到可核查的产品素材，请提供素材或补充产品信息"]}
        if status == "blocked":
            if not state["estimates"] and not state["checks"] and not state["last_tool_error"]:
                return {"ok": False, "accepted": False, "error": "attempt_required_before_blocking"}
            return {"ok": True, "accepted": True, "task_status": "incomplete",
                    "incomplete_reason": state["last_tool_error"] or "no_valid_script",
                    "risk_reasons": self._current_risks(state), "material_gaps": []}
        if status != "complete" or not isinstance(identifier, str):
            return {"ok": False, "accepted": False, "error": "invalid_finish_arguments"}
        estimate = state["estimates"].get(identifier)
        check = state["checks"].get(identifier)
        script = state["drafts"].get(identifier)
        if not estimate or not script:
            return {"ok": False, "accepted": False, "error": "duration_estimate_required"}
        if script_id(script) != identifier:
            return {"ok": False, "accepted": False, "error": "draft_integrity_error"}
        if not estimate["within_target"]:
            return {"ok": False, "accepted": False, "error": "duration_exceeds_target",
                    "over_by_seconds": estimate["over_by_seconds"]}
        if not check or not check.get("ok"):
            return {"ok": False, "accepted": False, "error": "rule_check_required"}
        if not check["passed"]:
            return {"ok": False, "accepted": False, "error": "rules_not_passed",
                    "risks": check["risks"]}
        timing = {item["scene_id"]: item for item in estimate["per_scene"]}
        scenes = [{**scene, **timing[scene["scene_id"]]} for scene in script["scenes"]]
        citations = check["citations"]
        if not citations:
            return {"ok": False, "accepted": False, "error": "material_citation_required"}
        return {"ok": True, "accepted": True, "task_status": "complete",
                "incomplete_reason": None, "risk_reasons": [], "material_gaps": [],
                "title": script["title"], "scenes": scenes,
                "estimated_total_seconds": estimate["estimated_total_seconds"],
                "material_source_ids": sorted({item["material_id"] for item in citations}),
                "material_citations": citations, "script_id": identifier}

    @staticmethod
    def _current_risks(state: dict) -> list:
        identifier = state["latest_script_id"]
        estimate = state["estimates"].get(identifier) if identifier else None
        check = state["checks"].get(identifier) if identifier else None
        risks = list(check["risks"]) if check and check.get("ok") else []
        if estimate and not estimate["within_target"]:
            risks.append({"code": "duration_exceeds_target", "over_by_seconds": estimate["over_by_seconds"]})
        return risks
