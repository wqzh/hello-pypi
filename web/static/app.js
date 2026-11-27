/* ==========================================================================
 * Pi Agent · 前端交互
 * 依赖后端协议：web/events.py
 *
 * 交互约定
 *   - 一轮对话 = 一条用户消息 + 其后的思考 / 工具调用 / 多段回答
 *   - 中间步骤（思考 + 工具卡）收进折叠区，回答气泡才是最终结果
 *   - 每条消息附「年月日 时分秒」；assistant 额外显示 token 与缓存命中率
 * ========================================================================== */

const API = "";

const el = (tag, className = "", text = "") => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== "" && text !== undefined && text !== null) node.textContent = String(text);
  return node;
};

const $ = (id) => document.getElementById(id);

const TOOL_META = {
  get_city_weather: { label: "查询城市天气", icon: "☁" },
  web_search: { label: "联网搜索", icon: "🔎" },
  bash: { label: "执行命令", icon: "⌘" },
  read: { label: "读取文件", icon: "📄" },
  write: { label: "写入文件", icon: "💾" },
  edit: { label: "编辑文件", icon: "✏️" },
  ls: { label: "列出目录", icon: "📁" },
  find: { label: "查找文件", icon: "🔍" },
  grep: { label: "内容检索", icon: "🧭" },
};

const QUICK_CARDS = [
  { icon: "☀", name: "查天气", desc: "北京明天的天气怎么样", prompt: "北京明天的天气怎么样？" },
  { icon: "🔎", name: "联网搜索", desc: "搜索最新的技术动态", prompt: "帮我搜索一下今天 AI 领域有哪些重要新闻" },
  { icon: "📄", name: "读文件", desc: "读取并总结 README", prompt: "读一下 README.md，并用三句话总结它" },
  { icon: "🗂", name: "制定计划", desc: "把目标拆成可执行步骤", prompt: "帮我制定一份为期两周的学习计划" },
  { icon: "🧩", name: "解释代码", desc: "逐行说明逻辑与风险", prompt: "解释一下这段代码的逻辑，并指出潜在风险：" },
  { icon: "💡", name: "头脑风暴", desc: "围绕问题列出多种思路", prompt: "给我 5 个适合个人开发者的小产品创意" },
];

const state = {
  sessions: [],
  sid: null,
  ws: null,
  wsSeq: 0,
  openSeq: 0,
  sending: false,
  stick: true,
  query: "",
  run: null,
  tools: new Map(),
  meta: null,
  models: [],
  modelKey: "",
  view: "chat",
  testing: new Set(),
  reconnectTimer: null,
  refreshTimer: null,
};

const dom = {};

/* ---------------------------------------------------------------- 启动 */

document.addEventListener("DOMContentLoaded", () => {
  dom.sessionList = $("sessionList");
  dom.messages = $("messages");
  dom.scroll = $("scroll");
  dom.welcome = $("welcome");
  dom.input = $("input");
  dom.sendBtn = $("sendBtn");
  dom.stopBtn = $("stopBtn");
  dom.newBtn = $("newBtn");
  dom.clearBtn = $("clearBtn");
  dom.title = $("title");
  dom.subtitle = $("subtitle");
  dom.busyBadge = $("busyBadge");
  dom.connDot = $("connDot");
  dom.connText = $("connText");
  dom.searchInput = $("searchInput");
  dom.quickCards = $("quickCards");
  dom.modelSelect = $("modelSelect");
  dom.toolChip = $("toolChip");
  dom.toolChipText = $("toolChipText");
  dom.toolsPop = $("toolsPop");
  dom.toolsList = $("toolsList");
  dom.toolsCount = $("toolsCount");
  dom.toolsClose = $("toolsClose");
  dom.main = $("main");
  dom.navChat = $("navChat");
  dom.navModels = $("navModels");
  dom.modelsView = $("modelsView");
  dom.modelList = $("modelList");
  dom.modelsSub = $("modelsSub");
  dom.testAllBtn = $("testAllBtn");

  renderQuickCards();
  bindEvents();
  setView("chat");
  loadMeta().then(refreshSessions).then(() => {
    if (state.sessions.length > 0) {
      openSession(state.sessions[0].id);
    } else {
      resetView();
    }
  });
});

function bindEvents() {
  dom.navChat.addEventListener("click", () => setView("chat"));
  dom.navModels.addEventListener("click", () => setView("models"));
  dom.testAllBtn.addEventListener("click", testAllModels);

  dom.newBtn.addEventListener("click", () => createSession());
  dom.sendBtn.addEventListener("click", send);
  dom.stopBtn.addEventListener("click", abortRun);
  dom.clearBtn.addEventListener("click", () => {
    if (state.sid) deleteSession(state.sid);
  });

  dom.input.addEventListener("input", autoResize);
  dom.input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      send();
    }
  });

  dom.searchInput.addEventListener("input", () => {
    state.query = dom.searchInput.value.trim().toLowerCase();
    renderSessions();
  });

  dom.scroll.addEventListener("scroll", () => {
    const distance =
      dom.scroll.scrollHeight - dom.scroll.scrollTop - dom.scroll.clientHeight;
    state.stick = distance < 90;
  });

  dom.modelSelect.addEventListener("change", () => {
    const key = dom.modelSelect.value;
    if (!key || key === state.modelKey) return;
    if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
      dom.modelSelect.value = state.modelKey;
      setConn(false, "连接未就绪，无法切换模型");
      return;
    }
    state.ws.send(JSON.stringify({ type: "set_model", model: key }));
  });

  dom.toolChip.addEventListener("click", (event) => {
    event.stopPropagation();
    dom.toolsPop.classList.toggle("hidden");
  });
  dom.toolsClose.addEventListener("click", () => dom.toolsPop.classList.add("hidden"));
  document.addEventListener("click", (event) => {
    if (dom.toolsPop.classList.contains("hidden")) return;
    if (dom.toolsPop.contains(event.target) || dom.toolChip.contains(event.target)) return;
    dom.toolsPop.classList.add("hidden");
  });
}

