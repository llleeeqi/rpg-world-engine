from __future__ import annotations

import json
import re
from typing import Any

from .llm import LLMError, OpenAICompatibleClient


MEMORY_WRITER_PROMPT = """你是长期互动 RPG 的 Memory Writer。
你的任务不是继续写剧情，而是把本轮已定稿的 Turn Outcome 整理成可撤销的记忆写入计划。

规则：
- 不新增 Turn Outcome 没有支撑的事实。
- resolved_events 进入玩家可见日志和世界事实。
- character_results 只更新已存在角色。
- 状态字段只能使用白名单：current_emotion, current_goal, location, outfit, visible_appearance, injuries, items, availability, custom。
- 秘密、误会和角色主观认知要明确说明 visibility 或写进 character_memory_updates，不要伪装成公开事实。
- 输出 JSON 对象，不要输出正文。
"""


STATE_FIELD_ALLOWLIST = {
    "current_emotion",
    "current_goal",
    "location",
    "outfit",
    "visible_appearance",
    "injuries",
    "items",
    "availability",
    "custom",
}


def safe_memory_id(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"\s+", "-", value)
    value = re.sub(r"[^a-z0-9_\-\u4e00-\u9fff]+", "", value)
    return value[:80]


def clean_text(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


class MemoryWriter:
    """Normalizes accepted turn outcomes into reversible memory patches."""

    def __init__(self, client: OpenAICompatibleClient | None = None):
        self.client = client or OpenAICompatibleClient()

    def build_plan(self, draft: dict[str, Any], characters: list[dict[str, Any]]) -> dict[str, Any]:
        fallback = self.fallback_plan(draft, characters)
        if not self.client.available:
            fallback["generated_by"] = "memory_fallback_no_llm_config"
            return fallback
        try:
            raw = self.llm_plan(draft, characters)
            plan = self.normalize_plan(raw, draft, characters)
            plan["generated_by"] = "openai_compatible_memory_writer"
            return plan
        except (LLMError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            fallback["generated_by"] = "memory_fallback_after_llm_error"
            fallback["warnings"].append(f"memory llm fallback: {exc}")
            return fallback

    def llm_plan(self, draft: dict[str, Any], characters: list[dict[str, Any]]) -> dict[str, Any]:
        messages = [
            {"role": "system", "content": MEMORY_WRITER_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "turn_id": draft.get("turn_id"),
                        "turn_input": draft.get("input", {}),
                        "turn_outcome": draft.get("turn_outcome", {}),
                        "characters": characters,
                        "output_schema": {
                            "visible_updates": [{"text": "玩家可见日志条目"}],
                            "truth_updates": [{"claim": "世界事实", "visibility": "visible|hidden"}],
                            "character_memory_updates": [
                                {
                                    "character_id": "角色 id",
                                    "memory": "角色本轮记忆",
                                    "emotion": "当前情绪",
                                    "next_goal": "下一目标",
                                    "location": "当前地点",
                                    "reason": "变更原因",
                                }
                            ],
                            "state_changes": [
                                {
                                    "character_id": "角色 id",
                                    "field": "允许的状态字段",
                                    "value": "新值",
                                    "reason": "变更原因",
                                }
                            ],
                            "scene_update": {},
                            "warnings": [],
                        },
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            },
        ]
        return self.client.chat_json("memory", messages, temperature=0.2)

    def fallback_plan(self, draft: dict[str, Any], characters: list[dict[str, Any]]) -> dict[str, Any]:
        outcome = draft.get("turn_outcome", {})
        turn_id = clean_text(draft.get("turn_id"), "unknown")
        known_ids = self.known_character_ids(characters)
        warnings: list[str] = []

        resolved = [clean_text(item) for item in outcome.get("resolved_events", []) if clean_text(item)]
        visible_updates = [{"text": item, "source_turn": turn_id} for item in resolved]
        truth_updates = [
            {
                "claim": item,
                "source_turn": turn_id,
                "status": "active",
                "visibility": "visible",
            }
            for item in resolved
        ]

        character_memory_updates = []
        state_changes = []
        for item in outcome.get("character_results", []):
            if not isinstance(item, dict):
                continue
            cid = safe_memory_id(str(item.get("character_id", "")))
            if cid not in known_ids:
                warnings.append(f"ignored character_result for unknown character {cid}")
                continue
            memory = clean_text(item.get("experienced"), "经历了本轮事件。")
            reason = clean_text(item.get("reason"), "本轮结果更新")
            character_memory_updates.append(
                {
                    "character_id": cid,
                    "memory": memory,
                    "reason": reason,
                    "source_turn": turn_id,
                }
            )
            for field, key in [
                ("current_emotion", "emotion"),
                ("current_goal", "next_goal"),
                ("location", "location"),
            ]:
                value = item.get(key)
                if value:
                    state_changes.append(
                        {
                            "character_id": cid,
                            "field": field,
                            "value": value,
                            "reason": reason,
                            "source_turn": turn_id,
                        }
                    )

        return {
            "visible_updates": visible_updates,
            "truth_updates": truth_updates,
            "character_memory_updates": character_memory_updates,
            "state_changes": state_changes,
            "scene_update": outcome.get("scene", {}) if isinstance(outcome.get("scene", {}), dict) else {},
            "warnings": warnings,
            "generated_by": "memory_fallback",
        }

    def normalize_plan(self, raw: dict[str, Any], draft: dict[str, Any], characters: list[dict[str, Any]]) -> dict[str, Any]:
        fallback = self.fallback_plan(draft, characters)
        turn_id = clean_text(draft.get("turn_id"), "unknown")
        known_ids = self.known_character_ids(characters)
        warnings = [clean_text(item) for item in raw.get("warnings", []) if clean_text(item)] if isinstance(raw.get("warnings", []), list) else []

        visible_updates = []
        for item in self.listify(raw.get("visible_updates", [])):
            text = clean_text(item.get("text") if isinstance(item, dict) else item)
            if text:
                visible_updates.append({"text": text, "source_turn": turn_id})

        truth_updates = []
        for item in self.listify(raw.get("truth_updates", [])):
            if isinstance(item, dict):
                claim = clean_text(item.get("claim") or item.get("text"))
                visibility = clean_text(item.get("visibility"), "visible")
            else:
                claim = clean_text(item)
                visibility = "visible"
            if claim:
                truth_updates.append(
                    {
                        "claim": claim,
                        "source_turn": turn_id,
                        "status": "active",
                        "visibility": visibility if visibility in {"visible", "hidden", "private"} else "visible",
                    }
                )

        character_memory_updates = []
        derived_changes = []
        for item in self.listify(raw.get("character_memory_updates", [])):
            if not isinstance(item, dict):
                continue
            cid = safe_memory_id(str(item.get("character_id", "")))
            if cid not in known_ids:
                warnings.append(f"ignored memory update for unknown character {cid}")
                continue
            memory = clean_text(item.get("memory") or item.get("experienced"), "经历了本轮事件。")
            reason = clean_text(item.get("reason"), "Memory Writer 更新")
            character_memory_updates.append(
                {
                    "character_id": cid,
                    "memory": memory,
                    "reason": reason,
                    "source_turn": turn_id,
                }
            )
            for field, key in [
                ("current_emotion", "emotion"),
                ("current_goal", "next_goal"),
                ("location", "location"),
            ]:
                value = item.get(key)
                if value:
                    derived_changes.append(
                        {
                            "character_id": cid,
                            "field": field,
                            "value": value,
                            "reason": reason,
                            "source_turn": turn_id,
                        }
                    )

        state_changes = derived_changes
        for item in self.listify(raw.get("state_changes", [])):
            if not isinstance(item, dict):
                continue
            cid = safe_memory_id(str(item.get("character_id", "")))
            field = clean_text(item.get("field"))
            if cid not in known_ids:
                warnings.append(f"ignored state change for unknown character {cid}")
                continue
            if field not in STATE_FIELD_ALLOWLIST:
                warnings.append(f"ignored state field {field} for {cid}")
                continue
            state_changes.append(
                {
                    "character_id": cid,
                    "field": field,
                    "value": item.get("value"),
                    "reason": clean_text(item.get("reason"), "Memory Writer 更新"),
                    "source_turn": turn_id,
                }
            )

        if not visible_updates:
            visible_updates = fallback["visible_updates"]
            warnings.append("visible_updates empty; used fallback")
        if not truth_updates:
            truth_updates = fallback["truth_updates"]
            warnings.append("truth_updates empty; used fallback")
        if not character_memory_updates:
            character_memory_updates = fallback["character_memory_updates"]
            warnings.append("character_memory_updates empty; used fallback")
        if not state_changes:
            state_changes = fallback["state_changes"]
            warnings.append("state_changes empty; used fallback")

        scene_update = raw.get("scene_update", raw.get("scene", {}))
        if not isinstance(scene_update, dict):
            scene_update = fallback["scene_update"]
            warnings.append("scene_update was not an object; used fallback")

        return {
            "visible_updates": visible_updates[:24],
            "truth_updates": truth_updates[:48],
            "character_memory_updates": character_memory_updates[:24],
            "state_changes": self.dedupe_state_changes(state_changes)[:64],
            "scene_update": scene_update,
            "warnings": warnings[:40],
        }

    def known_character_ids(self, characters: list[dict[str, Any]]) -> set[str]:
        return {safe_memory_id(str(char.get("character_id", ""))) for char in characters if char.get("character_id")}

    def listify(self, value: Any) -> list[Any]:
        if isinstance(value, list):
            return value
        if value in (None, ""):
            return []
        return [value]

    def dedupe_state_changes(self, changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[tuple[str, str]] = set()
        deduped: list[dict[str, Any]] = []
        for item in reversed(changes):
            key = (str(item.get("character_id", "")), str(item.get("field", "")))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return list(reversed(deduped))
