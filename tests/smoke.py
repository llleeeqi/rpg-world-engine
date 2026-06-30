import sys
import base64
import json
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rpg_world_engine.generator import DraftContext, LocalFallbackGenerator, MultiAgentGenerator
from rpg_world_engine.importer import Importer
from rpg_world_engine.storage import WorldRef, WorldStore, read_json, write_json


def png_card_payload(card: dict) -> str:
    encoded = base64.b64encode(json.dumps(card, ensure_ascii=False).encode("utf-8"))
    data = b"chara\x00" + encoded
    chunk = len(data).to_bytes(4, "big") + b"tEXt" + data + b"\x00\x00\x00\x00"
    iend = (0).to_bytes(4, "big") + b"IEND" + b"" + b"\x00\x00\x00\x00"
    raw = b"\x89PNG\r\n\x1a\n" + chunk + iend
    return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")


def main() -> None:
    with TemporaryDirectory() as tmp:
        store = WorldStore(Path(tmp) / "worlds")
        manifest = store.create_world("黑王国", "低魔王权崩坏年代")
        ref = WorldRef(manifest["world_id"])
        default_location = store.read_location_state(ref, "未定地点")
        assert default_location["location_id"] == "未定地点"
        assert default_location["last_updated_turn"] is None
        store.create_character(ref, {
            "name": "A",
            "profile": "谨慎、嘴硬、重视契约的旅行者。",
            "current_goal": "去坊市碰碰运气",
            "location": "王都坊市",
        })
        store.create_character(ref, {
            "name": "B",
            "profile": "精明的摊主，擅长借势谈价。",
            "current_goal": "在坊市摆摊卖东西",
            "location": "王都坊市",
        })
        rules_path = store.current_dir(ref) / "rules/dice_rules.json"
        assert rules_path.exists()
        strict_rules = store.read_rules(ref)
        strict_rules["version"] = 2
        strict_rules["roll_triggers"]["minimum_score"] = 99
        write_json(rules_path, strict_rules)
        strict_draft = LocalFallbackGenerator().build_draft(DraftContext(
            turn_id=store.next_turn_id(ref),
            input_payload={
                "pace": "scene",
                "divergence": "high",
                "controlled_orders": [{"slot": 1, "character_id": "a", "text": "我潜入后巷。"}],
                "dm_directive": "后巷有巡逻。",
            },
            characters=store.list_characters(ref),
            recent_events=[],
            scene=read_json(store.current_dir(ref) / "scenes/current_scene.json", {}),
            rules=store.read_rules(ref),
        ))
        assert strict_draft["turn_outcome"]["dice"] == []
        fixed_rules = store.read_rules(ref)
        fixed_rules["version"] = 3
        fixed_rules["roll_triggers"]["minimum_score"] = 0
        fixed_rules["dice"]["type"] = "d10"
        fixed_rules["dice"]["min_roll"] = 10
        fixed_rules["dice"]["max_roll"] = 10
        fixed_rules["dice"]["bonus_choices"] = [0]
        fixed_rules["dice"]["difficulty_min"] = 7
        fixed_rules["dice"]["difficulty_max"] = 7
        fixed_rules["dice"]["random_difficulty_jitter"] = [0, 0]
        write_json(rules_path, fixed_rules)
        fixed_draft = LocalFallbackGenerator().build_draft(DraftContext(
            turn_id=store.next_turn_id(ref),
            input_payload={
                "pace": "beat",
                "divergence": "low",
                "controlled_orders": [{"slot": 1, "character_id": "a", "text": "我试着讨价还价。"}],
                "dm_directive": "摊位前人群拥挤。",
            },
            characters=store.list_characters(ref),
            recent_events=[],
            scene=read_json(store.current_dir(ref) / "scenes/current_scene.json", {}),
            rules=store.read_rules(ref),
        ))
        assert fixed_draft["rules_version"] == 3
        assert fixed_draft["turn_outcome"]["dice"][0]["type"] == "d10"
        assert fixed_draft["turn_outcome"]["dice"][0]["difficulty"] == 7
        assert fixed_draft["turn_outcome"]["dice"][0]["roll"] == 10
        rewrite_draft = LocalFallbackGenerator().build_draft(DraftContext(
            turn_id=store.next_turn_id(ref),
            input_payload=fixed_draft["input"],
            characters=store.list_characters(ref),
            recent_events=[],
            scene=read_json(store.current_dir(ref) / "scenes/current_scene.json", {}),
            previous_candidate=fixed_draft,
            mode="rewrite",
            rules=store.read_rules(ref),
        ))
        assert rewrite_draft["turn_outcome"] == fixed_draft["turn_outcome"]
        assert rewrite_draft["narrative"] != fixed_draft["narrative"]
        assert rewrite_draft["rewrite_of"]["turn_id"] == fixed_draft["turn_id"]
        no_roll_rules = store.read_rules(ref)
        no_roll_rules["version"] = 4
        no_roll_rules["roll_triggers"]["minimum_score"] = 99
        write_json(rules_path, no_roll_rules)
        keep_dice_draft = LocalFallbackGenerator().build_draft(DraftContext(
            turn_id=store.next_turn_id(ref),
            input_payload=fixed_draft["input"],
            characters=store.list_characters(ref),
            recent_events=[],
            scene=read_json(store.current_dir(ref) / "scenes/current_scene.json", {}),
            previous_candidate=fixed_draft,
            mode="rerun",
            keep_dice=True,
            rules=store.read_rules(ref),
        ))
        assert keep_dice_draft["turn_outcome"]["dice"] == fixed_draft["turn_outcome"]["dice"]
        imported = Importer(store).import_text(ref, {
            "kind": "character",
            "source_name": "C角色卡",
            "text": "角色名：C\n性格：沉默、谨慎。\n当前目标：战斗后回去休息。",
        })
        assert imported.report["kind"] == "character"
        assert imported.report["questions"]
        assert store.read_manifest(ref)["setup_review_required"] is True
        session = store.get_import_review_session(ref)
        assert session["questions"]
        answered = store.answer_import_review_question(ref, "确认进入可控池")
        assert answered["questions"][0]["answer"] == "确认进入可控池"
        png_import = Importer(store).import_text(ref, {
            "kind": "auto",
            "file_name": "D.png",
            "file_base64": png_card_payload({
                "spec": "chara_card_v2",
                "data": {
                    "name": "D",
                    "description": "宫廷书记官。",
                    "personality": "谨慎、记仇。",
                    "scenario": "王都。",
                    "character_book": {
                        "entries": [
                            {
                                "key": ["宫廷书记官", "内廷文书"],
                                "comment": "D的职务秘密",
                                "content": "D 能接触到王都内廷的往来文书，但不会主动暴露来源。",
                                "constant": False,
                            }
                        ]
                    },
                },
            }),
        })
        assert png_import.report["kind"] == "character"
        assert png_import.report["converted_lore_entries"] == 1
        assert any(char["name"] == "D" for char in store.list_characters(ref))
        worldbook_import = Importer(store).import_text(ref, {
            "kind": "auto",
            "source_name": "王都世界书",
            "text": json.dumps({
                "entries": {
                    "0": {
                        "key": ["黑市", "地下坊市"],
                        "keysecondary": ["王都"],
                        "comment": "王都黑市",
                        "content": "王都黑市位于旧排水渠附近，只有熟人引荐才能进入。",
                        "constant": False,
                        "selective": True,
                        "order": 80,
                        "depth": 4,
                    },
                    "1": {
                        "key": ["银印契约"],
                        "comment": "银印契约",
                        "content": "银印契约由王国旧法承认，违约者会被行会排斥。",
                        "constant": True,
                    },
                }
            }, ensure_ascii=False),
        })
        assert worldbook_import.report["kind"] == "world"
        assert worldbook_import.report["converted_lore_entries"] == 2
        assert any(path.startswith("current/lore/") for path in worldbook_import.changed_paths)
        lore_text = "\n".join(path.read_text(encoding="utf-8") for path in (store.current_dir(ref) / "lore").glob("*.md"))
        assert "王都黑市位于旧排水渠附近" in lore_text
        assert '"keys"' in lore_text
        review = store.complete_import_review(ref, "核对完成")
        assert review["review_complete"] is True
        assert "import_review_notes.md" in review["applied_paths"]
        assert store.read_manifest(ref)["setup_review_required"] is False
        c_profile = (store.current_dir(ref) / "characters/c/profile.md").read_text(encoding="utf-8")
        assert "确认进入可控池" in c_profile
        review_notes = (store.current_dir(ref) / "import_review_notes.md").read_text(encoding="utf-8")
        assert "核对完成" in review_notes
        for index in range(13):
            store.create_character(ref, {
                "name": f"Extra{index}",
                "profile": "用于测试 agent 池预算的临时角色。",
                "role_type": "major_npc",
                "current_goal": "围绕坊市局势做出反应",
                "location": "王都坊市",
            })
        pool = store.agent_pool_status(ref)
        assert pool["independent_profiles"] >= 16
        assert pool["review_required"] is True
        store.update_character_agent(ref, "extra0", {
            "agent_mode": "dormant",
            "availability": "dormant",
            "agent_enabled": True,
        })
        store.update_character_agent(ref, "extra1", {
            "agent_mode": "npc_ensemble",
            "availability": "active",
            "agent_enabled": True,
        })
        pool = store.agent_pool_status(ref)
        assert pool["review_required"] is False
        assert pool["npc_ensemble"] >= 1
        payload = {
            "pace": "scene",
            "divergence": "medium",
            "controlled_orders": [
                {"slot": 1, "character_id": "a", "perspective": "first_person", "text": "我去坊市碰碰运气。"},
                {"slot": 2, "character_id": "b", "perspective": "first_person", "text": "我在坊市摆摊卖东西。"},
            ],
            "dm_directive": "A 和 B 因价格问题起冲突，但双方不能死亡。",
        }
        generator = LocalFallbackGenerator()
        draft = generator.build_draft(DraftContext(
            turn_id=store.next_turn_id(ref),
            input_payload=payload,
            characters=store.list_characters(ref),
            recent_events=[],
            scene=read_json(store.current_dir(ref) / "scenes/current_scene.json", {}),
            location_state=store.read_location_state(ref, "未定地点"),
            agent_budget=store.read_manifest(ref)["agent_budget"],
            rules=store.read_rules(ref),
        ))
        assert len(draft["active_agents"]) <= 8
        assert "extra0" not in draft["active_agents"]
        assert draft["turn_outcome"]["npc_ensemble"]["used"] is True
        saved = store.save_draft(ref, draft)
        result = store.accept_candidate(ref, saved["turn_id"], saved["candidate_id"])
        assert result["accepted"] is True
        assert (store.current_dir(ref) / "events" / "000001.turn.json").exists()
        assert (store.current_dir(ref) / "memory/patches/000001.memory_patch.json").exists()
        dm_records = store.list_dm_directives(ref)
        assert dm_records and dm_records[-1]["directive"] == payload["dm_directive"]
        assert dm_records[-1]["source_turn"] == "000001"
        current_scene = read_json(store.current_dir(ref) / "scenes/current_scene.json", {})
        location_after_turn = store.read_location_state(ref, current_scene.get("location_id", "未定地点"))
        assert location_after_turn["last_updated_turn"] == "000001"
        assert location_after_turn["recent_turns"][-1]["turn_id"] == "000001"
        previous_scene = read_json(store.current_dir(ref) / "scenes/previous_scene.json")
        assert previous_scene["scene_id"] == "scene_000001"
        scene_log = (store.current_dir(ref) / "scenes/scene_log.jsonl").read_text(encoding="utf-8")
        assert '"turn_id": "000001"' in scene_log
        memory_patch = read_json(store.current_dir(ref) / "memory/patches/000001.memory_patch.json")
        assert memory_patch["memory_writer"]["generated_by"].startswith("memory_fallback")
        patches = store.list_memory_patches(ref)
        assert patches and patches[0]["turn_id"] == "000001"
        assert patches[0]["memory_writer"].startswith("memory_fallback")
        reverted_patch = store.revert_memory_patch(ref, "000001")
        assert reverted_patch["reverted"] is True
        assert not (store.current_dir(ref) / "memory/patches/000001.memory_patch.json").exists()
        assert not store.list_dm_directives(ref)
        reverted_location = store.read_location_state(ref, current_scene.get("location_id", "未定地点"))
        assert reverted_location["last_updated_turn"] is None
        assert reverted_location["recent_turns"] == []
        saved_again = store.save_draft(ref, draft)
        result = store.accept_candidate(ref, saved_again["turn_id"], saved_again["candidate_id"])
        assert result["accepted"] is True
        branch = store.create_branch(ref, "坊市另一走向")
        assert branch["branch_id"]
        assert "password" not in branch
        results = store.search_index(ref, "坊市")
        assert results
        retrieval = store.build_retrieval_packet(ref, payload, store.list_characters(ref), read_json(store.current_dir(ref) / "scenes/current_scene.json", {}))
        assert retrieval
        normalized = MultiAgentGenerator().normalize_judge_result(
            [{"character_id": "a"}],
            {"resolved_events": "事件", "dice": [{"roll": 25, "difficulty": "14", "outcome": "bad"}], "character_results": [{"character_id": "a"}]},
        )
        assert normalized["dice"][0]["roll"] == 20
        assert normalized["validation_warnings"]
        preserved = MultiAgentGenerator().compose_outcome(
            DraftContext(
                turn_id="000099",
                input_payload=fixed_draft["input"],
                characters=store.list_characters(ref),
                recent_events=[],
                scene=read_json(store.current_dir(ref) / "scenes/current_scene.json", {}),
                previous_candidate=fixed_draft,
                keep_dice=True,
            ),
            [{"character_id": "a", "name": "A", "intent": "继续谈判", "emotion": "平静"}],
            {"used": False, "summary": "", "intents": []},
            {"resolved_events": ["新裁定"], "dice": [{"roll": 1, "difficulty": 20, "bonus": 0, "total": 1, "outcome": "failure"}], "character_results": []},
        )
        assert preserved["dice"] == fixed_draft["turn_outcome"]["dice"]
        files = store.list_current_files(ref)
        assert any(file["path"] == "world_bible.md" for file in files)
        world_file = store.read_current_file(ref, "world_bible.md")
        assert "黑王国" in world_file["content"]
        saved_file = store.write_current_file(ref, "world_bible.md", world_file["content"] + "\n追加设定。\n")
        assert saved_file["saved"] is True
        assert "追加设定" in store.read_current_file(ref, "world_bible.md")["content"]
        undo = store.undo_latest_turn(ref)
        assert undo["rolled_back"] is True
        revisions = store.list_revisions(ref)
        assert len(revisions) >= 2
        first_revision = revisions[0]["revision_id"]
        rollback = store.rollback_to_revision(ref, first_revision)
        assert rollback["rolled_back"] is True
        assert not (store.current_dir(ref) / "events" / "000001.turn.json").exists()
        print("smoke ok")


if __name__ == "__main__":
    main()