async function loadMeta() {
  try {
    const meta = await apiGet("/api/meta");
    state.meta = meta;
    state.models = meta.models || [];
    state.modelKey = meta.model || "";
    renderModelSelect(state.models, state.modelKey);
    renderTools(meta.tools || []);
    dom.subtitle.textContent = `${meta.provider || "Pi"} · 流式输出 · 工具调用`;
    if (state.view === "models") renderModelRows();
  } catch (error) {
    console.warn("加载元信息失败", error);
  }
}

function renderModelSelect(models, selectedKey) {
  dom.modelSelect.innerHTML = "";
  for (const model of models) {
    const option = document.createElement("option");
    option.value = model.key;
    option.textContent = model.label || model.model || model.key;
    dom.modelSelect.append(option);
  }
  if (selectedKey && models.some((m) => m.key === selectedKey)) {
    dom.modelSelect.value = selectedKey;
  }
}

function selectModel(key) {
  state.modelKey = key || state.modelKey;
  if (key && state.models.some((m) => m.key === key)) {
    dom.modelSelect.value = key;
  }
}

function renderTools(tools) {
  dom.toolChipText.textContent = `${tools.length} 个工具`;
  dom.toolsCount.textContent = `（${tools.length}）`;
  dom.toolsList.innerHTML = "";
  for (const tool of tools) {
    const meta = TOOL_META[tool.name] || {};
    const row = el("div", "tool-item");
    row.append(el("span", "tool-item-ico", meta.icon || "🛠"));
    const body = el("div", "tool-item-body");
    body.append(el("span", "tool-item-name", meta.label || tool.label || tool.name));
    body.append(el("code", "tool-item-code", tool.name));
    if (tool.description) {
      body.append(el("span", "tool-item-desc", tool.description));
    }
    row.append(body);
    dom.toolsList.append(row);
  }
}

function renderQuickCards() {
  for (const card of QUICK_CARDS) {
    const button = el("button", "quick-card");
    button.type = "button";
    button.append(el("span", "quick-ico", card.icon));
    const body = el("span", "quick-body");
    body.append(el("span", "quick-name", card.name));
    body.append(el("span", "quick-desc", card.desc));
    button.append(body);
    button.addEventListener("click", () => {
      dom.input.value = card.prompt;
      dom.input.focus();
      autoResize();
      dom.input.setSelectionRange(card.prompt.length, card.prompt.length);
    });
    dom.quickCards.append(button);
  }
}

/* ---------------------------------------------------------------- API */

