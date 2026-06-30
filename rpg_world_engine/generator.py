from __future__ import annotations

import copy
import random
import re
from dataclasses import dataclass
from typing import Any

from .llm import LLMError, OpenAICompatibleClient
from .rules import DEFAULT_DICE_RULES
from .storage import safe_id, utc_now


def merged_rules(rules: dict[str, Any] | None = None) -> dict[str, Any]:
    source = rules if isinstance(rules, dict) else {}
    merged = {
        "version": source.get("version", DEFAULT_DICE_RULES["version"]),
        "dm_force_patterns": list(source.get("dm_force_patterns") or DEFAULT_DICE_RULES["dm_force_patterns"]),
        "roll_triggers": dict(DEFAULT_DICE_RULES["roll_triggers"]),
        "dice": dict(DEFAULT_DICE_RULES["dice"]),
    }
    if isinstance(source.get("roll_triggers"), dict):
        merged["roll_triggers"].update(source["roll_triggers"])
    if isinstance(source.get("dice"), dict):
        merged["dice"].update(source["dice"])
    return merged


def int_rule(value: Any, fallback: int, min_value: int | None = None, max_value: int | None = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = fallback
    if min_value is not None:
        parsed = max(min_value, parsed)
    if max_value is not None:
        parsed = min(max_value, parsed)
    return parsed


def dict_int_lookup(source: Any, key: str, fallback: int) -> int:
    if not isinstance(source, dict):
        return fallback
    return int_rule(source.get(key, fallback), fallback)


def strip_first_person(text: str, name: str) -> str:
    cleaned = text.strip()
    cleaned = re.sub(r"^我", name, cleaned)
    cleaned = cleaned.replace("我", name)
    return cleaned


def includes_force(dm_directive: str, rules: dict[str, Any] | None = None) -> bool:
    config = merged_rules(rules)
    return any(str(token) in dm_directive for token in config["dm_force_patterns"])


def should_roll(order_count: int, pace: str, divergence: str, dm_directive: str, rules: dict[str, Any] | None = None) -> bool:
    config = merged_rules(rules)
    trigger = config["roll_triggers"]
    if includes_force(dm_directive, config):
        return False
    if order_count == 0 and not dm_directive.strip():
        return False
    score = 0
    score += dict_int_lookup(trigger.get("pace_score"), pace, 1)
    score += dict_int_lookup(trigger.get("divergence_score"), divergence, 1)
    risk_words = trigger.get("risk_words") if isinstance(trigger.get("risk_words"), list) else []
    if any(str(word) in dm_directive for word in risk_words):
        score += int_rule(trigger.get("risk_word_score"), 2)
    return score >= int_rule(trigger.get("minimum_score"), 2, 0, 99)


def dice_check(pace: str, divergence: str, reason: str, existing: dict[str, Any] | None = None, rules: dict[str, Any] | None = None) -> dict[str, Any]:
    if existing:
        return dict(existing)
    config = merged_rules(rules)
    dice = config["dice"]
    min_roll = int_rule(dice.get("min_roll"), 1, 1, 999)
    max_roll = int_rule(dice.get("max_roll"), 20, min_roll, 999)
    base = dict_int_lookup(dice.get("pace_base"), pace, 12)
    base += dict_int_lookup(dice.get("divergence_modifier"), divergence, 0)
    jitter = dice.get("random_difficulty_jitter", [-2, 2])
    if not isinstance(jitter, list) or len(jitter) != 2:
        jitter = [-2, 2]
    low_jitter = int_rule(jitter[0], -2)
    high_jitter = int_rule(jitter[1], 2)
    if low_jitter > high_jitter:
        low_jitter, high_jitter = high_jitter, low_jitter
    min_difficulty = int_rule(dice.get("difficulty_min"), 5, 1, 999)
    max_difficulty = int_rule(dice.get("difficulty_max"), 20, min_difficulty, 999)
    difficulty = max(min_difficulty, min(max_difficulty, base + random.randint(low_jitter, high_jitter)))
    roll = random.randint(min_roll, max_roll)
    bonus_choices = dice.get("bonus_choices") if isinstance(dice.get("bonus_choices"), list) else [0, 1, 2]
    clean_bonus = [int_rule(item, 0) for item in bonus_choices] or [0]
    bonus = random.choice(clean_bonus)
    total = roll + bonus
    margin = int_rule(dice.get("partial_success_margin"), 3, 0, 999)
    if roll == max_roll:
        outcome = "critical_success"
    elif roll == min_roll:
        outcome = "critical_failure"
    elif total >= difficulty:
        outcome = "success"
    elif difficulty - total <= margin:
        outcome = "partial_success"
    else:
        outcome = "failure"
    return {
        "type": str(dice.get("type") or f"d{max_roll}"),
        "reason": reason,
        "difficulty": difficulty,
        "roll": roll,
        "bonus": bonus,
        "total": total,
        "outcome": outcome,
        "explanation": f"难度 {difficulty}：依据当前世界的骰子规则、推进级别、发散程度和场面风险判断。",
    }


@dataclass
class DraftContext:
    turn_id: str
    input_payload: dict[str, Any]
    characters: list[dict[str, Any]]
    recent_events: list[dict[str, Any]]
    scene: dict[str, Any]
    location_state: dict[str, Any] | None = None
    world_bible: str = ""
    dm_policy: str = ""
    imported_preset: str = ""
    relevant_docs: list[dict[str, Any]] | None = None
    previous_candidate: dict[str, Any] | None = None
    mode: str = "new"
    keep_dice: bool = False
    agent_budget: dict[str, Any] | None = None
    rules: dict[str, Any] | None = None


AUTO_INDEPENDENT_MODES = {"independent_default", "independent_forced", "independent"}
NPC_ENSEMBLE_MODES = {"npc_ensemble", "grouped_npc"}
COLD_AVAILABILITY = {"dormant", "cold", "offstage", "archived"}


def agent_budget_limit(ctx: DraftContext) -> int:
    raw = (ctx.agent_budget or {}).get("max_independent_character_agents", 8)
    try:
        return max(1, min(32, int(raw)))
    except (TypeError, ValueError):
        return 8


def agent_state(char: dict[str, Any]) -> dict[str, Any]:
    state = char.get("state", {})
    return state if isinstance(state, dict) else {}


def character_agent_mode(char: dict[str, Any]) -> str:
    state = agent_state(char)
    return str(state.get("agent_mode") or char.get("agent_mode") or "independent_default")


def character_availability(char: dict[str, Any]) -> str:
    state = agent_state(char)
    return str(state.get("availability") or char.get("availability") or "active")


def character_agent_enabled(char: dict[str, Any]) -> bool:
    state = agent_state(char)
    return bool(state.get("agent_enabled", char.get("agent_enabled", True)))


def can_auto_independent(char: dict[str, Any]) -> bool:
    if not character_agent_enabled(char):
        return False
    if character_availability(char) in COLD_AVAILABILITY:
        return False
    return character_agent_mode(char) in AUTO_INDEPENDENT_MODES


def can_route_to_npc_ensemble(char: dict[str, Any]) -> bool:
    if not character_agent_enabled(char):
        return False
    if character_availability(char) in COLD_AVAILABILITY:
        return False
    return character_agent_mode(char) not in {"disabled", "dormant"}


def character_agent_priority(char: dict[str, Any], scene: dict[str, Any]) -> tuple[int, int, str]:
    cid = safe_id(str(char.get("character_id", "")))
    participants = scene.get("participants", [])
    if not isinstance(participants, list):
        participants = []
    role = str(char.get("role_type") or agent_state(char).get("role_type") or "")
    forced = 0 if character_agent_mode(char) == "independent_forced" else 1
    scene_rank = 0 if cid in {safe_id(str(item)) for item in participants} else 1
    role_rank = {"controllable": 0, "major_npc": 1, "background_npc": 2, "lore_entity": 3}.get(role, 4)
    return (forced, scene_rank + role_rank, cid)


def select_agent_participants(ctx: DraftContext, orders: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    max_agents = agent_budget_limit(ctx)
    controlled_ids = [safe_id(str(order["character_id"])) for order in orders if order.get("character_id")]
    by_id = {safe_id(str(char["character_id"])): char for char in ctx.characters if char.get("character_id")}
    active: list[dict[str, Any]] = []
    active_ids: set[str] = set()
    for cid in controlled_ids:
        char = by_id.get(cid)
        if char and cid not in active_ids and len(active) < max_agents:
            active.append(char)
            active_ids.add(cid)

    candidates = [
        char
        for char in ctx.characters
        if safe_id(str(char.get("character_id", ""))) not in active_ids and can_auto_independent(char)
    ]
    for char in sorted(candidates, key=lambda item: character_agent_priority(item, ctx.scene)):
        cid = safe_id(str(char.get("character_id", "")))
        if len(active) >= max_agents:
            break
        active.append(char)
        active_ids.add(cid)

    overflow = [
        char
        for char in ctx.characters
        if safe_id(str(char.get("character_id", ""))) not in active_ids and can_route_to_npc_ensemble(char)
    ]
    return active, overflow


class LocalFallbackGenerator:
    """Deterministic-enough local generator for offline MVP runs.

    This is deliberately not a replacement for the LLM provider layer. It keeps
    the full turn lifecycle testable before an API key is configured.
    """

    def build_draft(self, ctx: DraftContext) -> dict[str, Any]:
        if ctx.mode == "rewrite" and ctx.previous_candidate:
            return self.rewrite_previous(ctx)

        payload = ctx.input_payload
        pace = payload.get("pace", "scene")
        divergence = payload.get("divergence", "medium")
        dm_directive = payload.get("dm_directive", "").strip()
        rules = merged_rules(ctx.rules)
        orders = [order for order in payload.get("controlled_orders", []) if order.get("character_id")]

        active, overflow_chars = select_agent_participants(ctx, orders)

        intents = []
        for char in active:
            cid = safe_id(char["character_id"])
            matching = next((order for order in orders if safe_id(order["character_id"]) == cid), None)
            if matching:
                intent = strip_first_person(matching.get("text", ""), char.get("name", cid))
                source = "player_controlled"
            else:
                state = char.get("state", {})
                goal = state.get("current_goal") or char.get("current_goal") or "观察局势"
                intent = f"{char.get('name', cid)}按照既有目标“{goal}”谨慎行动。"
                source = "auto_agent"
            intents.append({
                "character_id": cid,
                "name": char.get("name", cid),
                "source": source,
                "intent": intent,
                "emotion": char.get("state", {}).get("current_emotion", char.get("current_emotion", "平静")),
                "dialogue_suggestion": "",
            })

        npc_ensemble = {
            "used": bool(overflow_chars),
            "summary": f"{len(overflow_chars)} 个其余活跃角色由 NPC Ensemble 统一处理。" if overflow_chars else "没有角色溢出到 NPC Ensemble。",
            "intents": [
                {
                    "character_id": safe_id(str(char.get("character_id", ""))),
                    "intent": f"{char.get('name', char.get('character_id', 'NPC'))}按既有状态参与背景反应。",
                }
                for char in overflow_chars[:12]
            ],
        }

        forced = includes_force(dm_directive, rules)
        no_roll_reasons = []
        dice = []
        if forced:
            no_roll_reasons.append(f"DM 指令包含硬约束，相关走向按 DM 意图处理：{dm_directive[:120]}")
        elif ctx.keep_dice and ctx.previous_candidate:
            dice = copy.deepcopy(ctx.previous_candidate.get("turn_outcome", {}).get("dice", []))
            no_roll_reasons = copy.deepcopy(ctx.previous_candidate.get("turn_outcome", {}).get("no_roll_reasons", []))
            if not dice and not no_roll_reasons:
                no_roll_reasons.append("保留上一候选的无骰子判定。")
        elif should_roll(len(orders), pace, divergence, dm_directive, rules):
            reason = dm_directive[:120] or "本轮行动存在不确定性和代价"
            dice.append(dice_check(pace, divergence, reason, None, rules))
        else:
            no_roll_reasons.append("无需骰子：本轮行动没有明显对抗，或结果主要由自然后果推进。")

        resolved_events = self.resolved_events(intents, dm_directive, dice, forced, pace, divergence)
        character_results = []
        for intent in intents:
            name = intent["name"]
            character_results.append({
                "character_id": intent["character_id"],
                "experienced": f"{name}参与了本轮推进：{intent['intent']}",
                "emotion": self.next_emotion(intent, dice, forced),
                "next_goal": "根据本轮结果调整下一步行动",
                "reason": "本轮推演结果",
            })

        title = ctx.scene.get("title") or "当前场景"
        if dm_directive and any(word in dm_directive for word in ["切到", "转场", "几天后", "随后"]):
            title = "DM 指定的新场景"
        scene = {
            "title": title,
            "focus": resolved_events[0] if resolved_events else "局势继续推进",
            "participants": [intent["character_id"] for intent in intents[:8]],
            "open_conflicts": [event for event in resolved_events if any(word in event for word in ["冲突", "失败", "代价", "争执"])],
            "continuity_locks": [event for event in resolved_events[:3]],
        }

        turn_outcome = {
            "resolved_events": resolved_events,
            "character_results": character_results,
            "character_intents": intents,
            "npc_ensemble": npc_ensemble,
            "dice": dice,
            "no_roll_reasons": no_roll_reasons,
            "dm_forced": [{"text": dm_directive, "random_roll_used": False}] if forced else [],
            "scene": scene,
            "pace": pace,
            "divergence": divergence,
        }
        narrative = self.write_narrative(turn_outcome, ctx.mode)
        return {
            "turn_id": ctx.turn_id,
            "input": payload,
            "active_agents": [intent["character_id"] for intent in intents],
            "retrieval_context": ctx.relevant_docs or [],
            "turn_outcome": turn_outcome,
            "narrative": narrative,
            "generated_by": "local_fallback",
            "mode": ctx.mode,
            "created_at": utc_now(),
            "rules_version": rules.get("version", 1),
        }

    def rewrite_previous(self, ctx: DraftContext) -> dict[str, Any]:
        previous = ctx.previous_candidate or {}
        outcome = copy.deepcopy(previous.get("turn_outcome", {}))
        payload = copy.deepcopy(previous.get("input", ctx.input_payload))
        narrative = self.write_narrative(outcome, "rewrite")
        return {
            "turn_id": ctx.turn_id,
            "input": payload,
            "active_agents": list(previous.get("active_agents", [])),
            "retrieval_context": ctx.relevant_docs or previous.get("retrieval_context", []),
            "turn_outcome": outcome,
            "narrative": narrative,
            "generated_by": "local_fallback_rewrite_only",
            "mode": "rewrite",
            "created_at": utc_now(),
            "rules_version": previous.get("rules_version", merged_rules(ctx.rules).get("version", 1)),
            "rewrite_of": {
                "turn_id": previous.get("turn_id"),
                "candidate_id": previous.get("candidate_id"),
            },
        }

    def resolved_events(self, intents: list[dict[str, Any]], dm_directive: str, dice: list[dict[str, Any]], forced: bool, pace: str, divergence: str) -> list[str]:
        events = []
        if dm_directive:
            events.append(f"DM 指令成为本轮主要走向：{dm_directive}")
        for intent in intents[:4]:
            events.append(f"{intent['name']}的行动意图被纳入场景：{intent['intent']}")
        if dice:
            check = dice[0]
            outcome_text = {
                "critical_success": "获得重大优势",
                "success": "达成目标",
                "partial_success": "带着代价部分达成目标",
                "failure": "未能达成目标，并留下后续压力",
                "critical_failure": "遭遇严重代价",
            }.get(check["outcome"], check["outcome"])
            events.append(f"随机判定结果：{outcome_text}。")
        elif forced:
            events.append("相关随机判定被 DM 硬约束覆盖。")
        else:
            events.append("本轮按自然后果推进，没有触发随机判定。")
        if divergence == "high":
            events.append("高发散推进带来新的支线苗头，但未覆盖已确认事实。")
        if pace in {"sequence", "downtime"}:
            events.append("较长推进使场景向下一个自然停顿点过渡。")
        return events

    def next_emotion(self, intent: dict[str, Any], dice: list[dict[str, Any]], forced: bool) -> str:
        if forced:
            return "受到 DM 走向牵引后的专注"
        if not dice:
            return intent.get("emotion", "平静")
        outcome = dice[0]["outcome"]
        if outcome in {"success", "critical_success"}:
            return "短暂松动后的主动"
        if outcome == "partial_success":
            return "警惕且带着压力"
        return "受挫、警惕"

    def write_narrative(self, outcome: dict[str, Any], mode: str = "new") -> str:
        style_prefix = "换一种叙述来看，" if mode == "rewrite" else ""
        paragraphs = []
        first = outcome["resolved_events"][0] if outcome.get("resolved_events") else "局势继续向前移动。"
        paragraphs.append(f"{style_prefix}{first}")
        for intent in outcome.get("character_intents", [])[:5]:
            name = intent["name"]
            action = intent["intent"]
            emotion = intent.get("emotion", "平静")
            paragraphs.append(f"{name}没有脱离自己的立场。{action} 这种选择让{name}显得{emotion}，也把场面推向了更明确的方向。")
        if outcome.get("dice"):
            check = outcome["dice"][0]
            paragraphs.append(
                f"真正让局势定型的是那次判定：掷骰 {check['roll']}，加值 {check['bonus']}，总计 {check['total']}，"
                f"对上难度 {check['difficulty']}，结果是 {check['outcome']}。"
            )
        for reason in outcome.get("no_roll_reasons", []):
            paragraphs.append(reason)
        if outcome.get("npc_ensemble", {}).get("used"):
            paragraphs.append(outcome["npc_ensemble"]["summary"])
        paragraphs.append("这一轮在一个自然停顿点收束，留下的后果会进入下一轮。")
        return "\n\n".join(paragraphs)


class MultiAgentGenerator:
    """OpenAI-compatible multi-agent generator with local fallback.

    The class keeps the agreed design boundary: character agents generate
    intentions, Judge fixes the outcome, Writer turns the outcome into prose.
    If provider config is missing or a call fails, the local fallback produces a
    usable draft and records why it degraded.
    """

    def __init__(self, client: OpenAICompatibleClient | None = None, fallback: LocalFallbackGenerator | None = None):
        self.client = client or OpenAICompatibleClient()
        self.fallback = fallback or LocalFallbackGenerator()

    def build_draft(self, ctx: DraftContext) -> dict[str, Any]:
        if ctx.mode == "rewrite" and ctx.previous_candidate:
            if not self.client.available:
                draft = self.fallback.rewrite_previous(ctx)
                draft["generated_by"] = "local_fallback_rewrite_only_no_llm_config"
                return draft
            try:
                return self.rewrite_previous_with_llm(ctx)
            except (LLMError, KeyError, TypeError, ValueError) as exc:
                draft = self.fallback.rewrite_previous(ctx)
                draft["generated_by"] = "local_fallback_rewrite_only_after_llm_error"
                draft["llm_error"] = str(exc)
                return draft
        if not self.client.available:
            draft = self.fallback.build_draft(ctx)
            draft["generated_by"] = "local_fallback_no_llm_config"
            return draft
        try:
            return self._build_with_llm(ctx)
        except (LLMError, KeyError, TypeError, ValueError) as exc:
            draft = self.fallback.build_draft(ctx)
            draft["generated_by"] = "local_fallback_after_llm_error"
            draft["llm_error"] = str(exc)
            return draft

    def rewrite_previous_with_llm(self, ctx: DraftContext) -> dict[str, Any]:
        previous = ctx.previous_candidate or {}
        outcome = copy.deepcopy(previous.get("turn_outcome", {}))
        narrative = self.writer(ctx, outcome)
        return {
            "turn_id": ctx.turn_id,
            "input": copy.deepcopy(previous.get("input", ctx.input_payload)),
            "active_agents": list(previous.get("active_agents", [])),
            "retrieval_context": ctx.relevant_docs or previous.get("retrieval_context", []),
            "turn_outcome": outcome,
            "narrative": narrative,
            "generated_by": "openai_compatible_writer_rewrite_only",
            "mode": "rewrite",
            "created_at": utc_now(),
            "rules_version": previous.get("rules_version", merged_rules(ctx.rules).get("version", 1)),
            "rewrite_of": {
                "turn_id": previous.get("turn_id"),
                "candidate_id": previous.get("candidate_id"),
            },
        }

    def _build_with_llm(self, ctx: DraftContext) -> dict[str, Any]:
        payload = ctx.input_payload
        orders = [order for order in payload.get("controlled_orders", []) if order.get("character_id")]
        selected = self.select_active_characters(ctx, orders)
        intents = [self.character_intent(ctx, char, orders) for char in selected]
        _, overflow_chars = select_agent_participants(ctx, orders)
        overflow_chars = [char for char in overflow_chars if safe_id(str(char["character_id"])) not in {i["character_id"] for i in intents}]
        npc_ensemble = self.npc_ensemble(ctx, overflow_chars) if overflow_chars else {
            "used": False,
            "summary": "没有角色溢出到 NPC Ensemble。",
            "intents": [],
        }
        judge = self.judge(ctx, intents, npc_ensemble)
        outcome = self.compose_outcome(ctx, intents, npc_ensemble, judge)
        narrative = self.writer(ctx, outcome)
        return {
            "turn_id": ctx.turn_id,
            "input": payload,
            "active_agents": [intent["character_id"] for intent in intents],
            "retrieval_context": ctx.relevant_docs or [],
            "turn_outcome": outcome,
            "narrative": narrative,
            "generated_by": "openai_compatible_multi_agent",
            "mode": ctx.mode,
            "created_at": utc_now(),
        }

    def select_active_characters(self, ctx: DraftContext, orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
        active, _overflow = select_agent_participants(ctx, orders)
        return active

    def character_intent(self, ctx: DraftContext, char: dict[str, Any], orders: list[dict[str, Any]]) -> dict[str, Any]:
        cid = safe_id(char["character_id"])
        matching = next((order for order in orders if safe_id(order.get("character_id", "")) == cid), None)
        messages = [
            {"role": "system", "content": CHARACTER_AGENT_PROMPT},
            {"role": "system", "content": ctx.imported_preset[:6000]},
            {"role": "user", "content": self.json_prompt({
                "world_bible": ctx.world_bible[:5000],
                "dm_policy": ctx.dm_policy[:2000],
                "scene": ctx.scene,
                "location_state": ctx.location_state or {},
                "recent_events": ctx.recent_events[-5:],
                "relevant_docs": (ctx.relevant_docs or [])[:8],
                "character": char,
                "player_order": matching,
                "turn_input": ctx.input_payload,
                "output_schema": {
                    "character_id": cid,
                    "name": char.get("name", cid),
                    "source": "player_controlled|auto_agent",
                    "intent": "角色本轮想做什么",
                    "emotion": "当前情绪",
                    "dialogue_suggestion": "可选对白",
                    "needs_context": [],
                },
            })},
        ]
        result = self.client.chat_json("character", messages, temperature=0.8)
        return {
            "character_id": cid,
            "name": char.get("name", cid),
            "source": "player_controlled" if matching else "auto_agent",
            "intent": str(result.get("intent", "")).strip() or f"{char.get('name', cid)}观察局势。",
            "emotion": str(result.get("emotion", char.get("current_emotion", "平静"))).strip(),
            "dialogue_suggestion": str(result.get("dialogue_suggestion", "")).strip(),
            "needs_context": result.get("needs_context", []),
        }

    def npc_ensemble(self, ctx: DraftContext, chars: list[dict[str, Any]]) -> dict[str, Any]:
        messages = [
            {"role": "system", "content": NPC_ENSEMBLE_PROMPT},
            {"role": "system", "content": ctx.imported_preset[:6000]},
            {"role": "user", "content": self.json_prompt({
                "scene": ctx.scene,
                "location_state": ctx.location_state or {},
                "relevant_docs": (ctx.relevant_docs or [])[:8],
                "turn_input": ctx.input_payload,
                "characters": chars[:40],
                "output_schema": {
                    "used": True,
                    "summary": "溢出角色/路人群体本轮如何反应",
                    "intents": [{"character_id": "id", "intent": "简短意图"}],
                },
            })},
        ]
        result = self.client.chat_json("character", messages, temperature=0.8)
        return {
            "used": True,
            "summary": str(result.get("summary", "NPC Ensemble 处理了其余角色。")),
            "intents": result.get("intents", []),
        }

    def judge(self, ctx: DraftContext, intents: list[dict[str, Any]], npc_ensemble: dict[str, Any]) -> dict[str, Any]:
        old_dice = []
        if ctx.keep_dice and ctx.previous_candidate:
            old_dice = ctx.previous_candidate.get("turn_outcome", {}).get("dice", [])
        messages = [
            {"role": "system", "content": JUDGE_PROMPT},
            {"role": "user", "content": self.json_prompt({
                "world_bible": ctx.world_bible[:5000],
                "dm_policy": ctx.dm_policy[:2000],
                "scene": ctx.scene,
                "location_state": ctx.location_state or {},
                "relevant_docs": (ctx.relevant_docs or [])[:12],
                "rules": merged_rules(ctx.rules),
                "turn_input": ctx.input_payload,
                "character_intents": intents,
                "npc_ensemble": npc_ensemble,
                "preserved_dice": old_dice,
                "output_schema": {
                    "resolved_events": ["本轮事实"],
                    "dice": [],
                    "no_roll_reasons": [],
                    "dm_forced": [],
                    "character_results": [],
                    "scene": {},
                },
            })},
        ]
        result = self.client.chat_json("judge", messages, temperature=0.35)
        return self.normalize_judge_result(intents, result)

    def compose_outcome(self, ctx: DraftContext, intents: list[dict[str, Any]], npc_ensemble: dict[str, Any], judge: dict[str, Any]) -> dict[str, Any]:
        dice = judge.get("dice", [])
        no_roll_reasons = judge.get("no_roll_reasons", [])
        warnings = list(judge.get("validation_warnings", []))
        if ctx.keep_dice and ctx.previous_candidate:
            preserved = copy.deepcopy(ctx.previous_candidate.get("turn_outcome", {}).get("dice", []))
            dice = preserved
            if preserved:
                no_roll_reasons = []
            else:
                no_roll_reasons = copy.deepcopy(ctx.previous_candidate.get("turn_outcome", {}).get("no_roll_reasons", []))
                if not no_roll_reasons:
                    no_roll_reasons = ["保留上一候选的无骰子判定。"]
            warnings.append("preserved previous dice because keep_dice=true")
        resolved = judge.get("resolved_events") or []
        if not resolved:
            resolved = LocalFallbackGenerator().resolved_events(
                intents,
                ctx.input_payload.get("dm_directive", ""),
                dice,
                bool(judge.get("dm_forced")),
                ctx.input_payload.get("pace", "scene"),
                ctx.input_payload.get("divergence", "medium"),
            )
        character_results = judge.get("character_results") or [
            {
                "character_id": intent["character_id"],
                "experienced": intent["intent"],
                "emotion": intent.get("emotion", "平静"),
                "next_goal": "根据本轮结果调整下一步行动",
                "reason": "Judge 未返回角色结果，由意图补齐",
            }
            for intent in intents
        ]
        return {
            "resolved_events": resolved,
            "character_results": character_results,
            "character_intents": intents,
            "npc_ensemble": npc_ensemble,
            "dice": dice,
            "no_roll_reasons": no_roll_reasons,
            "dm_forced": judge.get("dm_forced", []),
            "scene": judge.get("scene", {}),
            "pace": ctx.input_payload.get("pace", "scene"),
            "divergence": ctx.input_payload.get("divergence", "medium"),
            "validation_warnings": warnings,
        }

    def writer(self, ctx: DraftContext, outcome: dict[str, Any]) -> str:
        messages = [
            {"role": "system", "content": WRITER_PROMPT},
            {"role": "system", "content": ctx.imported_preset[:8000]},
            {"role": "user", "content": self.json_prompt({
                "mode": ctx.mode,
                "world_bible": ctx.world_bible[:6000],
                "scene": ctx.scene,
                "location_state": ctx.location_state or {},
                "relevant_docs": (ctx.relevant_docs or [])[:12],
                "turn_outcome": outcome,
                "instruction": "输出第三人称中文小说正文。不要输出 JSON。不要改写 Turn Outcome 的事实骨架。",
            })},
        ]
        return self.client.chat("writer", messages, temperature=0.9).content.strip()

    def json_prompt(self, data: dict[str, Any]) -> str:
        return json.dumps(data, ensure_ascii=False, indent=2)

    def normalize_judge_result(self, intents: list[dict[str, Any]], result: dict[str, Any]) -> dict[str, Any]:
        warnings: list[str] = []
        normalized = dict(result)

        resolved = normalized.get("resolved_events", [])
        if not isinstance(resolved, list):
            warnings.append("resolved_events was not a list")
            resolved = [resolved]
        normalized["resolved_events"] = [str(item).strip() for item in resolved if str(item).strip()][:12]

        dice = normalized.get("dice", [])
        if not isinstance(dice, list):
            warnings.append("dice was not a list")
            dice = []
        normalized["dice"] = [self.normalize_dice(item, warnings) for item in dice if isinstance(item, dict)][:8]

        no_roll = normalized.get("no_roll_reasons", [])
        if not isinstance(no_roll, list):
            no_roll = [no_roll]
        normalized["no_roll_reasons"] = [str(item).strip() for item in no_roll if str(item).strip()][:8]
        if not normalized["dice"] and not normalized["no_roll_reasons"]:
            normalized["no_roll_reasons"] = ["Judge 未提供骰子或无需骰原因，系统按自然后果推进。"]
            warnings.append("missing no_roll_reasons")

        forced = normalized.get("dm_forced", [])
        if not isinstance(forced, list):
            forced = [forced]
        normalized["dm_forced"] = [
            item if isinstance(item, dict) else {"text": str(item), "random_roll_used": False}
            for item in forced
        ][:8]

        known_ids = {intent["character_id"] for intent in intents}
        results = normalized.get("character_results", [])
        if not isinstance(results, list):
            results = []
        clean_results = []
        for item in results:
            if not isinstance(item, dict):
                continue
            cid = safe_id(str(item.get("character_id", "")))
            if cid not in known_ids:
                warnings.append(f"ignored character_result for unknown character {cid}")
                continue
            clean_results.append({
                "character_id": cid,
                "experienced": str(item.get("experienced", "")).strip() or "参与了本轮事件。",
                "emotion": str(item.get("emotion", "")).strip() or "平静",
                "next_goal": str(item.get("next_goal", "")).strip() or "根据本轮结果调整下一步行动",
                "location": str(item.get("location", "")).strip(),
                "reason": str(item.get("reason", "")).strip() or "Judge 结果更新",
            })
        normalized["character_results"] = clean_results

        scene = normalized.get("scene", {})
        normalized["scene"] = scene if isinstance(scene, dict) else {}
        normalized["validation_warnings"] = warnings
        return normalized

    def normalize_dice(self, item: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
        allowed = {"critical_success", "success", "partial_success", "failure", "critical_failure"}
        try:
            difficulty = int(item.get("difficulty", 12))
            roll = int(item.get("roll", random.randint(1, 20)))
            bonus = int(item.get("bonus", 0))
        except (TypeError, ValueError):
            warnings.append("invalid dice numeric fields")
            difficulty, roll, bonus = 12, random.randint(1, 20), 0
        difficulty = max(2, min(30, difficulty))
        roll = max(1, min(20, roll))
        total = int(item.get("total", roll + bonus) or roll + bonus)
        outcome = str(item.get("outcome", "")).strip()
        if outcome not in allowed:
            if roll == 20:
                outcome = "critical_success"
            elif roll == 1:
                outcome = "critical_failure"
            elif total >= difficulty:
                outcome = "success"
            elif difficulty - total <= 3:
                outcome = "partial_success"
            else:
                outcome = "failure"
            warnings.append("normalized invalid dice outcome")
        return {
            "type": str(item.get("type", "d20")),
            "reason": str(item.get("reason", "本轮风险判定")),
            "difficulty": difficulty,
            "roll": roll,
            "bonus": bonus,
            "total": total,
            "outcome": outcome,
            "explanation": str(item.get("explanation", "Judge 未提供解释，系统按结果继续。")),
        }


CHARACTER_AGENT_PROMPT = """你是独立角色 Agent。只代表一个角色生成本轮意图。
你必须遵守角色 state、记忆、玩家本轮接管输入和角色已知信息。
你不能改写世界事实，不能替 Judge 裁定结果，不能让角色知道未提供给它的秘密。
只输出 JSON 对象。"""

NPC_ENSEMBLE_PROMPT = """你是 NPC Ensemble Agent，负责统一处理超过 8 个独立名额之外的角色和路人。
你可以给出群体反应和少数角色简短意图，但不能改写世界事实或裁定结果。
只输出 JSON 对象。"""

JUDGE_PROMPT = """你是干净的 Judge。你负责把角色意图和 DM 指令裁定成本轮事实骨架。
只有结果不确定且有代价时才掷 d20。DM 硬指令优先于随机。
如果不掷骰，必须给 no_roll_reasons。骰子结果必须包含 reason、difficulty、roll、bonus、total、outcome、explanation。
不要写小说。只输出 JSON 对象。"""

WRITER_PROMPT = """你是 Writer。你根据 Turn Outcome 写第三人称中文小说正文。
你可以吸收导入的酒馆预设和文风提示，可以自由补充低风险细节。
你不能改变 Judge 已裁定事实、角色位置/状态、骰子结果或 DM 硬约束。
只输出正文，不要输出 JSON 或解释。"""
