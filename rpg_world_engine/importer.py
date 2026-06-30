from __future__ import annotations

import base64
import json
import re
import zlib
from dataclasses import dataclass
from typing import Any

from .storage import WorldRef, WorldStore, append_markdown_block, safe_id, utc_now, write_json


def guess_title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        clean = line.strip().lstrip("#").strip()
        if clean:
            return clean[:48]
    return fallback


def extract_character_name(text: str, fallback: str) -> str:
    maybe_card = parse_character_card_json(text)
    if maybe_card:
        return maybe_card["name"]
    patterns = [
        r"(?:name|Name|角色名|姓名)\s*[:：]\s*([^\n\r]+)",
        r"^#\s*([^\n\r]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.MULTILINE)
        if match:
            return match.group(1).strip()[:40]
    return guess_title(text, fallback)


def classify_import(text: str, requested: str) -> str:
    if requested in {"world", "character", "preset", "lore"}:
        return requested
    if parse_character_card_json(text):
        return "character"
    if parse_worldbook_json(text):
        return "world"
    lowered = text.lower()
    if any(key in lowered for key in ["temperature", "jailbreak", "system prompt", "prompt manager", "破限", "预设"]):
        return "preset"
    if any(key in text for key in ["角色名", "性格", "第一条消息", "示例对话"]) or "name:" in lowered:
        return "character"
    if any(key in text for key in ["世界书", "World Info", "Lorebook", "背景", "设定"]):
        return "world"
    return "lore"


@dataclass
class ImportResult:
    report: dict[str, Any]
    changed_paths: list[str]


class Importer:
    def __init__(self, store: WorldStore):
        self.store = store

    def import_text(self, ref: WorldRef, payload: dict[str, Any]) -> ImportResult:
        raw_rel = ""
        text = payload.get("text", "").strip()
        source_name = payload.get("source_name", "").strip() or payload.get("file_name", "").strip() or "pasted-import"
        file_b64 = payload.get("file_base64", "")
        if file_b64:
            raw_bytes = base64.b64decode(file_b64.split(",", 1)[-1])
            source_name = source_name or "uploaded-file"
            raw_rel = self.save_raw_file(ref, source_name, raw_bytes)
            text = self.extract_text_from_file(source_name, raw_bytes)
        if not text:
            raise ValueError("text is required")
        requested = payload.get("kind", "auto")
        kind = classify_import(text, requested)
        stamp = utc_now().replace(":", "").replace("-", "")
        root = self.store.world_root(ref.world_id)
        if not raw_rel:
            raw_rel = f"imports/raw/{safe_id(source_name)}-{stamp}.txt"
            raw_path = root / raw_rel
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_text(text, encoding="utf-8")

        current = self.store.current_dir(ref)
        changed: list[str] = [raw_rel]
        questions: list[str] = []
        created: dict[str, Any] = {}
        converted_lore_entries = 0

        if kind == "character":
            name = extract_character_name(text, source_name)
            card = parse_character_card_json(text)
            created = self.store.create_character(ref, {
                "name": name,
                "profile": self.clean_character_profile(text),
                "role_type": "controllable",
                "current_goal": "等待导入核对",
            })
            changed.extend([
                f"current/characters/{safe_id(name)}/profile.md",
                f"current/characters/{safe_id(name)}/state.json",
            ])
            if card and card.get("character_book_entries"):
                lore_paths = self.write_lore_entries(
                    current,
                    f"{source_name} 角色书",
                    card["character_book_entries"],
                    prefix=f"character-{safe_id(name)}",
                )
                changed.extend(lore_paths)
                converted_lore_entries += len(lore_paths)
            questions = [
                f"{name} 是否应该进入可控角色池？",
                f"{name} 的核心性格是否需要压缩或删减？",
                f"{name} 当前知道哪些世界秘密？",
                f"{name} 初始地点和目标是否正确？",
            ]
        elif kind == "preset":
            target = current / "prompts" / "imported_preset.md"
            append_markdown_block(target, "import", f"## {source_name}\n\n{text}")
            changed.append("current/prompts/imported_preset.md")
            questions = [
                "这个预设是否默认作用于 Character Agent？",
                "这个预设是否默认作用于 Writer？",
                "其中是否有需要禁止进入 Judge/Memory 的强提示词？",
            ]
        elif kind == "world":
            worldbook = parse_worldbook_json(text)
            target = current / "world_bible.md"
            if worldbook:
                lore_paths = self.write_lore_entries(current, source_name, worldbook["entries"], prefix=safe_id(source_name))
                changed.extend(lore_paths)
                converted_lore_entries += len(lore_paths)
                summary = [
                    f"## 导入世界书：{source_name}",
                    "",
                    f"- 条目数：{len(worldbook['entries'])}",
                    f"- 来源格式：{worldbook.get('format', 'SillyTavern worldbook')}",
                    "- 已转换为 `current/lore/` 下的独立条目，原始文件仍保存在 `imports/raw/`。",
                ]
                append_markdown_block(target, "import", "\n".join(summary))
            else:
                append_markdown_block(target, "import", f"## 导入背景：{source_name}\n\n{text}")
            changed.append("current/world_bible.md")
            questions = [
                "世界类型和基调是否识别正确？",
                "魔法/科技/时代水平是否需要改写？",
                "哪些设定是硬规则，不能被剧情随意覆盖？",
                "主要冲突是什么？",
                "玩家默认从哪个地点和身份开始？",
                "哪些世界书条目只是资料或传闻？",
                "是否有需要默认隐藏给玩家的秘密？",
                "初始场景应该从哪里开始？",
            ]
        else:
            worldbook = parse_worldbook_json(text)
            if worldbook:
                lore_paths = self.write_lore_entries(current, source_name, worldbook["entries"], prefix=safe_id(source_name))
                changed.extend(lore_paths)
                converted_lore_entries += len(lore_paths)
            else:
                title = guess_title(text, source_name)
                lore_dir = current / "lore"
                lore_dir.mkdir(parents=True, exist_ok=True)
                lore_path = lore_dir / f"{safe_id(title)}.md"
                lore_path.write_text(f"# {title}\n\n{text}\n", encoding="utf-8")
                changed.append(f"current/lore/{safe_id(title)}.md")
            questions = [
                "这条 lore 是硬设定、软资料，还是传闻？",
                "它应该关联哪些角色、地点或势力？",
            ]

        report = {
            "kind": kind,
            "source_name": source_name,
            "raw_path": raw_rel,
            "created": created,
            "converted_lore_entries": converted_lore_entries,
            "changed_paths": changed,
            "questions": questions,
            "created_at": utc_now(),
            "status": "needs_review",
        }
        report_path = root / "imports" / "reports" / f"{safe_id(source_name)}-{stamp}.json"
        write_json(report_path, report)
        changed.append(str(report_path.relative_to(root)))
        self.store.set_setup_review_required(ref, True)
        self.store.build_import_review_session(ref, force=True)
        revision = self.store.snapshot(ref, f"import-{kind}")
        self.store.rebuild_index(ref)
        report["revision"] = revision
        return ImportResult(report=report, changed_paths=changed)

    def clean_character_profile(self, text: str) -> str:
        maybe_card = parse_character_card_json(text)
        if maybe_card:
            return maybe_card["profile"]
        lines = [line.rstrip() for line in text.splitlines()]
        cleaned = "\n".join(line for line in lines if line.strip())
        return cleaned[:12000]

    def write_lore_entries(self, current, source_name: str, entries: list[dict[str, Any]], prefix: str = "lore") -> list[str]:
        lore_dir = current / "lore"
        lore_dir.mkdir(parents=True, exist_ok=True)
        changed: list[str] = []
        used: set[str] = set()
        for index, entry in enumerate(entries, start=1):
            title = entry.get("title") or entry.get("primary_key") or f"{source_name}-{index}"
            base = safe_id(f"{prefix}-{title}")[:80]
            filename = f"{base}.md"
            if filename in used or (lore_dir / filename).exists():
                filename = f"{base}-{index:03d}.md"
            used.add(filename)
            path = lore_dir / filename
            path.write_text(format_lore_entry_markdown(source_name, entry), encoding="utf-8")
            changed.append(f"current/lore/{filename}")
        return changed

    def save_raw_file(self, ref: WorldRef, source_name: str, raw: bytes) -> str:
        suffix = "." + source_name.rsplit(".", 1)[-1].lower() if "." in source_name else ".bin"
        stamp = utc_now().replace(":", "").replace("-", "")
        raw_rel = f"imports/raw/{safe_id(source_name.rsplit('.', 1)[0])}-{stamp}{suffix}"
        raw_path = self.store.world_root(ref.world_id) / raw_rel
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_bytes(raw)
        return raw_rel

    def extract_text_from_file(self, file_name: str, raw: bytes) -> str:
        lower = file_name.lower()
        if lower.endswith(".png"):
            card = extract_character_card_from_png(raw)
            if card:
                return json.dumps(card, ensure_ascii=False, indent=2)
            raise ValueError("PNG did not contain a supported character card text chunk")
        if lower.endswith(".json"):
            parsed = json.loads(raw.decode("utf-8"))
            return json.dumps(parsed, ensure_ascii=False, indent=2)
        return raw.decode("utf-8", errors="ignore")


def parse_character_card_json(text: str) -> dict[str, Any] | None:
    try:
        card = json.loads(text)
    except json.JSONDecodeError:
        return None
    data = card.get("data", card)
    if not isinstance(data, dict):
        return None
    name = str(data.get("name") or card.get("name") or "").strip()
    if not name:
        return None
    parts = [
        ("描述", data.get("description")),
        ("性格", data.get("personality")),
        ("场景", data.get("scenario")),
        ("第一条消息", data.get("first_mes")),
        ("示例对话", data.get("mes_example")),
        ("创建者备注", data.get("creator_notes")),
        ("系统提示", data.get("system_prompt")),
        ("后置历史指令", data.get("post_history_instructions")),
    ]
    profile = [f"# {name}"]
    for title, value in parts:
        if value:
            profile.append(f"\n## {title}\n\n{value}")
    character_book = data.get("character_book") or card.get("character_book") or {}
    book_entries = normalize_worldbook_entries(character_book)
    if book_entries:
        profile.append(f"\n## 角色书\n\n已导入 {len(book_entries)} 条角色书条目到 lore 层。")
    return {"name": name, "profile": "\n".join(profile), "character_book_entries": book_entries}


def parse_worldbook_json(text: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    candidates = [
        parsed,
        parsed.get("data"),
        parsed.get("world_info"),
        parsed.get("lorebook"),
        parsed.get("character_book"),
    ]
    for candidate in candidates:
        entries = normalize_worldbook_entries(candidate)
        if entries:
            return {
                "format": detect_worldbook_format(parsed),
                "entries": entries,
            }
    return None


def detect_worldbook_format(parsed: dict[str, Any]) -> str:
    if parsed.get("entries"):
        return "SillyTavern worldbook"
    if parsed.get("world_info"):
        return "world_info"
    if parsed.get("lorebook"):
        return "lorebook"
    if parsed.get("character_book"):
        return "character_book"
    return "json_lorebook"


def normalize_worldbook_entries(book: Any) -> list[dict[str, Any]]:
    if not isinstance(book, dict):
        return []
    raw_entries = book.get("entries")
    if isinstance(raw_entries, dict):
        iterable = list(raw_entries.values())
    elif isinstance(raw_entries, list):
        iterable = raw_entries
    else:
        iterable = []
    entries = []
    for index, raw in enumerate(iterable, start=1):
        if not isinstance(raw, dict):
            continue
        content = str(raw.get("content") or raw.get("text") or "").strip()
        if not content:
            continue
        keys = normalize_key_list(raw.get("key") or raw.get("keys"))
        secondary_keys = normalize_key_list(raw.get("keysecondary") or raw.get("secondary_keys") or raw.get("key_secondary"))
        title = str(raw.get("comment") or raw.get("name") or raw.get("title") or "").strip()
        if not title:
            title = keys[0] if keys else f"条目 {index}"
        entries.append({
            "title": title[:80],
            "uid": raw.get("uid", raw.get("id", index)),
            "primary_key": keys[0] if keys else "",
            "keys": keys,
            "secondary_keys": secondary_keys,
            "content": content,
            "constant": bool(raw.get("constant", False)),
            "selective": bool(raw.get("selective", False)),
            "disabled": bool(raw.get("disable", raw.get("disabled", False))),
            "order": raw.get("order"),
            "position": raw.get("position"),
            "depth": raw.get("depth"),
            "probability": raw.get("probability"),
            "use_probability": bool(raw.get("useProbability", raw.get("use_probability", False))),
            "case_sensitive": bool(raw.get("case_sensitive", raw.get("caseSensitive", False))),
            "match_whole_words": bool(raw.get("match_whole_words", raw.get("matchWholeWords", False))),
        })
    return entries


def normalize_key_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        parts = re.split(r"[,，\n]", value)
        return [part.strip() for part in parts if part.strip()]
    return []


def format_lore_entry_markdown(source_name: str, entry: dict[str, Any]) -> str:
    lines = [
        f"# {entry.get('title') or entry.get('primary_key') or '未命名条目'}",
        "",
        "```json",
        json.dumps({
            "source": source_name,
            "uid": entry.get("uid"),
            "keys": entry.get("keys", []),
            "secondary_keys": entry.get("secondary_keys", []),
            "constant": entry.get("constant", False),
            "selective": entry.get("selective", False),
            "disabled": entry.get("disabled", False),
            "order": entry.get("order"),
            "position": entry.get("position"),
            "depth": entry.get("depth"),
            "probability": entry.get("probability"),
            "use_probability": entry.get("use_probability", False),
            "case_sensitive": entry.get("case_sensitive", False),
            "match_whole_words": entry.get("match_whole_words", False),
        }, ensure_ascii=False, indent=2),
        "```",
        "",
        entry.get("content", "").rstrip(),
        "",
    ]
    return "\n".join(lines)


def extract_character_card_from_png(raw: bytes) -> dict[str, Any] | None:
    if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("not a png")
    offset = 8
    text_chunks: dict[str, str] = {}
    while offset + 8 <= len(raw):
        length = int.from_bytes(raw[offset : offset + 4], "big")
        chunk_type = raw[offset + 4 : offset + 8]
        data = raw[offset + 8 : offset + 8 + length]
        offset += 12 + length
        if chunk_type == b"IEND":
            break
        if chunk_type == b"tEXt":
            keyword, _, value = data.partition(b"\x00")
            text_chunks[keyword.decode("latin-1", errors="ignore")] = value.decode("utf-8", errors="ignore")
        elif chunk_type == b"zTXt":
            keyword, _, rest = data.partition(b"\x00")
            if rest:
                method = rest[0]
                compressed = rest[1:]
                if method == 0:
                    text_chunks[keyword.decode("latin-1", errors="ignore")] = zlib.decompress(compressed).decode("utf-8", errors="ignore")
        elif chunk_type == b"iTXt":
            parts = data.split(b"\x00", 5)
            if len(parts) == 6:
                keyword, compression_flag, _method, _language, _translated, value = parts
                if compression_flag == b"\x01":
                    value = zlib.decompress(value)
                text_chunks[keyword.decode("utf-8", errors="ignore")] = value.decode("utf-8", errors="ignore")

    for key in ["chara", "ccv3", "ccv2"]:
        value = text_chunks.get(key)
        if not value:
            continue
        parsed = decode_card_payload(value)
        if parsed:
            return parsed
    for value in text_chunks.values():
        parsed = decode_card_payload(value)
        if parsed:
            return parsed
    return None


def decode_card_payload(value: str) -> dict[str, Any] | None:
    candidates = [value.strip()]
    try:
        candidates.append(base64.b64decode(value.strip()).decode("utf-8"))
    except Exception:
        pass
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and (parsed.get("data") or parsed.get("name")):
            return parsed
    return None