async function apiGet(path) {
  const response = await fetch(API + path);
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

async function apiSend(path, options) {
  const response = await fetch(API + path, options);
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

/* ---------------------------------------------------------------- 时间格式 */

const pad2 = (n) => String(n).padStart(2, "0");

/** 毫秒 → 2026-11-27 15:08 */
function fmtMinute(ms) {
  if (!ms) return "";
  const d = new Date(ms);
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
}

/** 毫秒 → 2026-11-27 15:08:32 */
function fmtSecond(ms) {
  if (!ms) return "";
  const d = new Date(ms);
  return `${fmtMinute(ms)}:${pad2(d.getSeconds())}`;
}

function fmtNumber(value) {
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) return "0";
  return n.toLocaleString("en-US");
}

/* ---------------------------------------------------------------- 会话列表 */

async function refreshSessions() {
  try {
    state.sessions = await apiGet("/api/sessions");
    renderSessions();
  } catch (error) {
    console.warn("加载会话失败", error);
    setConn(false, "加载失败");
  }
}

function scheduleRefresh() {
  clearTimeout(state.refreshTimer);
  state.refreshTimer = setTimeout(refreshSessions, 400);
}

function renderSessions() {
  dom.sessionList.innerHTML = "";
  const list = state.sessions.filter((item) => {
    if (!state.query) return true;
    return `${item.title} ${item.preview}`.toLowerCase().includes(state.query);
  });

  if (list.length === 0) {
    dom.sessionList.append(el("div", "rail-empty", "还没有对话\n点上方「新对话」开始"));
    return;
  }

  for (const item of list) {
    const row = el("div", "session-item" + (item.id === state.sid ? " active" : ""));
    row.dataset.id = item.id;

    const body = el("div", "session-body");
    body.append(el("span", "session-title", item.title || "新对话"));

    const metaRow = el("span", "session-meta");
    // 年月日-时分
    metaRow.append(el("span", "session-time", fmtMinute((item.updated_at || 0) * 1000)));
    if (item.busy) metaRow.append(el("span", "session-busy", "生成中"));
    body.append(metaRow);

    const del = el("button", "session-del", "✕");
    del.type = "button";
    del.title = "删除该对话";
    del.addEventListener("click", (event) => {
      event.stopPropagation();
      deleteSession(item.id);
    });

    row.append(body, del);
    // 模型信息放 hover 提示，避免时间行拥挤
    row.title = [item.title, item.model_label].filter(Boolean).join("\n");
    row.addEventListener("click", () => openSession(item.id));
    dom.sessionList.append(row);
  }
}

function setActiveSession() {
  for (const row of dom.sessionList.children) {
    row.classList.toggle("active", row.dataset.id === state.sid);
  }
}

/* ---------------------------------------------------------------- 会话切换 */

async function createSession() {
  try {
    const payload = { title: "新对话" };
    if (state.modelKey) payload.model = state.modelKey;
    const data = await apiSend("/api/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    await refreshSessions();
    await openSession(data.id);
  } catch (error) {
    appendError(`新建对话失败：${error.message}`);
  }
}

async function openSession(sid) {
  const sequence = ++state.openSeq;
  state.sid = sid;
  setActiveSession();
  closeWs();
  endRun({ removeEmpty: true });

  let data;
  try {
    data = await apiGet(`/api/sessions/${sid}/messages`);
  } catch (error) {
    if (sequence === state.openSeq) appendError(`加载对话失败：${error.message}`);
    return;
  }
  if (sequence !== state.openSeq) return;

  setTitle(data.title || "对话");
  if (data.model_key) selectModel(data.model_key);
  renderHistory(data.messages || []);
  setBusy(Boolean(data.busy));
  connect(sid);
}

async function deleteSession(sid) {
  if (!window.confirm("删除后这条对话记录不可恢复，确定删除？")) return;
  try {
    await apiSend(`/api/sessions/${sid}`, { method: "DELETE" });
  } catch (error) {
    appendError(`删除失败：${error.message}`);
    return;
  }
  if (state.sid === sid) {
    state.sid = null;
    closeWs();
    resetView();
  }
  await refreshSessions();
}

function resetView() {
  dom.title.textContent = "新对话";
  dom.messages.innerHTML = "";
  dom.welcome.classList.remove("hidden");
  endRun({ removeEmpty: true });
  state.tools.clear();
  setBusy(false);
}

function setTitle(title) {
  dom.title.textContent = title || "新对话";
}

/* ---------------------------------------------------------------- WebSocket */

function connect(sid) {
  closeWs();
  state.reconnectTimer && clearTimeout(state.reconnectTimer);

  const protocol = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${protocol}://${location.host}/ws/${sid}`);
  const sequence = ++state.wsSeq;
  state.ws = ws;

  ws.addEventListener("open", () => {
    if (sequence === state.wsSeq) setConn(true);
  });

  ws.addEventListener("close", () => {
    if (sequence !== state.wsSeq || state.sid !== sid) return;
    setConn(false);
    state.reconnectTimer = setTimeout(async () => {
      if (state.sid !== sid) return;
      try {
        const data = await apiGet(`/api/sessions/${sid}/messages`);
        if (state.sid !== sid) return;
        renderHistory(data.messages || []);
        setTitle(data.title || "对话");
        if (data.model_key) selectModel(data.model_key);
        setBusy(Boolean(data.busy));
      } catch (error) {
        /* 服务未就绪，继续重试 */
      }
      connect(sid);
    }, 1500);
  });

  ws.addEventListener("error", () => setConn(false));

  ws.addEventListener("message", (event) => {
    let payload;
    try {
      payload = JSON.parse(event.data);
    } catch (error) {
      return;
    }
    handleEvent(payload);
  });
}

function closeWs() {
  if (!state.ws) return;
  state.ws.onclose = null;
  try {
    state.ws.close();
  } catch (error) {
    /* ignore */
  }
  state.ws = null;
}

function setConn(online, text) {
  dom.connDot.classList.toggle("on", online);
  dom.connDot.classList.toggle("off", !online);
  dom.connText.textContent = text || (online ? "已连接" : "连接断开，正在重连…");
}

/* ---------------------------------------------------------------- 事件分发 */

function handleEvent(data) {
  switch (data.type) {
    case "user":
      hideWelcome();
      endRun({ removeEmpty: true });
      appendUser(data.text, data.ts);
      if (data.title) setTitle(data.title);
      scheduleRefresh();
      break;

    case "busy":
      setBusy(Boolean(data.value));
      if (data.value) showTyping();
      else clearTyping();
      break;

    case "message_start":
      if (data.role === "assistant") beginAssistantMessage();
      break;

    case "text_delta":
      clearTyping();
      appendDelta(data.delta || "");
      break;

    case "thinking_start":
      clearTyping();
      startThinking();
      break;

    case "thinking_delta":
      appendThinking(data.delta || "");
      break;

    case "thinking_end":
      endThinking();
      break;

    case "tool_start":
      addTool(data);
      break;

    case "tool_update":
      updateTool(data);
      break;

    case "tool_end":
      finishTool(data);
      break;

    case "message_end":
      if (data.role === "assistant") {
        clearTyping();
        finishAssistantMessage(data);
      }
      break;

    case "model":
      selectModel(data.key);
      appendSystem(`已切换模型：${data.label || data.key}`);
      break;

    case "compact":
      appendSystem(`上下文已压缩：${data.before} → ${data.after} tokens`);
      break;

    case "warn":
      appendError(data.message);
      break;

    case "error":
      clearTyping();
      appendError(data.message);
      endRun({ removeEmpty: true });
      setBusy(false);
      break;

    case "done": {
      clearTyping();
      const run = state.run;
      const noAnswer = Boolean(run && !run.hasAnswer);
      const hasSteps = Boolean(run && run.count > 0);
      collapseSteps();
      if (noAnswer && hasSteps) {
        // 只有思考、没有正文：展开思考并明确提示，避免界面一片空白
        run.steps.open = true;
        run.answers.append(
          el(
            "div",
            "empty-hint",
            "模型只返回了思考内容，没有生成回答（通常是 max_tokens 被思考耗尽，可在 .env 调大 PI_LLM_MAX_TOKENS）。"
          )
        );
      } else if (noAnswer) {
        // 连思考都没有：本轮完全空白，给一条可见提示
        appendError("模型本轮没有返回任何内容（content 为空），请重试或换用其他模型。");
      }
      endRun({ removeEmpty: true });
      setBusy(false);
      scheduleRefresh();
      break;
    }

    case "deleted":
      if (state.sid === data.id) {
        state.sid = null;
        closeWs();
        resetView();
      }
      refreshSessions();
      break;

    default:
      break;
  }
}

/* ---------------------------------------------------------------- 一轮对话容器 */

function createRun() {
  const root = el("div", "msg assistant run");
  root.append(el("div", "avatar", "✦"));

  const body = el("div", "body run-body");

  const steps = document.createElement("details");
  steps.className = "steps hidden";
  steps.open = true;

  const summary = document.createElement("summary");
  summary.append(el("span", "steps-dot"));
  const summaryText = el("span", "steps-text", "执行中…");
  summary.append(summaryText);

  const stepsBody = el("div", "steps-body");
  steps.append(summary, stepsBody);

  const answers = el("div", "answers");
  body.append(steps, answers);
  root.append(body);
  dom.messages.append(root);

  return {
    root,
    body,
    steps,
    summaryText,
    stepsBody,
    answers,
    count: 0,
    textEl: null,
    textBuffer: "",
    hasAnswer: false,
    thinkingEl: null,
    thinkStart: 0,
    typingEl: null,
  };
}

function ensureRun() {
  if (!state.run) state.run = createRun();
  return state.run;
}

function endRun(options = {}) {
  const run = state.run;
  if (!run) return;
  if (run.typingEl) {
    run.typingEl.remove();
    run.typingEl = null;
  }
  if (
    options.removeEmpty &&
    run.count === 0 &&
    run.answers.childNodes.length === 0
  ) {
    run.root.remove();
  }
  state.run = null;
}

function collapseSteps() {
  for (const node of dom.messages.querySelectorAll(".steps")) {
    node.open = false;
  }
}

function updateStepsSummary(run) {
  if (!run) return;
  run.steps.classList.toggle("hidden", run.count === 0);
  if (run.count === 0) return;
  run.summaryText.textContent = `执行了 ${run.count} 个步骤`;
  run.steps.classList.toggle("error", Boolean(run.hasError));
}

function addStepNode(node) {
  const run = ensureRun();
  run.count += 1;
  run.stepsBody.append(node);
  updateStepsSummary(run);
  if (run.steps.open === false && state.sending) run.steps.open = true;
  scrollBottom();
}

/* ---------------------------------------------------------------- 流式渲染 */

function hideWelcome() {
  dom.welcome.classList.add("hidden");
}

function showTyping() {
  const run = ensureRun();
  if (run.textEl || run.typingEl) return;
  const typing = el("div", "typing");
  typing.append(el("i"), el("i"), el("i"));
  run.answers.append(typing);
  run.typingEl = typing;
}

function clearTyping() {
  const run = state.run;
  if (run && run.typingEl) {
    run.typingEl.remove();
    run.typingEl = null;
  }
}

function beginAssistantMessage() {
  const run = ensureRun();
  run.textEl = null;
  run.textBuffer = "";
}

function appendDelta(delta) {
  if (!delta) return;
  const run = ensureRun();
  if (!run.textEl) {
    run.textEl = el("div", "chat-text");
    run.answers.append(run.textEl);
  }
  run.textBuffer += delta;
  run.hasAnswer = true;
  run.textEl.innerHTML = renderMarkdown(run.textBuffer);
  scrollBottom();
}

function finishAssistantMessage(data) {
  const run = state.run;
  if (!run) return;
  const usage = data.usage || {};
  if (run.textEl) {
    run.answers.append(buildMessageMeta(Date.now(), usage));
  } else if (run.count === 0 && run.answers.childNodes.length === 0) {
    updateStepsSummary(run);
  }
  run.textEl = null;
  run.textBuffer = "";
  updateStepsSummary(run);
  scrollBottom();
}

/* ---------------------------------------------------------------- 思考过程 */

function startThinking() {
  const run = ensureRun();
  if (run.thinkingEl) return;

  const details = document.createElement("details");
  details.className = "step step-think";
  const summary = document.createElement("summary");
  summary.append(el("span", "step-ico", "✳"));
  const label = el("span", "step-title", "思考中…");
  summary.append(label, el("span", "spin"));
  const body = el("div", "step-body");
  details.append(summary, body);

  run.thinkingEl = details;
  run.thinkLabel = label;
  run.thinkStart = Date.now();
  addStepNode(details);
}

function appendThinking(delta) {
  if (!delta) return;
  const run = state.run;
  if (!run || !run.thinkingEl) startThinking();
  const body = state.run.thinkingEl.querySelector(".step-body");
  if (body) {
    body.textContent += delta;
    scrollBottom();
  }
}

function endThinking() {
  const run = state.run;
  if (!run || !run.thinkingEl) return;
  const seconds = ((Date.now() - run.thinkStart) / 1000).toFixed(1);
  if (run.thinkLabel) run.thinkLabel.textContent = `思考 ${seconds}s`;
  const spinner = run.thinkingEl.querySelector(".spin");
  if (spinner) spinner.remove();
  // 思考结束立即折叠（用户可随时展开）；若本轮无正文，done 时会重新展开并提示
  run.thinkingEl.open = false;
  run.thinkingEl = null;
  run.thinkLabel = null;
}

/* ---------------------------------------------------------------- 消息 meta */

function buildMessageMeta(ms, usage) {
  const meta = el("div", "msg-meta");
  meta.append(el("span", "meta-time", fmtSecond(ms)));

  if (usage && (usage.input || usage.output)) {
    meta.append(el("span", "meta-dot", "·"));
    meta.append(el("span", "meta-token", `输入 ${fmtNumber(usage.input)}`));
    meta.append(el("span", "meta-dot", "·"));
    meta.append(el("span", "meta-token", `输出 ${fmtNumber(usage.output)}`));
    if (usage.cache_read) {
      const rate = Number(usage.cache_hit_rate || 0) * 100;
      meta.append(el("span", "meta-dot", "·"));
      const cache = el(
        "span",
        "meta-token meta-cache",
        `缓存命中 ${rate.toFixed(0)}%`
      );
      cache.title = `缓存读取 ${fmtNumber(usage.cache_read)} / 缓存写入 ${fmtNumber(usage.cache_write)}`;
      meta.append(cache);
    }
  }
  return meta;
}

/* ---------------------------------------------------------------- 工具卡 */

function toolMeta(name) {
  return TOOL_META[name] || { label: name || "工具", icon: "🛠" };
}

function normalizeArgs(args) {
  if (args === null || args === undefined) return [];
  let value = args;
  if (typeof value === "string") {
    try {
      value = JSON.parse(value);
    } catch (error) {
      return [["参数", value]];
    }
  }
  if (typeof value !== "object" || Array.isArray(value)) return [["参数", String(value)]];
  return Object.entries(value);
}

function prettyArgs(args) {
  let value = args;
  if (typeof value === "string") {
    try {
      value = JSON.parse(value);
    } catch (error) {
      return String(args);
    }
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch (error) {
    return String(value);
  }
}

function argChip(key, value) {
  const chip = el("span", "arg-chip");
  chip.append(el("b", "", `${key}=`));
  const text = typeof value === "object" ? JSON.stringify(value) : String(value);
  chip.append(document.createTextNode(text.length > 80 ? `${text.slice(0, 80)}…` : text));
  return chip;
}

function statusNode(status) {
  const node = el("span", "tool-status");
  if (status === "running") {
    node.append(el("span", "spin"), document.createTextNode("运行中"));
  } else if (status === "error") {
    node.textContent = "失败";
  } else {
    node.textContent = "完成";
  }
  return node;
}

function block(labelText, preText, withCopy = false) {
  const wrapper = el("div", "tool-block");
  const label = el("div", "block-label", labelText);
  if (withCopy) {
    const copy = el("button", "copy-btn", "复制");
    copy.type = "button";
    copy.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(preText || "");
        copy.textContent = "已复制";
        setTimeout(() => (copy.textContent = "复制"), 1200);
      } catch (error) {
        /* ignore */
      }
    });
    label.append(copy);
  }
  const pre = el("pre", "tool-pre" + (labelText === "结果" ? " result" : ""), preText || "");
  wrapper.append(label, pre);
  return wrapper;
}

