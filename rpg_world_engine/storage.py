from __future__ import annotations

import hashlib
import json
import re
import secrets
import shutil
import sqlite3
import zipfile
import base64
from io import BytesIO
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .rules import DEFAULT_DICE_RULES


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = Path(__import__("os").environ.get("RPG_WORLD_DATA", PROJECT_ROOT / "data"))
WORLDS_ROOT = DATA_ROOT / "worlds"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def append_markdown_block(path: Path, turn_id: str, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(f"\n<!-- turn:{turn_id}:start -->\n{content.rstrip()}\n<!-- turn:{turn_id}:end -->\n")


def upsert_markdown_block(path: Path, marker: str, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    pattern = re.compile(rf"\n?<!-- {re.escape(marker)}:start -->.*?<!-- {re.escape(marker)}:end -->\n?", re.DOTALL)
    new = pattern.sub("\n", old).rstrip()
    block = f"\n\n<!-- {marker}:start -->\n{content.rstrip()}\n<!-- {marker}:end -->\n"
    path.write_text(new + block, encoding="utf-8")


def remove_markdown_block(path: Path, turn_id: str) -> None:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(rf"\n?<!-- turn:{re.escape(turn_id)}:start -->.*?<!-- turn:{re.escape(turn_id)}:end -->\n?", re.DOTALL)
    path.write_text(pattern.sub("\n", text), encoding="utf-8")


def remove_jsonl_by_source_turn(path: Path, turn_id: str) -> None:
    if not path.exists():
        return
    kept = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            kept.append(line)
            continue
        if row.get("source_turn") != turn_id:
            kept.append(json.dumps(row, ensure_ascii=False))
    path.write_text(("\n".join(kept) + "\n") if kept else "", encoding="utf-8")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def safe_id(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"\s+", "-", value)
    value = re.sub(r"[^a-z0-9_\-\u4e00-\u9fff]+", "", value)
    return value[:80] or f"id-{secrets.token_hex(4)}"


def compact_query(value: str, limit: int = 80) -> str:
    value = re.sub(r"[^\w\u4e00-\u9fff]+", " ", value, flags=re.UNICODE)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:limit]


def new_world_id(name: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"w-{stamp}-{safe_id(name)[:24]}-{secrets.token_hex(2)}"


def hash_password(password: str) -> dict[str, str]:
    salt = secrets.token_hex(16)
    digest = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
    return {"salt": salt, "hash": digest}


def verify_password(stored: dict[str, str] | None, password: str) -> bool:
    if not stored:
        return True
    digest = hashlib.sha256((stored["salt"] + password).encode("utf-8")).hexdigest()
    return secrets.compare_digest(digest, stored["hash"])


def public_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    clean = dict(manifest)
    clean.pop("password", None)
    return clean


def should_skip_archive_path(rel: Path) -> bool:
    return any(part in {".cache", "drafts"} for part in rel.parts)


def archive_member_parts(name: str) -> list[str]:
    parts = []
    for part in Path(name).parts:
        if part in {"", ".", "/"}:
            continue
        if part == "..":
            raise ValueError("archive contains parent path")
        parts.append(part)
    return parts


@dataclass(frozen=True)
class WorldRef:
    world_id: str
    branch_id: str | None = None

    @classmethod
    def parse(cls, raw: str) -> "WorldRef":
        if "::" in raw:
            world_id, branch_id = raw.split("::", 1)
            return cls(world_id, branch_id or None)
        return cls(raw, None)

    @property
    def key(self) -> str:
        return f"{self.world_id}::{self.branch_id}" if self.branch_id else self.world_id


class WorldStore:
    def __init__(self, root: Path = WORLDS_ROOT):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def world_root(self, world_id: str) -> Path:
        candidate = self.root / safe_id(world_id)
        if candidate.exists():
            return candidate
        direct = self.root / world_id
        if direct.exists() and direct.resolve().is_relative_to(self.root.resolve()):
            return direct
        raise FileNotFoundError(world_id)

    def branch_root(self, ref: WorldRef) -> Path:
        root = self.world_root(ref.world_id)
        if not ref.branch_id:
            return root
        branch = root / "branches" / safe_id(ref.branch_id)
        if not branch.exists():
            raise FileNotFoundError(ref.key)
        return branch

    def current_dir(self, ref: WorldRef) -> Path:
        return self.branch_root(ref) / "current"

    def manifest_path(self, ref: WorldRef) -> Path:
        return self.branch_root(ref) / "manifest.json"

    def read_manifest(self, ref: WorldRef) -> dict[str, Any]:
        return read_json(self.manifest_path(ref), {})

    def write_manifest(self, ref: WorldRef, manifest: dict[str, Any]) -> None:
        write_json(self.manifest_path(ref), manifest)

    def read_rules(self, ref: WorldRef) -> dict[str, Any]:
        rules_path = self.current_dir(ref) / "rules" / "dice_rules.json"
        if not rules_path.exists():
            write_json(rules_path, DEFAULT_DICE_RULES)
        rules = read_json(rules_path, DEFAULT_DICE_RULES)
        return rules if isinstance(rules, dict) else DEFAULT_DICE_RULES

    def read_location_state(self, ref: WorldRef, location_id: str) -> dict[str, Any]:
        current = self.current_dir(ref)
        location = str(location_id or "未定地点")
        path = current / "locations" / f"{safe_id(location)}.json"
        if not path.exists():
            write_json(path, {
                "location_id": location,
                "name": location,
                "summary": "尚未记录地点状态。",
                "visible_objects": [],
                "open_conflicts": [],
                "recent_turns": [],
                "last_updated_turn": None,
            })
        state = read_json(path, {})
        return state if isinstance(state, dict) else {}

    def update_location_state(self, ref: WorldRef, scene: dict[str, Any], turn_id: str, summary: str = "") -> dict[str, Any]:
        current = self.current_dir(ref)
        location = str(scene.get("location_id") or "未定地点")
        path = current / "locations" / f"{safe_id(location)}.json"
        state = self.read_location_state(ref, location)
        state["location_id"] = location
        state["name"] = state.get("name") or location
        state["last_scene_id"] = scene.get("scene_id")
        state["current_focus"] = scene.get("focus", state.get("current_focus", ""))
        if isinstance(scene.get("visible_objects"), list):
            state["visible_objects"] = scene.get("visible_objects", [])
        if isinstance(scene.get("open_conflicts"), list):
            state["open_conflicts"] = scene.get("open_conflicts", [])
        recent = state.get("recent_turns", [])
        if not isinstance(recent, list):
            recent = []
        recent.append({
            "turn_id": turn_id,
            "scene_id": scene.get("scene_id"),
            "focus": scene.get("focus"),
            "summary": summary,
            "at": utc_now(),
        })
        state["recent_turns"] = recent[-12:]
        state["last_updated_turn"] = turn_id
        write_json(path, state)
        return state

    def list_worlds(self) -> list[dict[str, Any]]:
        worlds: list[dict[str, Any]] = []
        for path in sorted(self.root.iterdir() if self.root.exists() else []):
            if not path.is_dir() or not (path / "manifest.json").exists():
                continue
            manifest = public_manifest(read_json(path / "manifest.json", {}))
            branches = []
            for branch in sorted((path / "branches").iterdir() if (path / "branches").exists() else []):
                if branch.is_dir() and (branch / "manifest.json").exists():
                    branch_manifest = public_manifest(read_json(branch / "manifest.json", {}))
                    branch_manifest["ref"] = f"{manifest.get('world_id')}::{branch_manifest.get('branch_id')}"
                    branches.append(branch_manifest)
            manifest["branches"] = branches
            worlds.append(manifest)
        return worlds

    def create_world(self, display_name: str, subtitle: str = "", password: str = "") -> dict[str, Any]:
        world_id = new_world_id(display_name)
        root = self.root / world_id
        current = root / "current"
        for sub in [
            "characters",
            "locations",
            "scenes",
            "events",
            "memory/truth",
            "memory/visible",
            "memory/beliefs/characters",
            "memory/patches",
            "dm",
            "prompts",
            "rules",
            "drafts/turns",
            "revisions",
            "branches",
            "imports/raw",
            "imports/reports",
            "assets/characters",
            ".cache",
        ]:
            (root / sub if sub.startswith(("drafts", "revisions", "branches", "imports", "assets", ".cache")) else current / sub).mkdir(parents=True, exist_ok=True)

        locked = bool(password)
        manifest = {
            "world_id": world_id,
            "display_name": display_name,
            "subtitle": subtitle,
            "cover_image": "",
            "owner_name": "本机玩家",
            "locked": locked,
            "password": hash_password(password) if locked else None,
            "created_at": utc_now(),
            "last_played_at": utc_now(),
            "storage_version": 1,
            "current_revision": "000001.create",
            "setup_review_required": False,
            "generation_style": {
                "default_pace": "scene",
                "default_divergence": "medium",
                "writer_freedom": "high",
            },
            "agent_budget": {
                "max_independent_character_agents": 8,
                "max_controlled_characters_per_turn": 3,
                "overflow_strategy": "npc_ensemble",
                "independent_review_threshold": 16,
            },
        }
        write_json(root / "manifest.json", manifest)
        (current / "world_bible.md").write_text(
            f"# {display_name}\n\n{subtitle or '一个尚未展开的互动文字 RPG 世界。'}\n",
            encoding="utf-8",
        )
        (current / "dm_policy.md").write_text(
            "# DM 协议\n\n"
            "- DM 硬指令优先于随机判定。\n"
            "- 只有结果不确定且有代价时才需要骰子。\n"
            "- 推进级别和发散程度会影响风险与机会的触发概率。\n"
            "- 已接受事件不能被后续随机判定推翻。\n",
            encoding="utf-8",
        )
        (current / "prompts/imported_preset.md").write_text(
            "# 导入预设\n\n尚未导入酒馆预设。导入后默认作用于 Character Agent、NPC Ensemble 和 Writer。\n",
            encoding="utf-8",
        )
        write_json(current / "rules" / "dice_rules.json", DEFAULT_DICE_RULES)
        write_json(current / "locations" / f"{safe_id('未定地点')}.json", {
            "location_id": "未定地点",
            "name": "未定地点",
            "summary": "开场位置尚未确定。",
            "visible_objects": [],
            "open_conflicts": [],
            "recent_turns": [],
            "last_updated_turn": None,
        })
        write_json(current / "scenes/current_scene.json", {
            "scene_id": "scene_000001",
            "previous_scene_id": None,
            "location_id": "未定地点",
            "title": "开场",
            "participants": [],
            "focus": "等待玩家输入第一轮行动。",
            "open_conflicts": [],
            "visible_objects": [],
            "continuity_locks": [],
            "started_turn": None,
            "last_updated_turn": None,
        })
        write_json(current / "scenes/previous_scene.json", {})
        (current / "scenes/scene_log.jsonl").touch()
        (current / "memory/truth/facts.jsonl").touch()
        (current / "memory/visible/player_journal.md").write_text(f"# {display_name} 玩家日志\n", encoding="utf-8")
        (current / "dm/active_directives.jsonl").touch()
        (current / "dm/resolved_directives.jsonl").touch()
        self.snapshot(WorldRef(world_id), "create")
        self.rebuild_index(WorldRef(world_id))
        return public_manifest(manifest)

    def update_manifest_public(self, ref: WorldRef, updates: dict[str, Any]) -> dict[str, Any]:
        manifest = self.read_manifest(ref)
        for key in ["display_name", "subtitle", "cover_image", "owner_name", "locked"]:
            if key in updates:
                manifest[key] = updates[key]
        if "password" in updates and updates["password"]:
            manifest["locked"] = True
            manifest["password"] = hash_password(updates["password"])
        manifest["last_played_at"] = utc_now()
        self.write_manifest(ref, manifest)
        return public_manifest(manifest)

    def set_setup_review_required(self, ref: WorldRef, required: bool) -> dict[str, Any]:
        manifest = self.read_manifest(ref)
        manifest["setup_review_required"] = required
        manifest["last_played_at"] = utc_now()
        self.write_manifest(ref, manifest)
        return public_manifest(manifest)

    def complete_import_review(self, ref: WorldRef, note: str = "") -> dict[str, Any]:
        root = self.world_root(ref.world_id)
        session_path = root / "imports" / "reports" / "review-session.json"
        session = read_json(session_path, None) or self.build_import_review_session(ref)
        applied = self.apply_import_review_answers(ref, session, note)
        append_jsonl(root / "imports" / "reports" / "import-decisions.jsonl", {
            "at": utc_now(),
            "branch": ref.branch_id,
            "note": note,
            "action": "review_complete",
            "applied_paths": applied,
        })
        if session:
            session["status"] = "complete"
            session["completed_at"] = utc_now()
            session["completion_note"] = note
            session["applied_paths"] = applied
            session["updated_at"] = utc_now()
            write_json(session_path, session)
        manifest = self.set_setup_review_required(ref, False)
        revision = self.snapshot(ref, "review-complete")
        index = self.rebuild_index(ref)
        return {"review_complete": True, "manifest": manifest, "revision": revision, "applied_paths": applied, "index": index}

    def apply_import_review_answers(self, ref: WorldRef, session: dict[str, Any], note: str = "") -> list[str]:
        current = self.current_dir(ref)
        root = self.world_root(ref.world_id)
        questions = [question for question in session.get("questions", []) if question.get("answer")]
        by_report: dict[str, list[dict[str, Any]]] = {}
        for question in questions:
            report_name = Path(str(question.get("report", ""))).name
            if report_name:
                by_report.setdefault(report_name, []).append(question)

        report_names = set(by_report)
        if note:
            for report_file in self.import_report_files(ref):
                report_names.add(report_file.name)

        applied: list[str] = []
        overview_lines = [
            "# 导入核对决策",
            "",
            f"- 更新时间：{utc_now()}",
        ]
        if note.strip():
            overview_lines.extend(["", "## 完成备注", "", note.strip()])

        for report_name in sorted(report_names):
            report_path = root / "imports" / "reports" / report_name
            report = read_json(report_path, {})
            answers = by_report.get(report_name, [])
            if not report:
                continue
            block = self.render_import_review_block(report, answers, note)
            marker = f"import-review:{safe_id(report_name)}"
            for target in self.import_review_targets(ref, report):
                upsert_markdown_block(target, marker, block)
                rel = target.relative_to(current).as_posix()
                if rel not in applied:
                    applied.append(rel)
            overview_lines.extend(["", f"## {report.get('source_name', report_name)}"])
            overview_lines.append(f"- 类型：{report.get('kind', 'unknown')}")
            for item in answers:
                overview_lines.append(f"- {item.get('question', '')}：{item.get('answer', '')}")

        overview_path = current / "import_review_notes.md"
        overview_marker = "import-review:summary"
        upsert_markdown_block(overview_path, overview_marker, "\n".join(overview_lines))
        overview_rel = overview_path.relative_to(current).as_posix()
        if overview_rel not in applied:
            applied.append(overview_rel)
        return applied

    def render_import_review_block(self, report: dict[str, Any], answers: list[dict[str, Any]], note: str = "") -> str:
        lines = [
            f"## 导入核对决策：{report.get('source_name', '未命名导入')}",
            "",
            f"- 类型：{report.get('kind', 'unknown')}",
            f"- 原始文件：{report.get('raw_path', 'unknown')}",
            f"- 应用时间：{utc_now()}",
        ]
        if note.strip():
            lines.extend(["", "### 完成备注", "", note.strip()])
        if answers:
            lines.extend(["", "### 已回答问题"])
            for item in answers:
                lines.append(f"- 问：{item.get('question', '')}")
                lines.append(f"  答：{item.get('answer', '')}")
        return "\n".join(lines)

    def import_review_targets(self, ref: WorldRef, report: dict[str, Any]) -> list[Path]:
        current = self.current_dir(ref)
        kind = report.get("kind", "")
        targets: list[Path] = []
        if kind == "character":
            created = report.get("created", {}) if isinstance(report.get("created", {}), dict) else {}
            character_id = safe_id(str(created.get("character_id") or created.get("name") or report.get("source_name", "")))
            target = current / "characters" / character_id / "profile.md"
            if target.exists():
                targets.append(target)
        elif kind == "preset":
            targets.append(current / "prompts" / "imported_preset.md")
        elif kind == "world":
            targets.append(current / "world_bible.md")
        elif kind == "lore":
            for changed in report.get("changed_paths", []):
                rel = str(changed)
                if rel.startswith("current/lore/") and rel.endswith(".md"):
                    target = current / rel.removeprefix("current/")
                    if target.exists():
                        targets.append(target)
        if not targets:
            for changed in report.get("changed_paths", []):
                rel = str(changed)
                if rel.startswith("current/") and rel.endswith((".md", ".json", ".jsonl")):
                    target = current / rel.removeprefix("current/")
                    if target.exists() and target.suffix.lower() == ".md":
                        targets.append(target)
                        break
        return targets or [current / "import_review_notes.md"]

    def import_report_files(self, ref: WorldRef) -> list[Path]:
        root = self.world_root(ref.world_id) / "imports" / "reports"
        files = []
        for file in sorted(root.glob("*.json") if root.exists() else []):
            if file.name == "review-session.json":
                continue
            files.append(file)
        return files

    def build_import_review_session(self, ref: WorldRef, force: bool = False) -> dict[str, Any]:
        root = self.world_root(ref.world_id)
        session_path = root / "imports" / "reports" / "review-session.json"
        existing = read_json(session_path, None)
        if existing and existing.get("status") == "active" and not force:
            return existing
        previous_answers = {}
        if existing:
            for question in existing.get("questions", []):
                if question.get("answer"):
                    previous_answers[question.get("id")] = question
        questions = []
        for report_file in self.import_report_files(ref):
            report = read_json(report_file, {})
            for index, question in enumerate(report.get("questions", []), start=1):
                question_id = f"{safe_id(report_file.stem)}-{index}"
                previous = previous_answers.get(question_id, {})
                questions.append({
                    "id": question_id,
                    "report": report_file.name,
                    "kind": report.get("kind", ""),
                    "source_name": report.get("source_name", ""),
                    "question": question,
                    "answer": previous.get("answer", ""),
                    "answered_at": previous.get("answered_at"),
                })
        first_unanswered = next((idx for idx, item in enumerate(questions) if not item.get("answer")), len(questions))
        session = {
            "status": "active" if first_unanswered < len(questions) else "complete",
            "current_index": first_unanswered,
            "questions": questions,
            "created_at": utc_now(),
            "updated_at": utc_now(),
        }
        write_json(session_path, session)
        return session

    def get_import_review_session(self, ref: WorldRef) -> dict[str, Any]:
        root = self.world_root(ref.world_id)
        session_path = root / "imports" / "reports" / "review-session.json"
        session = read_json(session_path, None)
        if not session:
            session = self.build_import_review_session(ref)
        return session

    def answer_import_review_question(self, ref: WorldRef, answer: str) -> dict[str, Any]:
        root = self.world_root(ref.world_id)
        session_path = root / "imports" / "reports" / "review-session.json"
        session = self.build_import_review_session(ref, force=True)
        questions = session.get("questions", [])
        index = int(session.get("current_index", 0))
        if not questions:
            session["status"] = "complete"
        elif index >= len(questions):
            session["status"] = "complete"
        else:
            questions[index]["answer"] = answer
            questions[index]["answered_at"] = utc_now()
            append_jsonl(root / "imports" / "reports" / "import-decisions.jsonl", {
                "at": utc_now(),
                "branch": ref.branch_id,
                "action": "review_answer",
                "question": questions[index]["question"],
                "answer": answer,
                "report": questions[index].get("report", ""),
                "source_name": questions[index].get("source_name", ""),
                "kind": questions[index].get("kind", ""),
            })
            index += 1
            session["current_index"] = index
            session["status"] = "complete" if index >= len(questions) else "active"
        session["updated_at"] = utc_now()
        write_json(session_path, session)
        return session

    def create_character(self, ref: WorldRef, data: dict[str, Any]) -> dict[str, Any]:
        name = data.get("name", "").strip() or "未命名角色"
        character_id = safe_id(data.get("character_id") or name)
        root = self.current_dir(ref) / "characters" / character_id
        root.mkdir(parents=True, exist_ok=True)
        profile = data.get("profile", "").strip() or f"{name} 的人设尚未完善。"
        (root / "profile.md").write_text(f"# {name}\n\n{profile}\n", encoding="utf-8")
        (root / "profile_summary.md").write_text(data.get("profile_summary", profile[:600]), encoding="utf-8")
        state = {
            "character_id": character_id,
            "name": name,
            "role_type": data.get("role_type", "controllable"),
            "agent_mode": data.get("agent_mode", "independent_default"),
            "agent_enabled": True,
            "location": data.get("location", "未定地点"),
            "outfit": data.get("outfit", "未记录"),
            "visible_appearance": data.get("visible_appearance", "未记录"),
            "injuries": data.get("injuries", []),
            "items": data.get("items", []),
            "current_emotion": data.get("current_emotion", "平静"),
            "current_goal": data.get("current_goal", "等待局势推进"),
            "availability": "active",
            "last_updated_turn": None,
            "custom": data.get("custom", {}),
        }
        write_json(root / "state.json", state)
        (root / "memory.md").write_text(f"# {name} 记忆\n\n暂无长期记忆。\n", encoding="utf-8")
        (root / "beliefs.jsonl").touch()
        write_json(root / "relationships.json", {})
        return self.character_summary(root)

    def list_characters(self, ref: WorldRef) -> list[dict[str, Any]]:
        chars = []
        base = self.current_dir(ref) / "characters"
        for path in sorted(base.iterdir() if base.exists() else []):
            if path.is_dir() and (path / "state.json").exists():
                chars.append(self.character_summary(path))
        return chars

    def character_summary(self, path: Path) -> dict[str, Any]:
        state = read_json(path / "state.json", {})
        profile = (path / "profile_summary.md").read_text(encoding="utf-8") if (path / "profile_summary.md").exists() else ""
        return {
            "character_id": state.get("character_id", path.name),
            "name": state.get("name", path.name),
            "role_type": state.get("role_type", "controllable"),
            "agent_mode": state.get("agent_mode", "independent_default"),
            "agent_enabled": state.get("agent_enabled", True),
            "availability": state.get("availability", "active"),
            "location": state.get("location", "未定地点"),
            "current_goal": state.get("current_goal", ""),
            "current_emotion": state.get("current_emotion", ""),
            "summary": profile[:400],
            "state": state,
        }

    def agent_pool_status(self, ref: WorldRef) -> dict[str, Any]:
        manifest = self.read_manifest(ref)
        budget = manifest.get("agent_budget", {}) if isinstance(manifest.get("agent_budget", {}), dict) else {}
        max_active = int(budget.get("max_independent_character_agents", 8) or 8)
        review_threshold = int(budget.get("independent_review_threshold", 16) or 16)
        independent_modes = {"independent_default", "independent_forced", "independent"}
        cold_availability = {"dormant", "cold", "offstage", "archived"}
        characters = self.list_characters(ref)
        independent_profiles = [
            char
            for char in characters
            if char.get("agent_enabled", True) and char.get("agent_mode", "independent_default") in independent_modes
        ]
        active_independent = [
            char
            for char in independent_profiles
            if char.get("availability", "active") not in cold_availability
        ]
        npc_ensemble = [
            char
            for char in characters
            if char.get("agent_enabled", True) and char.get("agent_mode") in {"npc_ensemble", "grouped_npc"}
        ]
        dormant = [char for char in characters if char.get("availability") in cold_availability or not char.get("agent_enabled", True)]
        return {
            "total_characters": len(characters),
            "max_active_independent": max_active,
            "independent_review_threshold": review_threshold,
            "independent_profiles": len(independent_profiles),
            "active_independent": len(active_independent),
            "npc_ensemble": len(npc_ensemble),
            "dormant": len(dormant),
            "over_turn_budget": len(active_independent) > max_active,
            "review_required": len(independent_profiles) >= review_threshold,
        }

    def update_character_agent(self, ref: WorldRef, character_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        current = self.current_dir(ref)
        char_dir = current / "characters" / safe_id(character_id)
        state_path = char_dir / "state.json"
        if not state_path.exists():
            raise FileNotFoundError(character_id)
        state = read_json(state_path, {})
        allowed_values = {
            "role_type": {"controllable", "major_npc", "background_npc", "lore_entity"},
            "agent_mode": {"independent_default", "independent_forced", "npc_ensemble", "dormant", "disabled"},
            "availability": {"active", "offstage", "dormant", "archived"},
        }
        for field, choices in allowed_values.items():
            if field in updates:
                value = str(updates[field]).strip()
                if value not in choices:
                    raise ValueError(f"invalid {field}: {value}")
                state[field] = value
        if "agent_enabled" in updates:
            raw = updates["agent_enabled"]
            if isinstance(raw, str):
                state["agent_enabled"] = raw.lower() in {"1", "true", "yes", "on"}
            else:
                state["agent_enabled"] = bool(raw)
        state["agent_updated_at"] = utc_now()
        write_json(state_path, state)
        revision = self.snapshot(ref, "character-agent")
        index = self.rebuild_index(ref)
        summary = self.character_summary(char_dir)
        return {"saved": True, "character": summary, "agent_pool": self.agent_pool_status(ref), "revision": revision, "index": index}

    def next_turn_id(self, ref: WorldRef) -> str:
        events = self.current_dir(ref) / "events"
        max_id = 0
        for file in events.glob("*.turn.json"):
            try:
                max_id = max(max_id, int(file.name.split(".", 1)[0]))
            except ValueError:
                continue
        return f"{max_id + 1:06d}"

    def next_candidate_id(self, ref: WorldRef, turn_id: str) -> str:
        folder = self.branch_root(ref) / "drafts" / "turns" / turn_id
        folder.mkdir(parents=True, exist_ok=True)
        nums = []
        for file in folder.glob("candidate-*.json"):
            try:
                nums.append(int(file.stem.split("-")[1]))
            except (IndexError, ValueError):
                pass
        return f"candidate-{(max(nums) + 1) if nums else 1:03d}"

    def save_draft(self, ref: WorldRef, draft: dict[str, Any]) -> dict[str, Any]:
        turn_id = draft["turn_id"]
        candidate_id = self.next_candidate_id(ref, turn_id)
        draft["candidate_id"] = candidate_id
        draft["status"] = "draft"
        draft["created_at"] = utc_now()
        folder = self.branch_root(ref) / "drafts" / "turns" / turn_id
        write_json(folder / f"{candidate_id}.json", draft)
        candidates = sorted(folder.glob("candidate-*.json"))
        while len(candidates) > 5:
            candidates[0].unlink(missing_ok=True)
            candidates = sorted(folder.glob("candidate-*.json"))
        write_json(folder / "meta.json", {"turn_id": turn_id, "updated_at": utc_now()})
        return draft

    def read_candidate(self, ref: WorldRef, turn_id: str, candidate_id: str) -> dict[str, Any]:
        return read_json(self.branch_root(ref) / "drafts" / "turns" / turn_id / f"{candidate_id}.json", {})

    def recent_events(self, ref: WorldRef, limit: int = 20) -> list[dict[str, Any]]:
        files = sorted((self.current_dir(ref) / "events").glob("*.turn.json"))
        return [read_json(file, {}) for file in files[-limit:]]

    def list_dm_directives(self, ref: WorldRef, limit: int = 20) -> list[dict[str, Any]]:
        rows = read_jsonl(self.current_dir(ref) / "dm" / "resolved_directives.jsonl")
        return rows[-limit:]

    def accept_candidate(self, ref: WorldRef, turn_id: str, candidate_id: str) -> dict[str, Any]:
        draft = self.read_candidate(ref, turn_id, candidate_id)
        if not draft:
            raise FileNotFoundError(f"{turn_id}/{candidate_id}")
        draft["status"] = "accepted"
        draft["accepted_at"] = utc_now()
        current = self.current_dir(ref)
        write_json(current / "events" / f"{turn_id}.turn.json", draft)
        patch = self.apply_outcome(ref, draft)
        write_json(current / "memory" / "patches" / f"{turn_id}.memory_patch.json", patch)
        drafts = self.branch_root(ref) / "drafts" / "turns" / turn_id
        shutil.rmtree(drafts, ignore_errors=True)
        manifest = self.read_manifest(ref)
        manifest["last_played_at"] = utc_now()
        self.write_manifest(ref, manifest)
        revision = self.snapshot(ref, "turn")
        self.rebuild_index(ref)
        return {"accepted": True, "turn_id": turn_id, "revision": revision, "patch": patch}

    def apply_outcome(self, ref: WorldRef, draft: dict[str, Any]) -> dict[str, Any]:
        from .memory import MemoryWriter

        current = self.current_dir(ref)
        turn_id = draft["turn_id"]
        plan = MemoryWriter().build_plan(draft, self.list_characters(ref))
        patch = {
            "turn_id": turn_id,
            "memory_writer": {
                "generated_by": plan.get("generated_by", "unknown"),
                "warnings": plan.get("warnings", []),
            },
            "writes": [],
            "state_changes": [],
        }

        summary_lines = [f"- {item.get('text', '').strip()}" for item in plan.get("visible_updates", []) if item.get("text", "").strip()]
        if summary_lines:
            append_markdown_block(current / "memory" / "visible" / "player_journal.md", turn_id, "\n".join(summary_lines))
            patch["writes"].append({"target": "memory/visible/player_journal.md", "operation": "append_markdown_block", "source_turn": turn_id})

        for fact in plan.get("truth_updates", []):
            claim = str(fact.get("claim", "")).strip()
            if not claim:
                continue
            append_jsonl(current / "memory" / "truth" / "facts.jsonl", {
                "claim": claim,
                "source_turn": turn_id,
                "status": fact.get("status", "active"),
                "visibility": fact.get("visibility", "visible"),
            })
        if plan.get("truth_updates"):
            patch["writes"].append({"target": "memory/truth/facts.jsonl", "operation": "append_jsonl_source_turn", "source_turn": turn_id})

        for result in plan.get("character_memory_updates", []):
            char_id = result.get("character_id")
            if not char_id:
                continue
            char_dir = current / "characters" / safe_id(char_id)
            if not char_dir.exists():
                continue
            memory_line = f"- {str(result.get('memory', '')).strip() or '经历了本轮事件。'}"
            append_markdown_block(char_dir / "memory.md", turn_id, memory_line)
            patch["writes"].append({"target": f"characters/{safe_id(char_id)}/memory.md", "operation": "append_markdown_block", "source_turn": turn_id})

        for change in plan.get("state_changes", []):
            char_id = change.get("character_id")
            field = change.get("field")
            if not char_id or not field:
                continue
            char_dir = current / "characters" / safe_id(char_id)
            state_path = char_dir / "state.json"
            if not state_path.exists():
                continue
            state_path = char_dir / "state.json"
            state = read_json(state_path, {})
            value = change.get("value")
            old = state.get(field)
            state[field] = value
            patch["state_changes"].append({
                "target": f"characters/{safe_id(char_id)}/state.json",
                "field": field,
                "op": "set",
                "old": old,
                "value": value,
                "reason": change.get("reason", "Memory Writer 更新"),
                "source_turn": turn_id,
            })
            state["last_updated_turn"] = turn_id
            write_json(state_path, state)

        scene = read_json(current / "scenes" / "current_scene.json", {})
        previous_scene = dict(scene)
        scene_update = plan.get("scene_update") if isinstance(plan.get("scene_update"), dict) else {}
        write_json(current / "scenes" / "previous_scene.json", previous_scene)
        if scene_update:
            scene_update.setdefault("scene_id", scene.get("scene_id", "scene_000001"))
            scene_update.setdefault("previous_scene_id", scene.get("scene_id"))
            scene.update(scene_update)
        scene["last_updated_turn"] = turn_id
        write_json(current / "scenes" / "current_scene.json", scene)
        append_jsonl(current / "scenes" / "scene_log.jsonl", {
            "turn_id": turn_id,
            "at": utc_now(),
            "before": {
                "scene_id": previous_scene.get("scene_id"),
                "title": previous_scene.get("title"),
                "location_id": previous_scene.get("location_id"),
                "focus": previous_scene.get("focus"),
            },
            "after": {
                "scene_id": scene.get("scene_id"),
                "title": scene.get("title"),
                "location_id": scene.get("location_id"),
                "focus": scene.get("focus"),
            },
            "updated_fields": sorted(scene_update.keys()),
        })
        location_id = str(scene.get("location_id") or "未定地点")
        location_target = f"locations/{safe_id(location_id)}.json"
        old_location_state = read_json(current / location_target, None)
        location_state = self.update_location_state(
            ref,
            scene,
            turn_id,
            (draft.get("turn_outcome", {}).get("resolved_events", []) or [""])[0],
        )
        patch["writes"].append({
            "target": location_target,
            "operation": "update_location_state",
            "old": old_location_state,
            "value": location_state,
            "source_turn": turn_id,
        })

        dm_directive = str(draft.get("input", {}).get("dm_directive", "")).strip()
        if dm_directive:
            outcome = draft.get("turn_outcome", {}) if isinstance(draft.get("turn_outcome", {}), dict) else {}
            dice = outcome.get("dice", []) if isinstance(outcome.get("dice", []), list) else []
            forced = outcome.get("dm_forced", []) if isinstance(outcome.get("dm_forced", []), list) else []
            append_jsonl(current / "dm" / "resolved_directives.jsonl", {
                "turn_id": turn_id,
                "source_turn": turn_id,
                "at": utc_now(),
                "directive": dm_directive,
                "status": "resolved",
                "mode": draft.get("mode", "new"),
                "forced": bool(forced),
                "random_roll_used": bool(dice),
                "dice": dice,
                "summary": (outcome.get("resolved_events", []) or [""])[0],
            })
            patch["writes"].append({"target": "dm/resolved_directives.jsonl", "operation": "append_jsonl_source_turn", "source_turn": turn_id})
        return patch

    def revert_memory_patch(self, ref: WorldRef, turn_id: str) -> dict[str, Any]:
        current = self.current_dir(ref)
        patch_path = current / "memory" / "patches" / f"{turn_id}.memory_patch.json"
        patch = read_json(patch_path, None)
        if not patch:
            raise FileNotFoundError(turn_id)
        reverted = []
        for write in patch.get("writes", []):
            target = current / write.get("target", "")
            operation = write.get("operation")
            if operation == "append_markdown_block":
                remove_markdown_block(target, turn_id)
                reverted.append({"target": write.get("target"), "operation": "remove_markdown_block"})
            elif operation == "append_jsonl_source_turn":
                remove_jsonl_by_source_turn(target, turn_id)
                reverted.append({"target": write.get("target"), "operation": "remove_jsonl_source_turn"})
            elif operation == "update_location_state":
                old = write.get("old")
                if old is None:
                    target.unlink(missing_ok=True)
                    reverted.append({"target": write.get("target"), "operation": "delete_created_location_state"})
                else:
                    write_json(target, old)
                    reverted.append({"target": write.get("target"), "operation": "restore_location_state"})
        for change in reversed(patch.get("state_changes", [])):
            target = current / change.get("target", "")
            if not target.exists():
                continue
            state = read_json(target, {})
            field = change.get("field")
            if field:
                state[field] = change.get("old")
                write_json(target, state)
                reverted.append({"target": change.get("target"), "field": field, "operation": "restore_old"})
        patch_path.unlink(missing_ok=True)
        revision = self.snapshot(ref, "patch-revert")
        index = self.rebuild_index(ref)
        return {"reverted": True, "turn_id": turn_id, "changes": reverted, "revision": revision, "index": index}

    def snapshot(self, ref: WorldRef, kind: str) -> str:
        branch = self.branch_root(ref)
        rev_root = branch / "revisions"
        rev_root.mkdir(parents=True, exist_ok=True)
        existing = []
        for item in rev_root.iterdir():
            if item.is_dir():
                try:
                    existing.append(int(item.name.split(".", 1)[0]))
                except ValueError:
                    pass
        rev_num = max(existing) + 1 if existing else 1
        revision_id = f"{rev_num:06d}.{safe_id(kind)}"
        dst = rev_root / revision_id / "current"
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(branch / "current", dst)
        manifest = self.read_manifest(ref)
        manifest["current_revision"] = revision_id
        manifest["last_played_at"] = utc_now()
        self.write_manifest(ref, manifest)
        return revision_id

    def list_revisions(self, ref: WorldRef) -> list[dict[str, Any]]:
        rev_root = self.branch_root(ref) / "revisions"
        revisions = []
        for item in sorted(rev_root.iterdir() if rev_root.exists() else []):
            if not item.is_dir() or not (item / "current").exists():
                continue
            try:
                number = int(item.name.split(".", 1)[0])
            except ValueError:
                number = 0
            stat = item.stat()
            event_count = len(list((item / "current" / "events").glob("*.turn.json")))
            revisions.append({
                "revision_id": item.name,
                "number": number,
                "kind": item.name.split(".", 1)[1] if "." in item.name else "",
                "event_count": event_count,
                "updated_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            })
        return revisions

    def rollback_to_revision(self, ref: WorldRef, revision_id: str) -> dict[str, Any]:
        branch = self.branch_root(ref)
        rev_root = branch / "revisions"
        target = rev_root / revision_id / "current"
        if not target.exists():
            raise FileNotFoundError(revision_id)
        try:
            target_num = int(revision_id.split(".", 1)[0])
        except ValueError:
            raise ValueError("invalid revision id") from None

        current = branch / "current"
        if current.exists():
            shutil.rmtree(current)
        shutil.copytree(target, current)

        for item in list(rev_root.iterdir()):
            if not item.is_dir():
                continue
            try:
                number = int(item.name.split(".", 1)[0])
            except ValueError:
                continue
            if number > target_num:
                shutil.rmtree(item)

        shutil.rmtree(branch / "drafts", ignore_errors=True)
        (branch / "drafts" / "turns").mkdir(parents=True, exist_ok=True)
        manifest = self.read_manifest(ref)
        manifest["current_revision"] = revision_id
        manifest["last_played_at"] = utc_now()
        self.write_manifest(ref, manifest)
        index = self.rebuild_index(ref)
        return {"rolled_back": True, "revision_id": revision_id, "index": index}

    def undo_latest_turn(self, ref: WorldRef) -> dict[str, Any]:
        revisions = self.list_revisions(ref)
        turn_revisions = [rev for rev in revisions if rev["kind"] == "turn"]
        if not turn_revisions:
            raise ValueError("no accepted turn to undo")
        latest = turn_revisions[-1]
        previous_candidates = [rev for rev in revisions if rev["number"] < latest["number"]]
        if not previous_candidates:
            raise ValueError("no previous revision")
        target = previous_candidates[-1]
        result = self.rollback_to_revision(ref, target["revision_id"])
        result["undone_revision"] = latest["revision_id"]
        return result

    def list_memory_patches(self, ref: WorldRef) -> list[dict[str, Any]]:
        patch_dir = self.current_dir(ref) / "memory" / "patches"
        patches = []
        for file in sorted(patch_dir.glob("*.json") if patch_dir.exists() else []):
            data = read_json(file, {})
            memory_writer = data.get("memory_writer", {})
            patches.append({
                "path": file.relative_to(self.current_dir(ref)).as_posix(),
                "turn_id": data.get("turn_id", file.stem),
                "writes": len(data.get("writes", [])),
                "state_changes": len(data.get("state_changes", [])),
                "memory_writer": memory_writer.get("generated_by", "legacy"),
                "warnings": len(memory_writer.get("warnings", [])),
                "updated_at": datetime.fromtimestamp(file.stat().st_mtime, timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            })
        return patches

    def list_current_files(self, ref: WorldRef) -> list[dict[str, Any]]:
        current = self.current_dir(ref)
        files = []
        for file in sorted(current.rglob("*")):
            if not file.is_file() or file.suffix.lower() not in {".md", ".json", ".jsonl"}:
                continue
            rel = file.relative_to(current).as_posix()
            files.append({
                "path": rel,
                "kind": rel.split("/", 1)[0],
                "size": file.stat().st_size,
                "updated_at": datetime.fromtimestamp(file.stat().st_mtime, timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            })
        return files

    def read_current_file(self, ref: WorldRef, rel_path: str) -> dict[str, Any]:
        current = self.current_dir(ref).resolve()
        target = (current / rel_path).resolve()
        if not target.is_relative_to(current) or not target.is_file():
            raise FileNotFoundError(rel_path)
        if target.suffix.lower() not in {".md", ".json", ".jsonl"}:
            raise ValueError("unsupported file type")
        return {
            "path": target.relative_to(current).as_posix(),
            "content": target.read_text(encoding="utf-8", errors="ignore"),
            "size": target.stat().st_size,
            "updated_at": datetime.fromtimestamp(target.stat().st_mtime, timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        }

    def write_current_file(self, ref: WorldRef, rel_path: str, content: str) -> dict[str, Any]:
        current = self.current_dir(ref).resolve()
        target = (current / rel_path).resolve()
        if not target.is_relative_to(current):
            raise FileNotFoundError(rel_path)
        if target.suffix.lower() not in {".md", ".json", ".jsonl"}:
            raise ValueError("unsupported file type")
        if target.suffix.lower() == ".json":
            json.loads(content)
        if target.suffix.lower() == ".jsonl":
            for index, line in enumerate(content.splitlines(), start=1):
                if line.strip():
                    try:
                        json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise ValueError(f"invalid jsonl at line {index}: {exc.msg}") from exc
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        revision = self.snapshot(ref, "file-edit")
        index = self.rebuild_index(ref)
        return {"saved": True, "path": target.relative_to(current).as_posix(), "revision": revision, "index": index}

    def create_branch(self, ref: WorldRef, name: str) -> dict[str, Any]:
        root = self.world_root(ref.world_id)
        branch_id = safe_id(name)
        branch = root / "branches" / branch_id
        if branch.exists():
            branch_id = f"{branch_id}-{secrets.token_hex(2)}"
            branch = root / "branches" / branch_id
        branch.mkdir(parents=True)
        shutil.copytree(self.current_dir(ref), branch / "current")
        (branch / "drafts" / "turns").mkdir(parents=True)
        (branch / "revisions").mkdir()
        manifest = self.read_manifest(WorldRef(ref.world_id))
        branch_manifest = {
            "world_id": ref.world_id,
            "branch_id": branch_id,
            "display_name": name,
            "subtitle": f"从 {manifest.get('display_name', ref.world_id)} 创建的分支",
            "locked": manifest.get("locked", False),
            "password": manifest.get("password"),
            "created_at": utc_now(),
            "last_played_at": utc_now(),
            "current_revision": "000001.branch",
            "storage_version": 1,
        }
        write_json(branch / "manifest.json", branch_manifest)
        self.snapshot(WorldRef(ref.world_id, branch_id), "branch")
        return public_manifest(branch_manifest)

    def export_archive(self, ref: WorldRef) -> dict[str, Any]:
        root = self.branch_root(ref)
        manifest = self.read_manifest(ref)
        label = manifest.get("display_name") or manifest.get("branch_id") or manifest.get("world_id") or root.name
        base_name = safe_id(str(label))
        if ref.branch_id:
            base_name = f"{safe_id(ref.world_id)}-{safe_id(ref.branch_id)}"
        filename = f"{base_name}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}.zip"
        prefix = base_name
        buffer = BytesIO()
        file_count = 0
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for file in sorted(root.rglob("*")):
                if not file.is_file():
                    continue
                rel = file.relative_to(root)
                if should_skip_archive_path(rel):
                    continue
                archive.write(file, (Path(prefix) / rel).as_posix())
                file_count += 1
            archive.writestr((Path(prefix) / "EXPORT_NOTES.txt").as_posix(), "明文世界导出。未包含 .cache 和 drafts；SQLite 索引可在导入后重建。\n")
            file_count += 1
        data = buffer.getvalue()
        return {
            "filename": filename,
            "content_type": "application/zip",
            "body": data,
            "size": len(data),
            "files": file_count,
        }

    def import_archive(self, file_base64: str, display_name: str = "") -> dict[str, Any]:
        raw = base64.b64decode(file_base64.split(",", 1)[-1])
        try:
            archive = zipfile.ZipFile(BytesIO(raw))
        except zipfile.BadZipFile as exc:
            raise ValueError("invalid zip archive") from exc
        members: dict[Path, zipfile.ZipInfo] = {}
        prefixes: list[str] = []
        try:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                parts = archive_member_parts(info.filename)
                if not parts:
                    continue
                prefixes.append(parts[0])
            if not prefixes:
                raise ValueError("archive is empty")
            prefix = prefixes[0] if all(item == prefixes[0] for item in prefixes) else ""
            for info in archive.infolist():
                if info.is_dir():
                    continue
                parts = archive_member_parts(info.filename)
                if prefix and parts and parts[0] == prefix:
                    parts = parts[1:]
                if not parts:
                    continue
                rel = Path(*parts)
                if should_skip_archive_path(rel):
                    continue
                members[rel] = info
            if Path("manifest.json") not in members or not any(rel.parts and rel.parts[0] == "current" for rel in members):
                raise ValueError("archive must contain manifest.json and current/")

            manifest = json.loads(archive.read(members[Path("manifest.json")]).decode("utf-8"))
            if not isinstance(manifest, dict):
                raise ValueError("manifest.json must be an object")
            name = display_name.strip() or str(manifest.get("display_name") or "导入世界")
            world_id = new_world_id(name)
            root = self.root / world_id
            root.mkdir(parents=True, exist_ok=False)
            for rel, info in members.items():
                target = (root / rel).resolve()
                if not target.is_relative_to(root.resolve()):
                    raise ValueError("archive member escapes world root")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(info))
            manifest["world_id"] = world_id
            manifest["display_name"] = name
            manifest["created_at"] = utc_now()
            manifest["last_played_at"] = utc_now()
            manifest["imported_from_archive"] = True
            manifest["imported_at"] = utc_now()
            write_json(root / "manifest.json", manifest)
            (root / "drafts" / "turns").mkdir(parents=True, exist_ok=True)
            (root / ".cache").mkdir(parents=True, exist_ok=True)
            self.rebuild_index(WorldRef(world_id))
            return public_manifest(manifest)
        finally:
            archive.close()

    def rebuild_index(self, ref: WorldRef) -> dict[str, Any]:
        branch = self.branch_root(ref)
        cache = branch / ".cache"
        if ref.branch_id:
            cache.mkdir(parents=True, exist_ok=True)
        else:
            cache = self.world_root(ref.world_id) / ".cache"
            cache.mkdir(parents=True, exist_ok=True)
        db_path = cache / "index.sqlite"
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("DROP TABLE IF EXISTS docs_fts")
            conn.execute("DROP TABLE IF EXISTS docs")
            conn.execute("CREATE TABLE docs(id INTEGER PRIMARY KEY, path TEXT UNIQUE, kind TEXT, title TEXT, body TEXT)")
            conn.execute("CREATE VIRTUAL TABLE docs_fts USING fts5(path, title, body, content='docs', content_rowid='id')")
            count = 0
            for file in self.current_dir(ref).rglob("*"):
                if not file.is_file() or file.suffix.lower() not in {".md", ".json", ".jsonl"}:
                    continue
                rel = file.relative_to(self.current_dir(ref)).as_posix()
                body = file.read_text(encoding="utf-8", errors="ignore")
                title = file.stem
                kind = rel.split("/", 1)[0]
                cur = conn.execute("INSERT INTO docs(path, kind, title, body) VALUES (?, ?, ?, ?)", (rel, kind, title, body[:200000]))
                rowid = cur.lastrowid
                conn.execute("INSERT INTO docs_fts(rowid, path, title, body) VALUES (?, ?, ?, ?)", (rowid, rel, title, body[:200000]))
                count += 1
            conn.commit()
            return {"indexed": count, "db": str(db_path)}
        finally:
            conn.close()

    def search_index(self, ref: WorldRef, query: str, limit: int = 20) -> list[dict[str, Any]]:
        branch = self.branch_root(ref)
        db_path = (branch / ".cache" / "index.sqlite") if ref.branch_id else (self.world_root(ref.world_id) / ".cache" / "index.sqlite")
        if not db_path.exists():
            self.rebuild_index(ref)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            safe_query = compact_query(query)
            rows = []
            if safe_query:
                try:
                    rows = conn.execute(
                        "SELECT docs.path, docs.kind, docs.title, snippet(docs_fts, 2, '[', ']', '...', 12) AS snippet "
                        "FROM docs_fts JOIN docs ON docs_fts.rowid = docs.id WHERE docs_fts MATCH ? LIMIT ?",
                        (safe_query, limit),
                    ).fetchall()
                except sqlite3.OperationalError:
                    rows = []
            if rows:
                return [dict(row) for row in rows]
            like = f"%{query}%"
            rows = conn.execute(
                "SELECT path, kind, title, substr(body, max(1, instr(body, ?) - 40), 120) AS snippet "
                "FROM docs WHERE body LIKE ? OR title LIKE ? OR path LIKE ? LIMIT ?",
                (query, like, like, like, limit),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def build_retrieval_packet(self, ref: WorldRef, payload: dict[str, Any], characters: list[dict[str, Any]], scene: dict[str, Any], limit: int = 12) -> list[dict[str, Any]]:
        pieces: list[str] = []
        pieces.append(str(payload.get("dm_directive", "")))
        for order in payload.get("controlled_orders", []):
            pieces.append(str(order.get("text", "")))
            char_id = safe_id(str(order.get("character_id", "")))
            match = next((char for char in characters if safe_id(char.get("character_id", "")) == char_id), None)
            if match:
                pieces.append(str(match.get("name", "")))
                pieces.append(str(match.get("location", "")))
                pieces.append(str(match.get("current_goal", "")))
        pieces.extend([
            str(scene.get("title", "")),
            str(scene.get("location_id", "")),
            str(scene.get("focus", "")),
            " ".join(scene.get("participants", []) if isinstance(scene.get("participants"), list) else []),
        ])
        query = compact_query(" ".join(piece for piece in pieces if piece), 160)
        queries = [query]
        queries.extend(compact_query(piece, 80) for piece in pieces if piece)
        seen_queries = []
        for item in queries:
            if item and item not in seen_queries:
                seen_queries.append(item)
        if not seen_queries:
            return []
        results = []
        seen_paths = set()
        for item in seen_queries:
            for result in self.search_index(ref, item, limit=limit):
                path = result.get("path")
                if path in seen_paths:
                    continue
                seen_paths.add(path)
                results.append(result)
                if len(results) >= limit:
                    break
            if len(results) >= limit:
                break
        packet = []
        for item in results:
            packet.append({
                "path": item.get("path"),
                "kind": item.get("kind"),
                "title": item.get("title"),
                "snippet": item.get("snippet"),
            })
        return packet
