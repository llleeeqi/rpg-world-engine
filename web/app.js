const state = {
  worlds: [],
  activeRef: null,
  world: null,
  token: null,
  draft: null,
  editingDraft: null,
  composeInput: null,
  lastImportReport: null,
  llmStatus: null,
  files: [],
  fileFilter: "",
  selectedFile: null,
  searchResults: [],
  error: "",
  notice: "",
  busy: null,
};

const $ = (selector) => document.querySelector(selector);

function tokenKey(ref) {
  return `rpg-token:${ref}`;
}

async function api(path, options = {}) {
  const headers = {
    "Content-Type": "application/json",
    ...(options.headers || {}),
  };
  if (state.activeRef && state.token) headers["X-World-Token"] = state.token;
  const res = await fetch(path, {
    ...options,
    headers,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function option(value, current, label) {
  return `<option value="${escapeHtml(value)}" ${value === current ? "selected" : ""}>${escapeHtml(label)}</option>`;
}

function isBusy(name = "") {
  return Boolean(state.busy && (!name || state.busy === name));
}

function busyAttr(name = "") {
  return state.busy && (!name || state.busy === name) ? "disabled" : "";
}

function busyLabel(name, idleLabel, busyLabelText = "处理中") {
  return isBusy(name) ? busyLabelText : idleLabel;
}

async function runAction(name, label, action, successMessage = null) {
  if (state.busy) return;
  state.busy = name;
  state.error = "";
  state.notice = label;
  render();
  try {
    const result = await action();
    state.error = "";
    if (successMessage !== null) state.notice = successMessage;
    return result;
  } catch (error) {
    state.error = error.message;
    state.notice = "";
  } finally {
    state.busy = null;
    render();
  }
}

function render() {
  document.body.innerHTML = "";
  const app = document.createElement("div");
  app.id = "app";
  app.innerHTML = `
    <div class="shell">
      <header class="topbar">
        <div class="brand">
          <strong>RPG World Engine</strong>
          <span>PC 本地叙事回合</span>
        </div>
        <div class="status">${renderLlmBadge()}${state.busy ? `<span class="pill busy">处理中</span>` : ""}${state.activeRef ? escapeHtml(state.activeRef) : "未打开世界"}</div>
      </header>
      <aside class="rail">${renderRail()}</aside>
      <main class="stage">${renderStage()}</main>
      <aside class="inspector">${renderInspector()}</aside>
    </div>
  `;
  document.body.appendChild(app);
  bindEvents();
}

function renderLlmBadge() {
  if (!state.llmStatus) return "";
  const ready = state.llmStatus.api_key_present;
  const label = ready ? `LLM ${state.llmStatus.default_provider}` : "Fallback";
  return `<span class="pill" title="${escapeHtml(state.llmStatus.config_path)}">${escapeHtml(label)}</span>`;
}

function renderRail() {
  const worlds = state.worlds
    .map((world) => {
      const ref = world.world_id;
      const branches = (world.branches || [])
        .map(
          (branch) => `
            <button class="branch-item ${state.activeRef === branch.ref ? "active" : ""}" data-open-world="${escapeHtml(branch.ref)}">
              <div class="world-name">${escapeHtml(branch.display_name)}</div>
              <div class="meta">分支 · ${escapeHtml(branch.last_played_at || "")}</div>
            </button>
          `,
        )
        .join("");
      return `
        <button class="world-item ${state.activeRef === ref ? "active" : ""}" data-open-world="${escapeHtml(ref)}">
          <div class="world-name">${world.locked ? "锁 " : ""}${escapeHtml(world.display_name)}</div>
          <div class="meta">${escapeHtml(world.subtitle || "无副标题")}<br />${escapeHtml(world.last_played_at || "")}</div>
        </button>
        ${branches ? `<div style="padding-left:14px">${branches}</div>` : ""}
      `;
    })
    .join("");
  return `
    <section class="section">
      <div class="section-title"><span>世界</span><button class="icon" id="refreshWorlds" title="刷新" ${busyAttr("loadWorlds")}>↻</button></div>
      ${worlds || '<div class="empty">还没有世界。</div>'}
    </section>
    <section class="section">
      <div class="section-title">新建世界</div>
      <form id="createWorldForm">
        <label>名称</label>
        <input name="display_name" required placeholder="黑王国" />
        <label>副标题</label>
        <input name="subtitle" placeholder="低魔王权崩坏年代" />
        <label>世界密码</label>
        <input name="password" type="password" placeholder="可留空" />
        <div class="toolbar">
          <button class="primary" type="submit" ${busyAttr("createWorld")}>${busyLabel("createWorld", "创建", "创建中")}</button>
        </div>
      </form>
    </section>
    <section class="section">
      <div class="section-title">导入世界 ZIP</div>
      <form id="importWorldArchiveForm">
        <label>显示名</label>
        <input name="display_name" placeholder="留空则使用导出名称" />
        <label>ZIP 文件</label>
        <input name="file" type="file" accept=".zip" required />
        <div class="toolbar">
          <button type="submit" ${busyAttr("importArchive")}>${busyLabel("importArchive", "导入 ZIP", "导入中")}</button>
        </div>
      </form>
    </section>
    ${state.world ? renderCharacterPanel() : ""}
  `;
}

function renderCharacterPanel() {
  const characters = state.world.characters || [];
  const pool = state.world.agent_pool || {};
  return `
    <section class="section">
      <div class="section-title">角色池</div>
      ${renderAgentPoolNotice(pool)}
      ${
        characters
          .map(
            (char) => `
              <div class="character-item">
                <div class="character-name">${escapeHtml(char.name)}</div>
                <div class="meta">
                  ${escapeHtml(char.role_type)} · ${escapeHtml(char.agent_mode || "independent_default")} · ${escapeHtml(char.availability || "active")}<br />
                  ${escapeHtml(char.location)} · ${escapeHtml(char.current_goal || "")}
                </div>
                <form class="agent-form" data-agent-form="${escapeHtml(char.character_id)}">
                  <div class="agent-grid">
                    <select name="role_type">
                      ${option("controllable", char.role_type, "可控角色")}
                      ${option("major_npc", char.role_type, "重要 NPC")}
                      ${option("background_npc", char.role_type, "后台角色")}
                      ${option("lore_entity", char.role_type, "资料实体")}
                    </select>
                    <select name="agent_mode">
                      ${option("independent_default", char.agent_mode, "独立默认")}
                      ${option("independent_forced", char.agent_mode, "强制独立")}
                      ${option("npc_ensemble", char.agent_mode, "并入 NPC")}
                      ${option("dormant", char.agent_mode, "休眠")}
                      ${option("disabled", char.agent_mode, "禁用")}
                    </select>
                    <select name="availability">
                      ${option("active", char.availability, "场内 active")}
                      ${option("offstage", char.availability, "离场 offstage")}
                      ${option("dormant", char.availability, "冷藏 dormant")}
                      ${option("archived", char.availability, "归档 archived")}
                    </select>
                  </div>
                  <label class="inline-check">
                    <input name="agent_enabled" type="checkbox" ${char.agent_enabled === false ? "" : "checked"} />
                    启用 agent
                  </label>
                  <div class="toolbar left">
                    <button type="submit" ${busyAttr("characterAgent")}>保存 agent</button>
                  </div>
                </form>
              </div>
            `,
          )
          .join("") || '<div class="empty">暂无角色。</div>'
      }
    </section>
    <section class="section">
      <div class="section-title">添加角色</div>
      <form id="createCharacterForm">
        <label>姓名</label>
        <input name="name" required />
        <label>定位</label>
        <select name="role_type">
          <option value="controllable">可控角色</option>
          <option value="major_npc">重要 NPC</option>
          <option value="background_npc">后台角色</option>
          <option value="lore_entity">资料实体</option>
        </select>
        <label>人设</label>
        <textarea name="profile" placeholder="性格、背景、目标、说话习惯"></textarea>
        <button class="primary" type="submit" ${busyAttr("createCharacter")}>${busyLabel("createCharacter", "加入角色池", "加入中")}</button>
      </form>
    </section>
    <section class="section">
      <div class="section-title">导入</div>
      <form id="importForm">
        <label>类型</label>
        <select name="kind">
          <option value="auto">自动判断</option>
          <option value="character">角色</option>
          <option value="world">世界背景/世界书</option>
          <option value="preset">酒馆预设/提示词</option>
          <option value="lore">资料</option>
        </select>
        <label>来源名</label>
        <input name="source_name" placeholder="角色卡或世界书名称" />
        <label>文件</label>
        <input name="file" type="file" accept=".png,.json,.txt,.md" />
        <label>文本</label>
        <textarea name="text" placeholder="粘贴角色卡文本、世界书或预设"></textarea>
        <button type="submit" ${busyAttr("import")}>${busyLabel("import", "导入并生成核对", "导入中")}</button>
      </form>
    </section>
  `;
}

function renderAgentPoolNotice(pool) {
  if (!pool.total_characters) return '<div class="empty">角色池为空。</div>';
  const warning = pool.review_required || pool.over_turn_budget;
  return `
    <div class="${warning ? "notice warning" : "notice"}">
      独立档案 ${pool.independent_profiles || 0}/${pool.independent_review_threshold || 16} ·
      本轮可自动独立 ${pool.active_independent || 0}/${pool.max_active_independent || 8} ·
      NPC Ensemble ${pool.npc_ensemble || 0} · 休眠/停用 ${pool.dormant || 0}
      ${
        warning
          ? `<br />${pool.review_required ? "独立角色已达到强提醒阈值，建议把低频角色并入 NPC 或冷藏。" : ""}${pool.over_turn_budget ? " 本轮会按优先级只拉起前 8 个独立 agent。" : ""}`
          : ""
      }
    </div>
  `;
}

function renderStage() {
  const messages = `
    ${state.error ? `<div class="error">${escapeHtml(state.error)}</div>` : ""}
    ${state.notice ? `<div class="success">${escapeHtml(state.notice)}</div>` : ""}
  `;
  return `${messages}${renderStageContent()}`;
}

function renderStageContent() {
  if (!state.activeRef) {
    return `<div class="empty">选择或创建一个世界。</div>`;
  }
  if (!state.world) {
    return `
      <div class="composer">
        <h2>世界已锁定</h2>
        <form id="unlockWorldForm">
          <label>密码</label>
          <input name="password" type="password" autofocus />
          <div class="toolbar left">
            <button class="primary" type="submit" ${busyAttr("unlock")}>${busyLabel("unlock", "进入", "验证中")}</button>
          </div>
        </form>
      </div>
    `;
  }
  if (state.world.manifest.setup_review_required) {
    return `
      <div class="composer">
        <h2>导入核对未完成</h2>
        <p class="meta">当前世界存在未确认的导入转换。请在右侧“导入核对”里确认世界与角色细节后再开始推演。</p>
      </div>
    `;
  }
  return `
    ${renderComposer()}
    ${state.draft ? renderDraft() : ""}
  `;
}

function characterOptions(selected = "") {
  const chars = state.world?.characters || [];
  return `<option value="">不接管</option>${chars
    .map((char) => `<option value="${escapeHtml(char.character_id)}" ${selected === char.character_id ? "selected" : ""}>${escapeHtml(char.name)}</option>`)
    .join("")}`;
}

function renderComposer() {
  const input = state.composeInput || {
    pace: state.world.manifest.generation_style?.default_pace || "scene",
    divergence: state.world.manifest.generation_style?.default_divergence || "medium",
    controlled_orders: [{}, {}, {}],
    dm_directive: "",
  };
  const slots = [0, 1, 2]
    .map((idx) => {
      const slot = input.controlled_orders?.[idx] || {};
      return `
        <div class="slot">
          <label>角色 ${idx + 1}</label>
          <select name="character_${idx}">${characterOptions(slot.character_id || "")}</select>
          <textarea name="order_${idx}" placeholder="第一人称输入本轮行动">${escapeHtml(slot.text || "")}</textarea>
        </div>
      `;
    })
    .join("");
  return `
    <form class="composer" id="turnForm">
      <div class="section-title">
        <span>${state.editingDraft ? "编辑本轮输入" : "本轮输入"}</span>
        <span class="meta">${state.editingDraft ? `来自候选 ${escapeHtml(state.editingDraft.candidate_id)}` : "最多 3 个第一人称角色槽"}</span>
      </div>
      <div class="turn-grid">${slots}</div>
      <div class="controls">
        <div>
          <label>推进级别</label>
          <select name="pace">
            ${["beat", "scene", "sequence", "downtime"].map((v) => `<option value="${v}" ${input.pace === v ? "selected" : ""}>${v}</option>`).join("")}
          </select>
        </div>
        <div>
          <label>发散程度</label>
          <select name="divergence">
            ${["low", "medium", "high"].map((v) => `<option value="${v}" ${input.divergence === v ? "selected" : ""}>${v}</option>`).join("")}
          </select>
        </div>
        <div>
          <label>DM 指令</label>
          <textarea name="dm_directive" placeholder="本轮走向、硬约束、意外、伏笔">${escapeHtml(input.dm_directive || "")}</textarea>
        </div>
      </div>
      ${
        state.editingDraft
          ? `
            <div class="toolbar">
              <span class="meta">重新生成会沿用上一候选骰子；重新判定会重掷骰子。</span>
              <div class="button-row">
                <button id="cancelEditInput" type="button">取消</button>
                <button class="primary" id="editedInputRegenerate" type="button" ${busyAttr("regenerate")}>${busyLabel("regenerate", "重新生成", "生成中")}</button>
                <button id="editedInputReroll" type="button" ${busyAttr("regenerate")}>${busyLabel("regenerate", "重新判定随机生成", "生成中")}</button>
              </div>
            </div>
          `
          : `
            <div class="toolbar">
              <span class="meta">DM 硬指令会覆盖相关随机判定。</span>
              <button class="primary" type="submit" ${busyAttr("draft")}>${busyLabel("draft", "开始推演", "推演中")}</button>
            </div>
          `
      }
    </form>
  `;
}

function renderDraft() {
  const outcome = state.draft.turn_outcome || {};
  const dice = outcome.dice || [];
  const retrieval = state.draft.retrieval_context || [];
  const warnings = outcome.validation_warnings || [];
  return `
    <section class="draft">
      <div class="section-title">
        <span>候选 ${escapeHtml(state.draft.candidate_id)}</span>
        <span class="meta">${escapeHtml(state.draft.generated_by)}</span>
      </div>
      <div class="narrative">${escapeHtml(state.draft.narrative)}</div>
      <details>
        <summary>本轮事实摘要与裁定</summary>
        <ul class="fact-list">
          ${(outcome.resolved_events || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("")}
        </ul>
        <div>
          ${dice
            .map(
              (check) => `
                <p><span class="pill">骰子</span>${escapeHtml(check.reason)} · DC ${check.difficulty} · ${check.roll}+${check.bonus}=${check.total} · ${escapeHtml(check.outcome)}</p>
                <p class="meta">${escapeHtml(check.explanation)}</p>
              `,
            )
            .join("")}
          ${(outcome.no_roll_reasons || []).map((reason) => `<p><span class="pill">无需骰子</span>${escapeHtml(reason)}</p>`).join("")}
          ${warnings.map((warning) => `<p><span class="pill">校验</span>${escapeHtml(warning)}</p>`).join("")}
        </div>
        ${
          retrieval.length
            ? `
              <h4>检索上下文</h4>
              <ul class="fact-list">
                ${retrieval.map((item) => `<li>${escapeHtml(item.path)}：${escapeHtml(item.snippet || "")}</li>`).join("")}
              </ul>
            `
            : ""
        }
      </details>
      <div class="toolbar">
        <button class="primary" id="acceptDraft" type="button" ${busyAttr("accept")}>${busyLabel("accept", "接受并进入下一轮", "写入中")}</button>
        <button id="toggleRegen" type="button" ${state.busy ? "disabled" : ""}>重新生成</button>
      </div>
      <div class="regen-menu hidden" id="regenMenu">
        <button id="editInput" type="button">编辑本轮输入</button>
        <button data-regen-mode="rewrite" data-keep-dice="true" type="button" ${busyAttr("regenerate")}>只重写正文</button>
        <button data-regen-mode="rerun" data-keep-dice="true" type="button" ${busyAttr("regenerate")}>重跑推演，保留骰子</button>
        <button data-regen-mode="rerun" data-keep-dice="false" type="button" ${busyAttr("regenerate")}>重跑推演，重掷骰子</button>
      </div>
    </section>
  `;
}

function renderInspector() {
  if (!state.world) {
    return `
      <section class="section">
        <div class="section-title">GM 面板</div>
        <div class="empty">解锁世界后显示幕后信息。</div>
      </section>
    `;
  }
  const manifest = state.world.manifest;
  const events = state.world.events || [];
  const dmDirectives = state.world.dm_directives || [];
  const revisions = state.world.revisions || [];
  const patches = state.world.patches || [];
  const review = state.world.import_review || {};
  const outcome = state.draft?.turn_outcome;
  return `
    <section class="section">
      <div class="section-title">世界信息</div>
      <form id="manifestForm">
        <label>名称</label>
        <input name="display_name" value="${escapeHtml(manifest.display_name)}" />
        <label>副标题</label>
        <input name="subtitle" value="${escapeHtml(manifest.subtitle || "")}" />
        <label>拥有者</label>
        <input name="owner_name" value="${escapeHtml(manifest.owner_name || "")}" />
        <div class="toolbar left">
          <button type="submit" ${busyAttr("manifest")}>${busyLabel("manifest", "保存基础信息", "保存中")}</button>
          <button type="button" id="exportWorld" ${busyAttr("export")}>${busyLabel("export", "导出世界 ZIP", "导出中")}</button>
        </div>
      </form>
    </section>
    <section class="section">
      <div class="section-title">LLM</div>
      ${
        state.llmStatus
          ? `
            <div class="event-item">
              <strong>${escapeHtml(state.llmStatus.api_key_present ? "已连接配置" : "本地 fallback")}</strong>
              <div class="meta">
                provider: ${escapeHtml(state.llmStatus.default_provider || "未配置")}<br />
                base: ${escapeHtml(state.llmStatus.base_url || "未配置")}<br />
                key env: ${escapeHtml(state.llmStatus.api_key_env || "未配置")}
              </div>
            </div>
          `
          : '<div class="empty">未读取 LLM 配置。</div>'
      }
    </section>
    <section class="section">
      <div class="section-title">规则</div>
      <div class="event-item">
        <strong>骰子规则 v${escapeHtml(state.world.rules?.version || 1)}</strong>
        <div class="meta">风险触发、强制词、难度表和骰子类型都在明文规则文件里。</div>
        <div class="toolbar left">
          <button type="button" data-file-path="${escapeHtml(state.world.rules?.path || "rules/dice_rules.json")}" ${busyAttr("loadFile")}>编辑骰子规则</button>
        </div>
      </div>
    </section>
    <section class="section">
      <div class="section-title">分支</div>
      <form id="branchForm">
        <label>分支名</label>
        <input name="display_name" placeholder="坊市冲突另一走向" />
        <button type="submit" ${busyAttr("branch")}>${busyLabel("branch", "从当前创建分支", "创建中")}</button>
      </form>
    </section>
    <section class="section">
      <div class="section-title">当前候选</div>
      ${
        outcome
          ? `
            ${(outcome.character_intents || [])
              .map((intent) => `<div class="event-item"><strong>${escapeHtml(intent.name)}</strong><div class="meta">${escapeHtml(intent.source)} · ${escapeHtml(intent.intent)}</div></div>`)
              .join("")}
          `
          : '<div class="empty">暂无候选。</div>'
      }
    </section>
    <section class="section">
      <div class="section-title">DM 记录</div>
      ${
        dmDirectives
          .slice()
          .reverse()
          .map(
            (item) => `
              <div class="event-item">
                <strong>${escapeHtml(item.turn_id || "")}</strong>
                <div class="meta">
                  ${escapeHtml(item.directive || "")}<br />
                  ${item.forced ? "强制走向" : "自然裁定"} · ${item.random_roll_used ? "使用骰子" : "未使用骰子"}
                </div>
              </div>
            `,
          )
          .join("") || '<div class="empty">接受含 DM 指令的回合后显示。</div>'
      }
    </section>
    <section class="section">
      <div class="section-title">导入核对</div>
      ${
        state.lastImportReport
          ? `
            <div class="event-item">
              <strong>${escapeHtml(state.lastImportReport.kind)} · ${escapeHtml(state.lastImportReport.source_name)}</strong>
              <div class="meta">
                ${escapeHtml(state.lastImportReport.raw_path)}<br />
                lore entries: ${state.lastImportReport.converted_lore_entries || 0}
              </div>
            </div>
            <ul class="fact-list">
              ${(state.lastImportReport.questions || []).map((q) => `<li>${escapeHtml(q)}</li>`).join("")}
            </ul>
          `
          : '<div class="empty">导入后显示核对问题。</div>'
      }
      ${renderReviewSession(review)}
      ${
        manifest.setup_review_required
          ? `
            <form id="reviewCompleteForm">
              <label>核对备注</label>
              <textarea name="note" placeholder="例如：世界基调无误，A/B/C 进入可控池，预设默认启用。"></textarea>
              <button class="primary" type="submit" ${busyAttr("reviewComplete")}>${busyLabel("reviewComplete", "确认核对完成，允许开始推演", "确认中")}</button>
            </form>
          `
          : '<div class="meta">导入核对状态：已完成或无需核对。</div>'
      }
    </section>
    <section class="section">
      <div class="section-title">文件 / 检索</div>
      <form id="searchForm">
        <label>全文搜索</label>
        <div class="split">
          <input name="q" placeholder="角色、地点、事件" />
          <button type="submit" ${busyAttr("search")}>${busyLabel("search", "搜索", "搜索中")}</button>
        </div>
      </form>
      <div class="toolbar left">
        <button id="loadFiles" type="button" ${busyAttr("files")}>${busyLabel("files", "加载当前文件", "加载中")}</button>
      </div>
      ${renderSearchResults()}
      ${renderFileList()}
      ${renderSelectedFile()}
    </section>
    <section class="section">
      <div class="section-title">
        <span>历史</span>
        <button id="rebuildIndex" type="button" ${busyAttr("index")}>${busyLabel("index", "重建索引", "重建中")}</button>
      </div>
      <div class="toolbar left">
        <button class="danger" id="undoLatestTurn" type="button" ${busyAttr("undo")}>${busyLabel("undo", "撤回最新正式回合", "撤回中")}</button>
      </div>
      ${
        events
          .slice()
          .reverse()
          .map((event) => `<div class="event-item"><strong>${escapeHtml(event.turn_id)}</strong><div class="meta">${escapeHtml((event.turn_outcome?.resolved_events || [])[0] || "已接受回合")}</div></div>`)
          .join("") || '<div class="empty">还没有正式事件。</div>'
      }
    </section>
    <section class="section">
      <div class="section-title">记忆 / 状态 Patch</div>
      ${
        patches
          .slice()
          .reverse()
          .map(
            (patch) => `
              <div class="event-item">
                <strong>${escapeHtml(patch.turn_id)}</strong>
                <div class="meta">
                  ${patch.writes} writes · ${patch.state_changes} state changes · ${escapeHtml(patch.memory_writer || "legacy")}
                  ${patch.warnings ? ` · ${patch.warnings} warnings` : ""}<br />
                  ${escapeHtml(patch.updated_at)}
                </div>
                <div class="toolbar left">
                  <button type="button" data-file-path="${escapeHtml(patch.path)}" ${busyAttr("loadFile")}>查看</button>
                  <button class="danger" type="button" data-revert-patch="${escapeHtml(patch.turn_id)}" ${busyAttr("revertPatch")}>反向应用</button>
                </div>
              </div>
            `,
          )
          .join("") || '<div class="empty">暂无 patch。</div>'
      }
    </section>
    <section class="section">
      <div class="section-title">Revision 回溯</div>
      ${
        revisions
          .slice()
          .reverse()
          .map(
            (rev) => `
              <div class="event-item">
                <strong>${escapeHtml(rev.revision_id)}</strong>
                <div class="meta">${escapeHtml(rev.kind)} · ${rev.event_count} 轮 · ${escapeHtml(rev.updated_at)}</div>
                <div class="toolbar left">
                  <button class="danger" data-rollback="${escapeHtml(rev.revision_id)}" type="button" ${busyAttr("rollback")}>回到这里</button>
                </div>
              </div>
            `,
          )
          .join("") || '<div class="empty">暂无 revision。</div>'
      }
    </section>
  `;
}

function bindEvents() {
  $("#refreshWorlds")?.addEventListener("click", loadWorlds);
  document.querySelectorAll("[data-open-world]").forEach((button) => {
    button.addEventListener("click", () => openWorld(button.dataset.openWorld));
  });
  $("#createWorldForm")?.addEventListener("submit", createWorld);
  $("#importWorldArchiveForm")?.addEventListener("submit", importWorldArchive);
  $("#unlockWorldForm")?.addEventListener("submit", unlockWorld);
  $("#createCharacterForm")?.addEventListener("submit", createCharacter);
  document.querySelectorAll("[data-agent-form]").forEach((form) => {
    form.addEventListener("submit", updateCharacterAgent);
  });
  $("#importForm")?.addEventListener("submit", importText);
  $("#reviewAnswerForm")?.addEventListener("submit", answerImportReview);
  $("#reviewCompleteForm")?.addEventListener("submit", completeImportReview);
  $("#turnForm")?.addEventListener("submit", submitTurn);
  $("#cancelEditInput")?.addEventListener("click", cancelEditInput);
  $("#editedInputRegenerate")?.addEventListener("click", () => regenerateEditedInput(true));
  $("#editedInputReroll")?.addEventListener("click", () => regenerateEditedInput(false));
  $("#acceptDraft")?.addEventListener("click", acceptDraft);
  $("#toggleRegen")?.addEventListener("click", () => $("#regenMenu")?.classList.toggle("hidden"));
  $("#editInput")?.addEventListener("click", editCurrentInput);
  document.querySelectorAll("[data-regen-mode]").forEach((button) => {
    button.addEventListener("click", () => regenerate(button.dataset.regenMode, button.dataset.keepDice === "true"));
  });
  $("#manifestForm")?.addEventListener("submit", saveManifest);
  $("#exportWorld")?.addEventListener("click", exportWorld);
  $("#branchForm")?.addEventListener("submit", createBranch);
  $("#rebuildIndex")?.addEventListener("click", rebuildIndex);
  $("#undoLatestTurn")?.addEventListener("click", undoLatestTurn);
  $("#loadFiles")?.addEventListener("click", loadFiles);
  $("#fileFilterForm")?.addEventListener("submit", applyFileFilter);
  $("#searchForm")?.addEventListener("submit", searchWorld);
  $("#fileEditForm")?.addEventListener("submit", saveSelectedFile);
  document.querySelectorAll("[data-file-path]").forEach((button) => {
    button.addEventListener("click", () => loadFile(button.dataset.filePath));
  });
  document.querySelectorAll("[data-rollback]").forEach((button) => {
    button.addEventListener("click", () => rollbackToRevision(button.dataset.rollback));
  });
  document.querySelectorAll("[data-revert-patch]").forEach((button) => {
    button.addEventListener("click", () => revertPatch(button.dataset.revertPatch));
  });
}

function renderSearchResults() {
  if (!state.searchResults.length) return "";
  return `
    <div class="section">
      ${state.searchResults
        .map(
          (item) => `
            <button class="event-item" type="button" data-file-path="${escapeHtml(item.path)}" ${busyAttr("loadFile")}>
              <strong>${escapeHtml(item.path)}</strong>
              <div class="meta">${escapeHtml(item.snippet || "")}</div>
            </button>
          `,
        )
        .join("")}
    </div>
  `;
}

function renderReviewSession(review) {
  const questions = review.questions || [];
  if (!questions.length) return "";
  const index = Math.min(review.current_index || 0, questions.length - 1);
  const current = questions[index];
  const answered = questions.filter((question) => question.answer).length;
  if (review.status === "complete") {
    return `<div class="event-item"><strong>核对问题已答完</strong><div class="meta">${answered}/${questions.length}</div></div>`;
  }
  return `
    <div class="event-item">
      <strong>${escapeHtml(current.source_name || current.kind)} · ${answered + 1}/${questions.length}</strong>
      <div class="meta">${escapeHtml(current.question)}</div>
    </div>
    <form id="reviewAnswerForm">
      <label>回答当前核对问题</label>
      <textarea name="answer" placeholder="例如：保留独立角色；当前目标改成调查黑市。"></textarea>
      <button type="submit" ${busyAttr("reviewAnswer")}>${busyLabel("reviewAnswer", "记录回答，下一问", "记录中")}</button>
    </form>
  `;
}

function renderFileList() {
  if (!state.files.length) return "";
  const filter = state.fileFilter.trim().toLowerCase();
  const filtered = filter ? state.files.filter((file) => file.path.toLowerCase().includes(filter)) : state.files;
  const visible = filtered.slice(0, 120);
  return `
    <details open>
      <summary>当前明文文件 · ${visible.length}/${filtered.length}${filtered.length !== state.files.length ? ` / ${state.files.length}` : ""}</summary>
      <form id="fileFilterForm" class="file-filter">
        <label>路径筛选</label>
        <div class="split">
          <input name="file_filter" value="${escapeHtml(state.fileFilter)}" placeholder="characters/a/state.json" />
          <button type="submit">筛选</button>
        </div>
      </form>
      ${
        visible
        .map(
          (file) => `
            <button class="event-item" type="button" data-file-path="${escapeHtml(file.path)}" ${busyAttr("loadFile")}>
              <strong>${escapeHtml(file.path)}</strong>
              <div class="meta">${file.size} bytes · ${escapeHtml(file.updated_at)}</div>
            </button>
          `,
        )
        .join("") || '<div class="empty">没有匹配的文件。</div>'
      }
    </details>
  `;
}

function renderSelectedFile() {
  if (!state.selectedFile) return "";
  return `
    <details open>
      <summary>${escapeHtml(state.selectedFile.path)}</summary>
      <form id="fileEditForm">
        <textarea class="file-editor" name="content">${escapeHtml(state.selectedFile.content)}</textarea>
        <div class="toolbar">
          <span class="meta">${state.selectedFile.size} bytes · 保存会创建 file-edit revision</span>
          <button class="primary" type="submit" ${busyAttr("saveFile")}>${busyLabel("saveFile", "保存文件", "保存中")}</button>
        </div>
      </form>
    </details>
  `;
}

async function loadWorlds() {
  await runAction("loadWorlds", "正在刷新世界列表", async () => {
    const [data, llm] = await Promise.all([api("/api/worlds"), api("/api/config/llm")]);
    state.worlds = data.worlds;
    state.llmStatus = llm;
  }, "世界列表已刷新");
}

async function createWorld(event) {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  await runAction("createWorld", "正在创建世界", async () => {
    const data = await api("/api/worlds", {
      method: "POST",
      body: JSON.stringify(Object.fromEntries(form.entries())),
    });
    state.worlds.unshift(data.manifest);
    state.activeRef = data.manifest.world_id;
    state.token = localStorage.getItem(tokenKey(state.activeRef));
    state.world = null;
    state.draft = null;
    state.editingDraft = null;
    state.composeInput = null;
    await loadActiveWorld();
  }, "世界已创建");
}

async function importWorldArchive(event) {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const file = form.get("file");
  if (!file || !file.size) return;
  await runAction("importArchive", "正在导入世界 ZIP", async () => {
    const data = await api("/api/world-imports", {
      method: "POST",
      body: JSON.stringify({
        display_name: form.get("display_name"),
        file_name: file.name,
        file_base64: await readFileAsDataUrl(file),
      }),
    });
    const worlds = await api("/api/worlds");
    state.worlds = worlds.worlds;
    state.activeRef = data.manifest.world_id;
    state.token = localStorage.getItem(tokenKey(state.activeRef));
    state.world = null;
    state.draft = null;
    state.editingDraft = null;
    state.composeInput = null;
    await loadActiveWorld();
  }, "世界 ZIP 已导入");
}

async function openWorld(ref) {
  await runAction("openWorld", "正在打开世界", async () => {
    state.activeRef = ref;
    state.token = localStorage.getItem(tokenKey(ref));
    state.world = null;
    state.draft = null;
    state.editingDraft = null;
    state.composeInput = null;
    await loadActiveWorld();
  }, "世界已打开");
}

async function loadActiveWorld() {
  if (!state.activeRef) return render();
  state.error = "";
  const data = await api(`/api/worlds/${state.activeRef}`);
  if (data.locked) {
    state.world = null;
  } else {
    state.world = data;
    state.files = [];
    state.fileFilter = "";
    state.selectedFile = null;
    state.searchResults = [];
  }
  render();
}

async function loadFiles() {
  await runAction("files", "正在加载当前文件", async () => {
    const data = await api(`/api/worlds/${state.activeRef}/files`);
    state.files = data.files;
  }, "当前文件已加载");
}

function applyFileFilter(event) {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  state.fileFilter = String(form.get("file_filter") || "");
  render();
}

async function loadFile(path) {
  await runAction("loadFile", `正在读取 ${path}`, async () => {
    const data = await api(`/api/worlds/${state.activeRef}/files/${encodeURIComponent(path)}`);
    state.selectedFile = data.file;
  }, "文件已读取");
}

async function saveSelectedFile(event) {
  event.preventDefault();
  if (!state.selectedFile) return;
  const form = new FormData(event.currentTarget);
  const path = state.selectedFile.path;
  await runAction("saveFile", `正在保存 ${path}`, async () => {
    await api(`/api/worlds/${state.activeRef}/files/${encodeURIComponent(path)}`, {
      method: "PATCH",
      body: JSON.stringify({ content: form.get("content") }),
    });
    await loadActiveWorld();
    const filesData = await api(`/api/worlds/${state.activeRef}/files`);
    state.files = filesData.files;
    const fileData = await api(`/api/worlds/${state.activeRef}/files/${encodeURIComponent(path)}`);
    state.selectedFile = fileData.file;
  }, "文件已保存");
}

async function searchWorld(event) {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const q = form.get("q");
  if (!q) return;
  await runAction("search", "正在搜索", async () => {
    const data = await api(`/api/worlds/${state.activeRef}/search?q=${encodeURIComponent(q)}`);
    state.searchResults = data.results;
  }, "搜索完成");
}

async function unlockWorld(event) {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  await runAction("unlock", "正在验证密码", async () => {
    const data = await api(`/api/worlds/${state.activeRef}/unlock`, {
      method: "POST",
      body: JSON.stringify({ password: form.get("password") }),
    });
    state.token = data.token;
    localStorage.setItem(tokenKey(state.activeRef), data.token);
    await loadActiveWorld();
  }, "世界已解锁");
}

async function createCharacter(event) {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  await runAction("createCharacter", "正在创建角色", async () => {
    await api(`/api/worlds/${state.activeRef}/characters`, {
      method: "POST",
      body: JSON.stringify(Object.fromEntries(form.entries())),
    });
    await loadActiveWorld();
  }, "角色已加入角色池");
}

async function updateCharacterAgent(event) {
  event.preventDefault();
  const formElement = event.currentTarget;
  const form = new FormData(formElement);
  const payload = Object.fromEntries(form.entries());
  payload.agent_enabled = form.has("agent_enabled");
  await runAction("characterAgent", "正在保存角色 agent 设置", async () => {
    await api(`/api/worlds/${state.activeRef}/characters/${encodeURIComponent(formElement.dataset.agentForm)}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
    await loadActiveWorld();
  }, "角色 agent 设置已保存");
}

async function importText(event) {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const payload = Object.fromEntries(form.entries());
  delete payload.file;
  const file = form.get("file");
  await runAction("import", "正在导入并转换", async () => {
    if (file && file.size) {
      payload.file_name = file.name;
      payload.file_base64 = await readFileAsDataUrl(file);
    }
    const data = await api(`/api/worlds/${state.activeRef}/imports`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    state.lastImportReport = data.report;
    await loadActiveWorld();
  }, "导入完成，等待核对");
}

async function completeImportReview(event) {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  await runAction("reviewComplete", "正在完成导入核对", async () => {
    await api(`/api/worlds/${state.activeRef}/import-review-complete`, {
      method: "POST",
      body: JSON.stringify({ note: form.get("note") }),
    });
    await loadActiveWorld();
  }, "导入核对已完成");
}

async function answerImportReview(event) {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  await runAction("reviewAnswer", "正在记录核对回答", async () => {
    await api(`/api/worlds/${state.activeRef}/import-review-answer`, {
      method: "POST",
      body: JSON.stringify({ answer: form.get("answer") }),
    });
    await loadActiveWorld();
  }, "回答已记录");
}

function readFileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

function collectTurnInput(formElement) {
  const form = new FormData(formElement);
  const controlled_orders = [0, 1, 2]
    .map((idx) => ({
      slot: idx + 1,
      character_id: form.get(`character_${idx}`),
      perspective: "first_person",
      text: form.get(`order_${idx}`),
    }))
    .filter((order) => order.character_id || order.text);
  return {
    pace: form.get("pace"),
    divergence: form.get("divergence"),
    controlled_orders,
    dm_directive: form.get("dm_directive"),
  };
}

async function submitTurn(event) {
  event.preventDefault();
  const input = collectTurnInput(event.currentTarget);
  state.composeInput = input;
  await runAction("draft", "正在推演本轮", async () => {
    const data = await api(`/api/worlds/${state.activeRef}/drafts`, {
      method: "POST",
      body: JSON.stringify({ input }),
    });
    state.draft = data.draft;
    state.editingDraft = null;
  }, "候选草稿已生成");
}

async function acceptDraft() {
  if (!state.draft) return;
  await runAction("accept", "正在写入正式世界", async () => {
    await api(`/api/worlds/${state.activeRef}/drafts/${state.draft.turn_id}/${state.draft.candidate_id}/accept`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    state.draft = null;
    state.editingDraft = null;
    state.composeInput = null;
    await loadActiveWorld();
  }, "已接受并进入下一轮");
}

function editCurrentInput() {
  if (!state.draft) return;
  state.editingDraft = state.draft;
  state.composeInput = state.draft.input;
  state.draft = null;
  render();
}

function cancelEditInput() {
  if (!state.editingDraft) return;
  state.draft = state.editingDraft;
  state.composeInput = state.editingDraft.input;
  state.editingDraft = null;
  render();
}

async function regenerateEditedInput(keepDice) {
  if (!state.editingDraft) return;
  const form = $("#turnForm");
  if (!form) return;
  const input = collectTurnInput(form);
  state.composeInput = input;
  await runAction("regenerate", "正在按编辑后的输入重新生成候选", async () => {
    const data = await api(`/api/worlds/${state.activeRef}/drafts`, {
      method: "POST",
      body: JSON.stringify({
        turn_id: state.editingDraft.turn_id,
        input,
        previous_candidate: {
          turn_id: state.editingDraft.turn_id,
          candidate_id: state.editingDraft.candidate_id,
        },
        mode: "rerun",
        keep_dice: keepDice,
      }),
    });
    state.draft = data.draft;
    state.editingDraft = null;
  }, keepDice ? "已按编辑输入重新生成" : "已按编辑输入重新判定");
}

async function regenerate(mode, keepDice) {
  if (!state.draft) return;
  await runAction("regenerate", "正在重新生成候选", async () => {
    const data = await api(`/api/worlds/${state.activeRef}/drafts`, {
      method: "POST",
      body: JSON.stringify({
        turn_id: state.draft.turn_id,
        input: state.draft.input,
        previous_candidate: {
          turn_id: state.draft.turn_id,
          candidate_id: state.draft.candidate_id,
        },
        mode,
        keep_dice: keepDice,
      }),
    });
    state.draft = data.draft;
    state.editingDraft = null;
  }, "候选已重新生成");
}

async function saveManifest(event) {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  await runAction("manifest", "正在保存世界信息", async () => {
    await api(`/api/worlds/${state.activeRef}`, {
      method: "PATCH",
      body: JSON.stringify(Object.fromEntries(form.entries())),
    });
    const [data, llm] = await Promise.all([api("/api/worlds"), api("/api/config/llm")]);
    state.worlds = data.worlds;
    state.llmStatus = llm;
    await loadActiveWorld();
  }, "世界信息已保存");
}

async function createBranch(event) {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  await runAction("branch", "正在创建分支", async () => {
    await api(`/api/worlds/${state.activeRef}/branches`, {
      method: "POST",
      body: JSON.stringify(Object.fromEntries(form.entries())),
    });
    const data = await api("/api/worlds");
    state.worlds = data.worlds;
    await loadActiveWorld();
  }, "分支已创建");
}

async function exportWorld() {
  await runAction("export", "正在打包世界", async () => {
    const headers = {};
    if (state.activeRef && state.token) headers["X-World-Token"] = state.token;
    const res = await fetch(`/api/worlds/${state.activeRef}/export`, { headers });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.error || res.statusText);
    }
    const blob = await res.blob();
    const disposition = res.headers.get("Content-Disposition") || "";
    const match = disposition.match(/filename\*=UTF-8''([^;]+)/);
    const filename = match ? decodeURIComponent(match[1]) : `${state.activeRef || "world"}.zip`;
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  }, "世界 ZIP 已生成");
}

async function rollbackToRevision(revisionId) {
  if (!revisionId) return;
  const ok = window.confirm(`回到 ${revisionId} 会丢弃之后的所有主线内容。需要保留的话先创建分支。继续？`);
  if (!ok) return;
  await runAction("rollback", "正在回滚 revision", async () => {
    await api(`/api/worlds/${state.activeRef}/rollback/${revisionId}`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    state.draft = null;
    state.editingDraft = null;
    state.composeInput = null;
    await loadActiveWorld();
  }, "已回到指定 revision");
}

async function undoLatestTurn() {
  const ok = window.confirm("撤回最新正式回合会丢弃该回合及其后的线性内容。需要保留的话先创建分支。继续？");
  if (!ok) return;
  await runAction("undo", "正在撤回最新正式回合", async () => {
    await api(`/api/worlds/${state.activeRef}/undo-latest-turn`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    state.draft = null;
    state.editingDraft = null;
    state.composeInput = null;
    await loadActiveWorld();
  }, "最新正式回合已撤回");
}

async function revertPatch(turnId) {
  if (!turnId) return;
  const ok = window.confirm(`反向应用 ${turnId} 的记忆/状态 patch？这会创建 patch-revert revision。`);
  if (!ok) return;
  await runAction("revertPatch", "正在反向应用 patch", async () => {
    await api(`/api/worlds/${state.activeRef}/patches/${turnId}`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    await loadActiveWorld();
  }, "patch 已反向应用");
}

async function rebuildIndex() {
  await runAction("index", "正在重建索引", async () => {
    const result = await api(`/api/worlds/${state.activeRef}/index`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    state.notice = `索引已重建：${result.indexed} 个文件`;
  });
}

loadWorlds();