/**
 * 工具卡（步骤内用，不含头像 / 外层 msg）
 * 返回 { card, statusEl, durationEl, resultEl }
 */
function buildToolCard(data) {
  const name = data.tool_name || "";
  const meta = toolMeta(name);
  const status = data.status || "running";

  const card = el("div", "tool-card step");
  card.dataset.state = status;

  const head = el("div", "tool-head");
  head.append(el("span", "step-ico", meta.icon));
  head.append(el("span", "tool-title", meta.label));
  head.append(el("code", "tool-name", name || "tool"));

  const statusEl = statusNode(status);
  head.append(statusEl);
  const durationEl = el("span", "tool-dur", "");
  head.append(durationEl);

  const toggle = el("button", "tool-toggle", "展开");
  toggle.type = "button";
  toggle.addEventListener("click", (event) => {
    event.stopPropagation();
    const expanded = card.classList.toggle("expanded");
    toggle.textContent = expanded ? "收起" : "展开";
  });
  head.append(toggle);
  card.append(head);

  const argsRow = el("div", "tool-args");
  const entries = normalizeArgs(data.args);
  if (entries.length === 0) {
    argsRow.append(el("span", "arg-chip", "无参数"));
  } else {
    for (const [key, value] of entries) argsRow.append(argChip(key, value));
  }
  card.append(argsRow);

  if (name === "get_city_weather" && data.result) {
    card.append(buildWeather(data.result));
  }

  const body = el("div", "tool-body");
  body.append(block("参数", prettyArgs(data.args || {}), false));
  body.append(block("结果", data.result || "（暂无输出）", true));
  card.append(body);

  if (data.duration_ms) {
    durationEl.textContent = `${(data.duration_ms / 1000).toFixed(1)}s`;
  }

  return {
    card,
    statusEl,
    durationEl,
    resultEl: card.querySelector(".tool-pre.result"),
  };
}

