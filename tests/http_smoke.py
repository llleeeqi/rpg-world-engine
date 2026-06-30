import json
import os
import time
import base64
import zipfile
from io import BytesIO
import urllib.error
import urllib.parse
import urllib.request


BASE_URL = os.environ.get("RPG_WORLD_BASE_URL", "http://127.0.0.1:54925").rstrip("/")


def request(method: str, path: str, payload: dict | None = None) -> tuple[int, str, dict]:
    body = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(f"{BASE_URL}{path}", data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            text = resp.read().decode("utf-8")
            data = json.loads(text) if resp.headers.get("Content-Type", "").startswith("application/json") else {}
            return resp.status, text, data
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8")
        data = json.loads(text) if text else {}
        return exc.code, text, data


def get_text(path: str) -> tuple[int, str]:
    with urllib.request.urlopen(f"{BASE_URL}{path}", timeout=10) as resp:
        return resp.status, resp.read().decode("utf-8")


def get_bytes(path: str) -> tuple[int, bytes, dict]:
    with urllib.request.urlopen(f"{BASE_URL}{path}", timeout=10) as resp:
        return resp.status, resp.read(), dict(resp.headers)


def main() -> None:
    status, _text, data = request("GET", "/api/health")
    assert status == 200 and data["ok"] is True

    status, html = get_text("/")
    assert status == 200
    assert "/app.js" in html and "/styles.css" in html

    name = f"HTTP Smoke {int(time.time())}"
    status, _text, created = request("POST", "/api/worlds", {
        "display_name": name,
        "subtitle": "HTTP integration test",
    })
    assert status == 201, created
    world_id = created["manifest"]["world_id"]
    world_ref = urllib.parse.quote(world_id, safe="")

    status, _text, character = request("POST", f"/api/worlds/{world_ref}/characters", {
        "name": "HTTP A",
        "profile": "用于 HTTP smoke 的角色。",
        "current_goal": "测试网页 API 链路",
        "location": "测试场景",
    })
    assert status == 201, character
    char_id = character["character"]["character_id"]

    status, _text, draft_response = request("POST", f"/api/worlds/{world_ref}/drafts", {
        "input": {
            "pace": "scene",
            "divergence": "medium",
            "controlled_orders": [
                {"slot": 1, "character_id": char_id, "perspective": "first_person", "text": "我检查当前局势。"},
            ],
            "dm_directive": "HTTP smoke 必须保持角色存活。",
        }
    })
    assert status == 201, draft_response
    draft = draft_response["draft"]
    assert draft["turn_id"] == "000001"
    assert draft["candidate_id"]
    assert draft["narrative"]
    assert draft["turn_outcome"]["dice"] == []

    edited_input = {
        "pace": "scene",
        "divergence": "medium",
        "controlled_orders": [
            {"slot": 1, "character_id": char_id, "perspective": "first_person", "text": "我改为调查场内冲突，并尝试谈判。"},
        ],
        "dm_directive": "HTTP smoke 发生冲突，局势需要调查和谈判。",
    }
    status, _text, keep_dice_response = request("POST", f"/api/worlds/{world_ref}/drafts", {
        "turn_id": draft["turn_id"],
        "input": edited_input,
        "previous_candidate": {"turn_id": draft["turn_id"], "candidate_id": draft["candidate_id"]},
        "mode": "rerun",
        "keep_dice": True,
    })
    assert status == 201, keep_dice_response
    keep_dice_draft = keep_dice_response["draft"]
    assert keep_dice_draft["turn_outcome"]["dice"] == draft["turn_outcome"]["dice"]
    assert keep_dice_draft["input"]["controlled_orders"][0]["text"].startswith("我改为调查")

    status, _text, reroll_response = request("POST", f"/api/worlds/{world_ref}/drafts", {
        "turn_id": draft["turn_id"],
        "input": edited_input,
        "previous_candidate": {"turn_id": draft["turn_id"], "candidate_id": draft["candidate_id"]},
        "mode": "rerun",
        "keep_dice": False,
    })
    assert status == 201, reroll_response
    draft = reroll_response["draft"]
    assert draft["turn_outcome"]["dice"], draft

    status, _text, accepted = request("POST", f"/api/worlds/{world_ref}/drafts/{draft['turn_id']}/{draft['candidate_id']}/accept", {})
    assert status == 200, accepted
    assert accepted["accepted"] is True

    status, _text, world = request("GET", f"/api/worlds/{world_ref}")
    assert status == 200, world
    assert world["events"] and world["events"][-1]["turn_id"] == "000001"
    assert world["dm_directives"] and world["dm_directives"][-1]["source_turn"] == "000001"
    assert world["patches"] and world["patches"][-1]["turn_id"] == "000001"

    status, _text, files = request("GET", f"/api/worlds/{world_ref}/files")
    assert status == 200, files
    assert any(item["path"] == "world_bible.md" for item in files["files"])
    assert any(item["path"].startswith("locations/") for item in files["files"])
    updated_location = None
    for item in files["files"]:
        if not item["path"].startswith("locations/"):
            continue
        status, _text, location_file = request("GET", f"/api/worlds/{world_ref}/files/{urllib.parse.quote(item['path'], safe='')}")
        assert status == 200, location_file
        content = json.loads(location_file["file"]["content"])
        if content.get("last_updated_turn") == "000001":
            updated_location = content
            break
    assert updated_location is not None
    assert updated_location["recent_turns"][-1]["turn_id"] == "000001"

    rules_path = urllib.parse.quote("rules/dice_rules.json", safe="")
    status, _text, bad_file = request("PATCH", f"/api/worlds/{world_ref}/files/{rules_path}", {"content": "{"})
    assert status == 400, bad_file
    assert "error" in bad_file

    status, _text, search = request("GET", f"/api/worlds/{world_ref}/search?q={urllib.parse.quote('HTTP')}")
    assert status == 200, search
    assert isinstance(search["results"], list)

    status, _text, indexed = request("POST", f"/api/worlds/{world_ref}/index", {})
    assert status == 200, indexed
    assert indexed["indexed"] > 0

    status, body, headers = get_bytes(f"/api/worlds/{world_ref}/export")
    assert status == 200
    assert headers["Content-Type"] == "application/zip"
    with zipfile.ZipFile(BytesIO(body)) as archive:
        names = archive.namelist()
        assert any(name.endswith("/manifest.json") for name in names)
        assert any(name.endswith("/current/events/000001.turn.json") for name in names)
        assert any(name.endswith("/EXPORT_NOTES.txt") for name in names)
        assert not any("/.cache/" in name or "/drafts/" in name for name in names)

    status, _text, imported = request("POST", "/api/world-imports", {
        "display_name": f"{name} Imported",
        "file_base64": "data:application/zip;base64," + base64.b64encode(body).decode("ascii"),
    })
    assert status == 201, imported
    imported_id = imported["manifest"]["world_id"]
    assert imported_id != world_id
    imported_ref = urllib.parse.quote(imported_id, safe="")
    status, _text, imported_world = request("GET", f"/api/worlds/{imported_ref}")
    assert status == 200, imported_world
    assert imported_world["events"] and imported_world["events"][-1]["turn_id"] == "000001"

    print("http smoke ok")


if __name__ == "__main__":
    main()
