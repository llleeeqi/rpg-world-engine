# Design

## Product Shape

这是一个单玩家、多 AI 角色的本地 Web RPG 世界系统。玩家每轮最多接管 3 个角色，用第一人称写行动，同时可以用 DM 指令进行全局主持。系统在玩家点击推进后生成一个草稿候选，玩家接受后才写入正式世界。

## Storage

权威数据使用明文文件，SQLite 只做可重建缓存。

```txt
worlds/<世界名>/
  manifest.json
  current/
    world_bible.md
    dm_policy.md
    prompts/
    characters/
    locations/
    scenes/
    events/
    memory/
    dm/
  drafts/
  revisions/
  branches/
  imports/
  assets/
  .cache/
```

默认线性历史。回溯会丢弃后续；需要保留时用户手动创建分支。每个 revision 保存完整 `current/` 快照。

## Turn Flow

```txt
玩家输入
  -> Orchestrator 选活跃角色和上下文
  -> Character Agents 生成角色意图
  -> NPC Ensemble 处理溢出角色和路人
  -> Judge 判断是否需要骰子并裁定结果
  -> Turn Outcome 固化事实骨架
  -> Writer 写第三人称正文
  -> Draft 等待用户审核
```

MVP 的本地 fallback 把这些步骤压在一个生成器里，但输出结构保留上述边界。

## Rules

每个世界都有 `current/rules/dice_rules.json`。它是明文权威规则，不是缓存：

- `dm_force_patterns`：DM 文本命中后不随机，按主持意图推进。
- `roll_triggers`：根据推进级别、发散程度、风险词计算是否需要骰子。
- `dice`：骰子类型、点数范围、加值候选、难度上下限、推进/发散修正、部分成功阈值。

PC GM 面板提供“编辑骰子规则”入口，实际仍走明文文件编辑。生成草稿时，Orchestrator 会读取当前规则并传给本地 fallback 或 Judge。

## Draft Actions

草稿生成后，用户是最终审核者：

- 接受并进入下一轮：写事件、状态、记忆、revision。
- 编辑本轮输入：回到本轮输入表单。
- 只重写正文：保留事实与骰子，只重跑 Writer。
- 重跑推演，保留骰子：重跑角色/裁定解释/正文，但保留随机结果。
- 重跑推演，重掷骰子：整轮重新推演。

接受候选后删除未采纳候选，保持线性存档干净。
当前实现会在后端强制这些边界：rewrite-only 直接复制旧 `Turn Outcome`，keep-dice 会在 compose 阶段用旧骰子覆盖 Judge 返回。

## DM Records

玩家每轮填写的 DM 指令只有在候选被接受后才进入正式世界。接受时会追加到 `current/dm/resolved_directives.jsonl`，记录指令文本、是否强制走向、是否使用骰子、关联回合和结果摘要。GM 面板会展示最近记录；单个 patch 反向应用时会按 `source_turn` 移除对应 DM 记录。

## Agent Budget

每轮最多 8 个独立角色 agent。玩家接管角色必须占名额。超过 8 的相关角色进入一个 NPC Ensemble agent 统一处理，但仍保留自己的角色文件和记忆。

角色 `state.json` 中的 `agent_mode`、`agent_enabled`、`availability` 决定是否自动拉起：

- `independent_default` / `independent_forced`：可作为独立角色 agent。
- `npc_ensemble`：不单独拉起，交给 NPC Ensemble 统一处理。
- `dormant` / `disabled` 或 `availability=offstage|dormant|archived`：冷藏，不自动参与本轮。

PC 端角色池可以直接调整这些字段。独立档案达到 16 个时，角色池会强提醒玩家整理、合并或冷藏低频角色。

## Continuity

每个角色都有 `state.json`，固定核心字段加 `custom` 扩展：

```json
{
  "location": "王都坊市",
  "outfit": "深灰旅行斗篷",
  "visible_appearance": "右肩动作略僵",
  "injuries": [],
  "items": [],
  "current_emotion": "警惕",
  "current_goal": "从围观者中脱身",
  "availability": "active",
  "custom": {}
}
```

地点和当前场景也有状态卡。`current/locations/*.json` 会记录地点名、场景焦点、可见物件、开放冲突和最近回合；生成草稿时会传给 Character Agent、NPC Ensemble、Judge 和 Writer，避免服装、伤势、位置、物品、场景物件漂移。

接受回合时会把更新前的当前场景写入 `scenes/previous_scene.json`，并在 `scenes/scene_log.jsonl` 记录 before/after 摘要，方便下一轮关联当前与上一次场景。

## Tavern Assets

导入酒馆角色卡、世界书、预设时：

- 原文完整保存在 `imports/raw/`。
- 转换为 World Bible、角色池、lore、prompt 层。
- PNG 角色卡通过文本 chunk 中的 `chara`/CCV payload 解析，JSON/TXT 直接转换。
- SillyTavern 世界书 / Lorebook JSON 的 `entries` 会拆成 `current/lore/*.md` 明文条目，保留 keys、secondary keys、constant、selective、order、depth 等元数据。
- 角色卡内嵌 `character_book.entries` 会随角色导入一起落到 lore 层。
- 导入核对使用对话式流程，世界最多 8 问收束，角色可以深入聊。
- 导入后 manifest 会进入 `setup_review_required` 状态，核对确认前不能开始正式推演。
- 当前实现把导入报告问题聚合成 `imports/reports/review-session.json`，PC 端逐问回答并写入 `import-decisions.jsonl`。
- 核对完成时，已回答内容会写回对应角色 `profile.md`、`world_bible.md`、`prompts/imported_preset.md` 或 `import_review_notes.md`，仍可在 GM 文件面板手动编辑。
- 破限/强提示词默认启用到 Character Agent、NPC Ensemble、Writer。
- Judge 和 Memory Writer 不吃或降权处理，避免污染事实和记忆。

## LLM Provider

目标是 OpenAI-compatible 多供应商配置。DeepSeek 可作为默认使用场景，但 provider 层不能写死。DeepSeek context cache 应通过稳定 prompt 前缀利用；本地检索仍由 FTS/结构化状态完成。

当前 MVP 用本地 fallback，确保没 API key 也能完整跑通。
真实 provider 可用时，多 agent prompt 会接收由 SQLite FTS/LIKE 生成的 `relevant_docs` 工作集，而不是全量注入世界文本。
Judge 输出会被归一化，缺失的骰子/无需骰原因、非法 outcome、未知角色结果都会进入 `validation_warnings`，并在 PC 端折叠裁定面板显示。
Memory Writer 在接受草稿后独立生成记忆计划；若 LLM 不可用或返回不合格结构，会退回本地 deterministic fallback，同时把告警写入本轮 memory patch。

## PC GM Panel

PC 端 GM 面板应覆盖：

- 世界基础信息编辑
- LLM 配置状态
- 手动创建分支
- 候选草稿意图摘要
- 导入核对问题
- 当前明文文件浏览
- 当前明文文件编辑
- FTS/LIKE 搜索
- revision 回滚
- 最新正式回合撤回
- 单个记忆/状态 patch 反向应用

回滚默认直接丢弃后续内容；需要保留时用户必须先创建分支。