function applyToolResult(built, status, result, durationMs) {
  built.card.dataset.state = status === "running" ? "done" : status;
  const fresh = statusNode(status === "running" ? "done" : status);
  built.statusEl.replaceWith(fresh);
  built.statusEl = fresh;
  if (durationMs) {
    built.durationEl.textContent = `${(durationMs / 1000).toFixed(1)}s`;
  }
  if (built.resultEl) built.resultEl.textContent = result || "（无输出）";
}

function buildWeather(raw) {
  const text = String(raw).trim();
  const temp = text.match(/([+-]?\d+)\s*°C/);
  const humidity = text.match(/(\d+)\s*%/);
  const wind = text.match(/(\d+(?:-\d+)?)\s*km\/h/);
  const city = text.match(/^([^:：]{1,24})[:：]/);

  const card = el("div", "weather-card");
  card.append(el("div", "weather-temp", temp ? `${temp[1]}°C` : "—"));

  const meta = el("div", "weather-meta");
  meta.append(el("span", "weather-city", city ? city[1].trim() : "当前天气"));
  const parts = [];
  if (humidity) parts.push(`湿度 ${humidity[1]}%`);
  if (wind) parts.push(`风速 ${wind[1]} km/h`);
  meta.append(el("span", "", parts.length ? parts.join(" · ") : "实时数据"));
  card.append(meta);

  card.append(el("div", "weather-raw", text.split("\n")[0]));
  return card;
}

