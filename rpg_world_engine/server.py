from __future__ import annotations

import json
import secrets
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

from .generator import DraftContext, MultiAgentGenerator
from .importer import Importer
from .llm import public_config_status
from .storage import PROJECT_ROOT, WorldRef, WorldStore, public_manifest, verify_password


WEB_ROOT = PROJECT_ROOT / "web"
store = WorldStore()
generator = MultiAgentGenerator()
importer = Importer(store)
sessions: dict[str, str] = {}


def json_bytes(data: Any) -> bytes:
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")


class Handler(SimpleHTTPRequestHandler):
    server_version = "RPGWorldEngine/0.1"

    def translate_path(self, path: str) -> str:
        parsed = urlparse(path)
        clean = unquote(parsed.path)
        if clean == "/":
            return str(WEB_ROOT / "index.html")
        candidate = (WEB_ROOT / clean.lstrip("/")).resolve()
        if not candidate.exists() or not candidate.is_relative_to(WEB_ROOT.resolve()):
            return str(WEB_ROOT / "index.html")
        return str(candidate)

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def read_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if not length:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw) if raw else {}

    def send_json(self, data: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json_bytes(data)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, status: HTTPStatus, message: str) -> None:
        self.send_json({"error": message}, status)

    def send_bytes(self, body: bytes, *, content_type: str, filename: str) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(filename)}")
        self.end_headers()
        self.wfile.write(body)

    def parse_ref(self, parts: list[str]) -> WorldRef:
        return WorldRef.parse(parts[2])

    def require_unlock(self, ref: WorldRef) -> bool:
        manifest = store.read_manifest(ref)
        if not manifest.get("locked"):
            return True
        token = self.headers.get("X-World-Token", "")
        return sessions.get(token) == ref.key

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if not path.startswith("/api/"):
            return super().do_GET()
        try:
            self.handle_get(path, parse_qs(parsed.query))
        except FileNotFoundError as exc:
            self.send_error_json(HTTPStatus.NOT_FOUND, str(exc))
        except ValueError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception as exc:  # noqa: BLE001
            self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            self.handle_post(parsed.path, self.read_body())
        except json.JSONDecodeError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, f"invalid json body: {exc.msg}")
        except FileNotFoundError as exc:
            self.send_error_json(HTTPStatus.NOT_FOUND, str(exc))
        except PermissionError:
            self.send_error_json(HTTPStatus.UNAUTHORIZED, "world is locked")
        except ValueError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception as exc:  # noqa: BLE001
            self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def do_PATCH(self) -> None:
        parsed = urlparse(self.path)
        try:
            self.handle_patch(parsed.path, self.read_body())
        except json.JSONDecodeError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, f"invalid json body: {exc.msg}")
        except FileNotFoundError as exc:
            self.send_error_json(HTTPStatus.NOT_FOUND, str(exc))
        except PermissionError:
            self.send_error_json(HTTPStatus.UNAUTHORIZED, "world is locked")
        except ValueError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception as exc:  # noqa: BLE001
            self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def handle_get(self, path: str, query: dict[str, list[str]]) -> None:
        parts = path.strip("/").split("/")
        if path == "/api/health":
            return self.send_json({"ok": True})
        if path == "/api/config/llm":
            return self.send_json(public_config_status())
        if path == "/api/worlds":
            return self.send_json({"worlds": store.list_worlds()})
        if len(parts) >= 3 and parts[0] == "api" and parts[1] == "worlds":
            ref = WorldRef.parse(unquote(parts[2]))
            if len(parts) == 3:
                manifest = store.read_manifest(ref)
                if manifest.get("locked") and not self.require_unlock(ref):
                    return self.send_json({"manifest": public_manifest(manifest), "locked": True})
                rules = store.read_rules(ref)
                return self.send_json({
                    "manifest": public_manifest(manifest),
                    "characters": store.list_characters(ref),
                    "events": store.recent_events(ref, 12),
                    "dm_directives": store.list_dm_directives(ref, 12),
                    "revisions": store.list_revisions(ref),
                    "patches": store.list_memory_patches(ref),
                    "agent_pool": store.agent_pool_status(ref),
                    "rules": {"version": rules.get("version", 1), "path": "rules/dice_rules.json"},
                    "import_review": store.get_import_review_session(ref),
                    "scene": store.current_dir(ref).joinpath("scenes/current_scene.json").read_text(encoding="utf-8"),
                })
            if not self.require_unlock(ref):
                raise PermissionError()
            if parts[3] == "characters":
                return self.send_json({"characters": store.list_characters(ref)})
            if parts[3] == "events":
                limit = int(query.get("limit", ["20"])[0])
                return self.send_json({"events": store.recent_events(ref, limit)})
            if parts[3] == "revisions":
                return self.send_json({"revisions": store.list_revisions(ref)})
            if parts[3] == "patches":
                return self.send_json({"patches": store.list_memory_patches(ref)})
            if parts[3] == "import-review":
                return self.send_json({"session": store.get_import_review_session(ref)})
            if parts[3] == "export":
                archive = store.export_archive(ref)
                return self.send_bytes(archive["body"], content_type=archive["content_type"], filename=archive["filename"])
            if parts[3] == "files" and len(parts) == 4:
                return self.send_json({"files": store.list_current_files(ref)})
            if parts[3] == "files" and len(parts) == 5:
                return self.send_json({"file": store.read_current_file(ref, unquote(parts[4]))})
            if parts[3] == "search":
                q = query.get("q", [""])[0]
                return self.send_json({"results": store.search_index(ref, q) if q else []})
        self.send_error_json(HTTPStatus.NOT_FOUND, path)

    def handle_post(self, path: str, body: dict[str, Any]) -> None:
        parts = path.strip("/").split("/")
        if path == "/api/worlds":
            display_name = body.get("display_name", "").strip()
            if not display_name:
                return self.send_error_json(HTTPStatus.BAD_REQUEST, "display_name is required")
            manifest = store.create_world(display_name, body.get("subtitle", ""), body.get("password", ""))
            return self.send_json({"manifest": manifest}, HTTPStatus.CREATED)
        if path == "/api/world-imports":
            file_base64 = body.get("file_base64", "")
            if not file_base64:
                return self.send_error_json(HTTPStatus.BAD_REQUEST, "file_base64 is required")
            manifest = store.import_archive(file_base64, body.get("display_name", ""))
            return self.send_json({"manifest": manifest}, HTTPStatus.CREATED)
        if len(parts) < 3 or parts[:2] != ["api", "worlds"]:
            return self.send_error_json(HTTPStatus.NOT_FOUND, path)
        ref = WorldRef.parse(unquote(parts[2]))
        if len(parts) == 4 and parts[3] == "unlock":
            manifest = store.read_manifest(ref)
            if verify_password(manifest.get("password"), body.get("password", "")):
                token = secrets.token_urlsafe(24)
                sessions[token] = ref.key
                return self.send_json({"token": token, "manifest": public_manifest(manifest)})
            return self.send_error_json(HTTPStatus.UNAUTHORIZED, "密码不正确")
        if not self.require_unlock(ref):
            raise PermissionError()
        if len(parts) == 4 and parts[3] == "characters":
            return self.send_json({"character": store.create_character(ref, body)}, HTTPStatus.CREATED)
        if len(parts) == 4 and parts[3] == "imports":
            result = importer.import_text(ref, body)
            return self.send_json({"report": result.report}, HTTPStatus.CREATED)
        if len(parts) == 4 and parts[3] == "import-review-complete":
            return self.send_json(store.complete_import_review(ref, body.get("note", "")))
        if len(parts) == 4 and parts[3] == "import-review-answer":
            return self.send_json({"session": store.answer_import_review_question(ref, body.get("answer", ""))})
        if len(parts) == 4 and parts[3] == "drafts":
            manifest = store.read_manifest(ref)
            if manifest.get("setup_review_required"):
                return self.send_error_json(HTTPStatus.CONFLICT, "导入核对未完成，暂不能开始推演")
            turn_id = body.get("turn_id") or store.next_turn_id(ref)
            previous = None
            if body.get("previous_candidate"):
                previous = store.read_candidate(ref, body["previous_candidate"]["turn_id"], body["previous_candidate"]["candidate_id"])
            input_payload = body.get("input", body)
            characters = store.list_characters(ref)
            scene = store.current_dir(ref).joinpath("scenes/current_scene.json").exists() and json.loads(store.current_dir(ref).joinpath("scenes/current_scene.json").read_text(encoding="utf-8")) or {}
            location_state = store.read_location_state(ref, scene.get("location_id", "未定地点"))
            draft = generator.build_draft(DraftContext(
                turn_id=turn_id,
                input_payload=input_payload,
                characters=characters,
                recent_events=store.recent_events(ref, 8),
                scene=scene,
                location_state=location_state,
                world_bible=(store.current_dir(ref) / "world_bible.md").read_text(encoding="utf-8", errors="ignore"),
                dm_policy=(store.current_dir(ref) / "dm_policy.md").read_text(encoding="utf-8", errors="ignore"),
                imported_preset=(store.current_dir(ref) / "prompts" / "imported_preset.md").read_text(encoding="utf-8", errors="ignore"),
                relevant_docs=store.build_retrieval_packet(ref, input_payload, characters, scene),
                previous_candidate=previous,
                mode=body.get("mode", "new"),
                keep_dice=bool(body.get("keep_dice")),
                agent_budget=manifest.get("agent_budget", {}),
                rules=store.read_rules(ref),
            ))
            return self.send_json({"draft": store.save_draft(ref, draft)}, HTTPStatus.CREATED)
        if len(parts) == 7 and parts[3] == "drafts" and parts[6] == "accept":
            return self.send_json(store.accept_candidate(ref, parts[4], parts[5]))
        if len(parts) == 4 and parts[3] == "index":
            return self.send_json(store.rebuild_index(ref))
        if len(parts) == 4 and parts[3] == "branches":
            name = body.get("display_name", "").strip() or "未命名分支"
            return self.send_json({"branch": store.create_branch(ref, name)}, HTTPStatus.CREATED)
        if len(parts) == 5 and parts[3] == "rollback":
            return self.send_json(store.rollback_to_revision(ref, parts[4]))
        if len(parts) == 4 and parts[3] == "undo-latest-turn":
            return self.send_json(store.undo_latest_turn(ref))
        if len(parts) == 5 and parts[3] == "patches":
            return self.send_json(store.revert_memory_patch(ref, parts[4]))
        self.send_error_json(HTTPStatus.NOT_FOUND, path)

    def handle_patch(self, path: str, body: dict[str, Any]) -> None:
        parts = path.strip("/").split("/")
        if len(parts) == 5 and parts[:2] == ["api", "worlds"] and parts[3] == "files":
            ref = WorldRef.parse(unquote(parts[2]))
            if not self.require_unlock(ref):
                raise PermissionError()
            return self.send_json(store.write_current_file(ref, unquote(parts[4]), body.get("content", "")))
        if len(parts) == 5 and parts[:2] == ["api", "worlds"] and parts[3] == "characters":
            ref = WorldRef.parse(unquote(parts[2]))
            if not self.require_unlock(ref):
                raise PermissionError()
            return self.send_json(store.update_character_agent(ref, unquote(parts[4]), body))
        if len(parts) == 3 and parts[:2] == ["api", "worlds"]:
            ref = WorldRef.parse(unquote(parts[2]))
            if not self.require_unlock(ref):
                raise PermissionError()
            return self.send_json({"manifest": store.update_manifest_public(ref, body)})
        self.send_error_json(HTTPStatus.NOT_FOUND, path)


def run(host: str = "127.0.0.1", port: int = 54925) -> None:
    WEB_ROOT.mkdir(parents=True, exist_ok=True)
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"RPG World Engine running at http://{host}:{port}")
    httpd.serve_forever()


if __name__ == "__main__":
    run()