function addTool(data) {
  hideWelcome();
  clearTyping();
  const built = buildToolCard({
    tool_call_id: data.tool_call_id,
    tool_name: data.tool_name,
    args: data.args,
    status: "running",
    result: "",
  });
  addStepNode(built.card);
  state.tools.set(data.tool_call_id || data.tool_name, built);
  scrollBottom();
}

function updateTool(data) {
  const key = data.tool_call_id || "";
  const built = state.tools.get(key) || state.tools.get(data.tool_name || "");
  if (!built || !built.resultEl) return;
  built.resultEl.textContent = data.partial || "";
}

function finishTool(data) {
  const key = data.tool_call_id || "";
  const built = state.tools.get(key) || state.tools.get(data.tool_name || "");
  if (!built) return;
  state.tools.delete(key);
  state.tools.delete(data.tool_name || "");

  applyToolResult(
    built,
    data.is_error ? "error" : "done",
    data.result,
    data.duration_ms
  );

  if (data.is_error && state.run) {
    state.run.hasError = true;
    updateStepsSummary(state.run);
  }

  if (data.tool_name === "get_city_weather" && data.result) {
    const old = built.card.querySelector(".weather-card");
    if (old) old.remove();
    built.card.insertBefore(buildWeather(data.result), built.card.querySelector(".tool-body"));
  }
  scrollBottom();
}

/* ---------------------------------------------------------------- 历史渲染 */

function renderHistory(messages) {
  dom.messages.innerHTML = "";
  state.tools.clear();
  state.run = null;

  if (!messages || messages.length === 0) {
    dom.welcome.classList.remove("hidden");
    return;
  }
  dom.welcome.classList.add("hidden");

  let run = null;

  for (const message of messages) {
    switch (message.role) {
      case "user":
        run = null;
        appendUser(message.text, message.ts);
        break;

      case "assistant": {
        run = run || createRun();
        if (message.thinking && message.thinking.trim()) {
          const details = document.createElement("details");
          details.className = "step step-think";
          const summary = document.createElement("summary");
          summary.append(el("span", "step-ico", "✳"));
          summary.append(el("span", "step-title", "思考过程"));
          const body = el("div", "step-body", message.thinking.trim());
          details.append(summary, body);
          run.count += 1;
          run.stepsBody.append(details);
          updateStepsSummary(run);
        }
        if (message.text && message.text.trim()) {
          const bubble = el("div", "chat-text");
          bubble.innerHTML = renderMarkdown(message.text);
          run.answers.append(bubble);
          run.answers.append(buildMessageMeta(message.ts, message.usage));
        }
        break;
      }

      case "tool": {
        run = run || createRun();
        const built = buildToolCard({
          tool_call_id: message.tool_call_id,
          tool_name: message.tool_name,
          args: message.args,
          status: message.status === "running" ? "done" : message.status || "done",
          result: message.result,
          duration_ms: message.duration_ms,
        });
        applyToolResult(
          built,
          message.status === "running" ? "done" : message.status || "done",
          message.result,
          message.duration_ms
        );
        if (message.status === "error") run.hasError = true;
        run.count += 1;
        run.stepsBody.append(built.card);
        updateStepsSummary(run);
        break;
      }

      case "system":
        run = null;
        appendSystem(message.text);
        break;

      default:
        break;
    }
  }

  collapseSteps();
  markEmptyAnswers();
  scrollBottom(true);
}

/** 历史回显：只有思考、没有正文的轮次展开思考并给提示 */
function markEmptyAnswers() {
  for (const runEl of dom.messages.querySelectorAll(".msg.assistant.run")) {
    const steps = runEl.querySelector(".steps");
    const answers = runEl.querySelector(".answers");
    if (!steps || !answers) continue;
    if (steps.classList.contains("hidden")) continue;
    if (answers.querySelector(".chat-text")) continue;
    steps.open = true;
    answers.append(
      el(
        "div",
        "empty-hint",
        "该轮模型只返回了思考内容，没有生成回答。"
      )
    );
  }
}

function appendUser(text, ts) {
  hideWelcome();
  const message = el("div", "msg user");
  message.append(el("div", "avatar", "我"));
  const body = el("div", "body");
  const bubble = el("div", "chat-text", text || "");
  body.append(bubble);
  if (ts) body.append(buildMessageMeta(ts, null));
  message.append(body);
  dom.messages.append(message);
  scrollBottom();
}

function appendSystem(text) {
  dom.messages.append(el("div", "system-line", text || ""));
  scrollBottom();
}

function appendError(message) {
  if (!message) return;
  hideWelcome();
  const group = el("div", "msg assistant");
  group.append(el("div", "avatar", "!"));
  const body = el("div", "body");
  body.append(el("div", "error-card", message));
  group.append(body);
  dom.messages.append(group);
  scrollBottom();
}

/* ---------------------------------------------------------------- 发送 */

async function send() {
  const text = dom.input.value.trim();
  if (!text || state.sending) return;

  if (!state.sid) {
    await createSession();
    if (!state.sid) return;
  }

  if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
    setConn(false, "连接未就绪，请稍候");
    return;
  }

  dom.input.value = "";
  autoResize();
  hideWelcome();
  setBusy(true);
  state.stick = true;

  // 不在这里插 typing 占位：服务端会先广播 user 消息，再广播 busy。
  // 提前插占位会让“思考中”气泡出现在用户消息上方。
  state.ws.send(JSON.stringify({ type: "user_message", text }));
}

function abortRun() {
  if (state.ws && state.ws.readyState === WebSocket.OPEN) {
    state.ws.send(JSON.stringify({ type: "abort" }));
    setBusy(false, "正在停止…");
  }
}

function setBusy(busy, hint) {
  state.sending = busy;
  dom.input.disabled = busy;
  dom.sendBtn.disabled = busy;
  dom.busyBadge.classList.toggle("hidden", !busy);
  dom.stopBtn.classList.toggle("hidden", !busy);
  dom.modelSelect.disabled = busy;
  if (busy) {
    state.stick = true;
  } else {
    dom.input.focus();
  }
  if (hint) dom.connText.textContent = hint;
}

/* ---------------------------------------------------------------- 工具函数 */

function scrollBottom(force = false) {
  if (force || state.stick) {
    dom.scroll.scrollTop = dom.scroll.scrollHeight;
  }
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[ch]));
}

/** 轻量 Markdown：先转义再处理，避免 XSS。 */
function renderMarkdown(text) {
  let html = escapeHtml(text);
  html = html.replace(/```([\s\S]*?)```/g, (_match, code) =>
    `<pre class="code-block">${String(code).replace(/^\n/, "")}</pre>`
  );
  html = html.replace(/`([^`\n]+)`/g, "<code>$1</code>");
  html = html.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/^###\s+(.+)$/gm, "<strong>$1</strong>");
  return html;
}

function autoResize() {
  dom.input.style.height = "auto";
  dom.input.style.height = `${Math.min(dom.input.scrollHeight, 200)}px`;
}

/* ---------------------------------------------------------------- 视图切换 */

const STATUS_TEXT = {
  0: "连接失败",
  400: "请求格式错误",
  401: "鉴权失败（KEY 无效）",
  403: "无权限",
  404: "接口不存在",
  405: "方法不允许",
  413: "请求过大",
  429: "触发限流",
  500: "服务端错误",
  502: "网关错误",
  503: "服务不可用",
  504: "网关超时",
};

const AUTH_LABEL = {
  bearer: "Bearer",
  raw: "裸 KEY",
  "api-key": "api-key",
  none: "无鉴权",
};

function setView(view) {
  const isModels = view === "models";
  state.view = view;
  dom.main.classList.toggle("view-models", isModels);
  dom.navChat.classList.toggle("active", !isModels);
  dom.navModels.classList.toggle("active", isModels);

  if (isModels) {
    dom.title.textContent = "模型";
    dom.subtitle.textContent = "连通性测试 · 逐个验证";
    dom.clearBtn.classList.add("hidden");
    dom.stopBtn.classList.add("hidden");
    dom.busyBadge.classList.add("hidden");
    renderModelRows();
    return;
  }

  // 回到对话：恢复标题、操作按钮与输入态
  const current = state.sessions.find((item) => item.id === state.sid);
  dom.title.textContent = current?.title || "新对话";
  dom.subtitle.textContent = `${state.meta?.provider || "Pi"} · 流式输出 · 工具调用`;
  dom.clearBtn.classList.toggle("hidden", !state.sid);
  setBusy(state.sending);
}

function renderModelRows() {
  const models = state.models || [];
  dom.modelsSub.textContent = `共 ${models.length} 个模型 · 点击右侧按钮测试连通性`;
  dom.modelList.innerHTML = "";

  if (models.length === 0) {
    dom.modelList.append(
      el("div", "rail-empty", "没有加载到模型\n请在 .env 中配置 PI_LLM_* 或 PI_MODELS")
    );
    return;
  }

  for (const model of models) {
    const item = el("div", "model-item");

    const row = el("div", "model-row");
    row.dataset.key = model.key;

    const info = el("div", "model-info");
    const name = el("div", "model-name");
    name.append(el("span", "", model.label || model.model || model.key));
    if (model.key === state.modelKey) name.append(el("span", "model-tag", "当前"));
    info.append(name);

    const metaParts = [model.model || model.key, model.provider || "-"];
    if (model.base_url) metaParts.push(model.base_url);
    if (!model.has_key) metaParts.push("无 KEY");
    info.append(el("div", "model-meta", metaParts.join("  ·  ")));

    const result = el("div", "model-result");

    const actions = el("div", "model-actions");
    const remote = el("div", "model-remote hidden");

    const listBtn = el("button", "btn model-remote-btn", "获取模型");
    listBtn.type = "button";
    listBtn.title = "GET {BASE_URL}/models";
    listBtn.addEventListener("click", () =>
      fetchRemoteModels(model.key, listBtn, remote)
    );

    const testBtn = el("button", "btn model-test-btn", "测试连接");
    testBtn.type = "button";
    testBtn.addEventListener("click", () => testModel(model.key, testBtn, result, row));

    actions.append(listBtn, testBtn);
    row.append(info, result, actions);
    item.append(row, remote);
    dom.modelList.append(item);
  }
}

async function fetchRemoteModels(key, button, remoteEl) {
  if (remoteEl.dataset.loading === "1") return;

  // 已获取过：直接展开/收起
  if (remoteEl.dataset.loaded === "1") {
    remoteEl.classList.toggle("hidden");
    button.textContent = remoteEl.classList.contains("hidden") ? "获取模型" : "收起";
    return;
  }

  remoteEl.dataset.loading = "1";
  button.disabled = true;
  button.textContent = "获取中…";
  remoteEl.classList.remove("hidden");
  remoteEl.innerHTML = "";

  const pending = el("div", "remote-status");
  pending.append(el("span", "spin"), document.createTextNode("正在请求 /models…"));
  remoteEl.append(pending);

  try {
    const data = await apiSend(`/api/models/${encodeURIComponent(key)}/remote`, {
      method: "POST",
    });
    renderRemoteModels(remoteEl, data);
  } catch (error) {
    renderRemoteModels(remoteEl, {
      ok: false,
      status: 0,
      detail: String(error.message || error),
      models: [],
    });
  } finally {
    remoteEl.dataset.loading = "0";
    remoteEl.dataset.loaded = "1";
    button.disabled = false;
    button.textContent = "收起";
  }
}

function renderRemoteModels(container, data) {
  container.innerHTML = "";

  const head = el("div", "remote-head");
  if (data.ok) {
    const badge = el("span", "badge green");
    badge.textContent = `✓ ${data.count} 个模型`;
    if (data.latency_ms) badge.textContent += ` · ${data.latency_ms}ms`;
    head.append(badge);
    head.append(
      el("span", "remote-auth", `鉴权：${AUTH_LABEL[data.auth] || data.auth || "-"}`)
    );
  } else {
    const badge = el("span", "badge red");
    const code = data.status || "ERR";
    badge.textContent = `${code} · ${STATUS_TEXT[data.status] || "获取失败"}`;
    head.append(badge);
    if (data.detail) head.append(el("span", "remote-detail", data.detail));
  }
  container.append(head);

  if (!data.ok || !data.models?.length) return;

  const grid = el("div", "remote-grid");
  for (const model of data.models) {
    const chip = el("div", "remote-chip");
    chip.append(el("code", "remote-id", model.id));
    if (model.owned_by) chip.append(el("span", "remote-owner", model.owned_by));
    chip.title = model.id;
    grid.append(chip);
  }
  container.append(grid);
}

async function testModel(key, button, resultEl, row) {
  if (state.testing.has(key)) return;
  state.testing.add(key);
  button.disabled = true;
  button.textContent = "测试中…";
  resultEl.innerHTML = "";
  row.dataset.state = "";

  const pending = el("span", "badge gray");
  pending.append(el("span", "spin"), document.createTextNode("连接中"));
  resultEl.append(pending);

  try {
    const data = await apiSend(`/api/models/${encodeURIComponent(key)}/test`, {
      method: "POST",
    });
    renderTestResult(resultEl, row, data);
  } catch (error) {
    renderTestResult(resultEl, row, {
      ok: false,
      status: 0,
      detail: String(error.message || error),
    });
  } finally {
    state.testing.delete(key);
    button.disabled = false;
    button.textContent = "重新测试";
  }
}

function renderTestResult(resultEl, row, data) {
  resultEl.innerHTML = "";
  row.dataset.state = data.ok ? "ok" : "fail";

  const badge = el("span", "badge " + (data.ok ? "green" : "red"));
  if (data.ok) {
    const parts = ["✓ 可用", `HTTP ${data.status ?? 200}`];
    if (data.latency_ms) parts.push(`${data.latency_ms}ms`);
    if (data.auth) parts.push(AUTH_LABEL[data.auth] || data.auth);
    badge.textContent = parts.join(" · ");
  } else {
    const code = data.status || "ERR";
    badge.textContent = `${code} · ${STATUS_TEXT[data.status] || "请求失败"}`;
  }
  resultEl.append(badge);

  const detailText = data.detail || "";
  if (detailText) {
    const detail = el(
      "div",
      "model-result-detail" + (data.ok ? "" : " err"),
      detailText
    );
    detail.title = detailText;
    resultEl.append(detail);
  }
}

async function testAllModels() {
  const rows = [...dom.modelList.querySelectorAll(".model-row")];
  if (rows.length === 0) return;
  dom.testAllBtn.disabled = true;
  try {
    // 串行探测，避免同时打满上游限流
    for (const row of rows) {
      const button = row.querySelector(".model-test-btn");
      const result = row.querySelector(".model-result");
      if (button && result) await testModel(row.dataset.key, button, result, row);
    }
  } finally {
    dom.testAllBtn.disabled = false;
  }
}