/* ==========================================================================
 * Pi Agent · 前端交互
 * 依赖后端协议：web/events.py
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
  group: null,
  groupBody: null,
  textEl: null,
  textBuffer: "",
  thinkEl: null,
  thinkStart: 0,
  typingEl: null,
  tools: new Map(),
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
  dom.modelChip = $("modelChip");
  dom.toolChip = $("toolChip");

  renderQuickCards();
  bindEvents();
  loadMeta();
  refreshSessions().then(() => {
    if (state.sessions.length > 0) {
      openSession(state.sessions[0].id);
    } else {
      resetView();
    }
  });
});

function bindEvents() {
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
}

async function loadMeta() {
  try {
    const meta = await apiGet("/api/meta");
    const modelName = String(meta.model || "").split("/").pop();
    dom.modelChip.textContent = modelName || "默认模型";
    dom.toolChip.textContent = `${(meta.tools || []).length} 个工具`;
    dom.subtitle.textContent = `${meta.provider || "Pi"} · 流式输出 · 工具调用`;
  } catch (error) {
    console.warn("加载元信息失败", error);
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
  state.refreshTimer = setTimeout(refreshSessions, 500);
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
    body.append(el("span", "session-time", relativeTime(item.updated_at)));

    const del = el("button", "session-del", "✕");
    del.type = "button";
    del.title = "删除该对话";
    del.addEventListener("click", (event) => {
      event.stopPropagation();
      deleteSession(item.id);
    });

    row.append(body, del);
    row.addEventListener("click", () => openSession(item.id));
    dom.sessionList.append(row);
  }
}

function relativeTime(seconds) {
  if (!seconds) return "";
  const diff = Date.now() - seconds * 1000;
  if (diff < 60_000) return "刚刚";
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前`;
  return new Date(seconds * 1000).toLocaleDateString("zh-CN");
}

function setActiveSession() {
  for (const row of dom.sessionList.children) {
    row.classList.toggle("active", row.dataset.id === state.sid);
  }
}

/* ---------------------------------------------------------------- 会话切换 */

async function createSession() {
  try {
    const data = await apiSend("/api/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: "新对话" }),
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
  clearTyping();
  resetGroup();

  let data;
  try {
    data = await apiGet(`/api/sessions/${sid}/messages`);
  } catch (error) {
    if (sequence === state.openSeq) appendError(`加载对话失败：${error.message}`);
    return;
  }
  if (sequence !== state.openSeq) return;

  setTitle(data.title || "对话");
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
  resetGroup();
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
      appendUser(data.text);
      if (data.title) setTitle(data.title);
      scheduleRefresh();
      break;

    case "busy":
      setBusy(Boolean(data.value));
      if (data.value) showTyping();
      else clearTyping();
      break;

    case "message_start":
      if (data.role === "assistant") resetGroup();
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
        resetGroup();
      }
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
      resetGroup();
      setBusy(false);
      break;

    case "done":
      clearTyping();
      setBusy(false);
      scheduleRefresh();
      break;

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

/* ---------------------------------------------------------------- 流式渲染 */

function hideWelcome() {
  dom.welcome.classList.add("hidden");
}

function ensureGroup() {
  if (state.group) return state.group;

  const group = el("div", "msg assistant");
  group.append(el("div", "avatar", "✦"));
  const body = el("div", "body");
  group.append(body);
  dom.messages.append(group);

  state.group = group;
  state.groupBody = body;
  state.textEl = null;
  return group;
}

function resetGroup() {
  if (state.typingEl) {
    state.typingEl.remove();
    state.typingEl = null;
  }
  // 只含头像/占位的空分组直接移除，避免留下空白气泡
  if (state.group && state.groupBody && state.groupBody.childNodes.length === 0) {
    state.group.remove();
  }
  state.group = null;
  state.groupBody = null;
  state.textEl = null;
  state.textBuffer = "";
  state.thinkEl = null;
}

function showTyping() {
  ensureGroup();
  if (state.textEl || state.typingEl) return;
  const typing = el("div", "typing");
  typing.append(el("i"), el("i"), el("i"));
  state.groupBody.append(typing);
  state.typingEl = typing;
}

function clearTyping() {
  if (state.typingEl) {
    state.typingEl.remove();
    state.typingEl = null;
  }
}

function appendDelta(delta) {
  if (!delta) return;
  ensureGroup();
  if (!state.textEl) {
    state.textEl = el("div", "chat-text");
    state.groupBody.append(state.textEl);
  }
  state.textBuffer += delta;
  state.textEl.innerHTML = renderMarkdown(state.textBuffer);
  scrollBottom();
}

function startThinking() {
  ensureGroup();
  if (state.thinkEl) return;
  const details = document.createElement("details");
  details.className = "think";
  details.open = true;

  const summary = document.createElement("summary");
  const spinner = el("span", "spin");
  summary.append(spinner, document.createTextNode(" 思考中…"));

  const body = el("div", "think-body");
  details.append(summary, body);
  state.groupBody.append(details);

  state.thinkEl = details;
  state.thinkStart = Date.now();
  scrollBottom();
}

function appendThinking(delta) {
  if (!delta) return;
  if (!state.thinkEl) startThinking();
  const body = state.thinkEl.querySelector(".think-body");
  if (body) {
    body.textContent += delta;
    scrollBottom();
  }
}

function endThinking() {
  if (!state.thinkEl) return;
  const seconds = ((Date.now() - state.thinkStart) / 1000).toFixed(1);
  const summary = state.thinkEl.querySelector("summary");
  if (summary) {
    summary.textContent = `已思考 ${seconds}s`;
  }
  state.thinkEl.open = false;
  state.thinkEl = null;
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

function buildToolCard(data) {
  const name = data.tool_name || "";
  const meta = toolMeta(name);

  const message = el("div", "msg tool");
  message.dataset.callId = data.tool_call_id || "";
  message.append(el("div", "avatar", meta.icon));

  const wrapper = el("div", "body");
  const card = el("div", "tool-card");
  card.dataset.state = data.status || "running";

  const head = el("div", "tool-head");
  head.append(el("span", "tool-title", meta.label));
  head.append(el("code", "tool-name", name || "tool"));

  const status = statusNode(data.status || "running");
  head.append(status);
  const duration = el("span", "tool-dur", "");
  head.append(duration);

  const toggle = el("button", "tool-toggle", "展开");
  toggle.type = "button";
  toggle.addEventListener("click", () => {
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
    duration.textContent = `${(data.duration_ms / 1000).toFixed(1)}s`;
  }

  wrapper.append(card);
  message.append(wrapper);

  return { message, card, status, duration };
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
  const built = buildToolCard({
    tool_call_id: data.tool_call_id,
    tool_name: data.tool_name,
    args: data.args,
    status: "running",
    result: "",
  });
  dom.messages.append(built.message);
  state.tools.set(data.tool_call_id || data.tool_name, built);
  scrollBottom();
}

function updateTool(data) {
  const key = data.tool_call_id || "";
  const built = state.tools.get(key) || state.tools.get(data.tool_name || "");
  if (!built) return;
  const pre = built.card.querySelector(".tool-pre.result");
  if (pre) pre.textContent = data.partial || "";
}

function finishTool(data) {
  const key = data.tool_call_id || "";
  const built = state.tools.get(key) || state.tools.get(data.tool_name || "");
  if (!built) return;
  state.tools.delete(key);
  state.tools.delete(data.tool_name || "");

  built.card.dataset.state = data.is_error ? "error" : "done";
  built.status.replaceWith(statusNode(data.is_error ? "error" : "done"));
  built.status = built.card.querySelector(".tool-status");

  if (data.duration_ms) {
    built.duration.textContent = `${(data.duration_ms / 1000).toFixed(1)}s`;
  }

  const pre = built.card.querySelector(".tool-pre.result");
  if (pre) pre.textContent = data.result || "（无输出）";

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
  resetGroup();

  if (!messages || messages.length === 0) {
    dom.welcome.classList.remove("hidden");
    return;
  }
  dom.welcome.classList.add("hidden");

  for (const message of messages) {
    switch (message.role) {
      case "user":
        appendUser(message.text, message.ts);
        break;

      case "assistant":
        appendAssistantHistory(message);
        break;

      case "tool":
        {
          const built = buildToolCard({
            tool_call_id: message.tool_call_id,
            tool_name: message.tool_name,
            args: message.args,
            status: message.status === "running" ? "done" : message.status || "done",
            result: message.result,
            duration_ms: message.duration_ms,
          });
          if (message.status === "running") {
            built.card.dataset.state = "done";
            built.status.replaceWith(statusNode("done"));
          }
          dom.messages.append(built.message);
        }
        break;

      case "system":
        appendSystem(message.text);
        break;

      default:
        break;
    }
  }
  scrollBottom(true);
}

function appendUser(text, ts) {
  hideWelcome();
  const message = el("div", "msg user");
  message.append(el("div", "avatar", "我"));
  const body = el("div", "body");
  const bubble = el("div", "chat-text", text || "");
  if (ts) bubble.title = new Date(ts).toLocaleString("zh-CN");
  body.append(bubble);
  message.append(body);
  dom.messages.append(message);
  scrollBottom();
}

function appendAssistantHistory(message) {
  const group = el("div", "msg assistant");
  group.append(el("div", "avatar", "✦"));
  const body = el("div", "body");

  if (message.thinking && message.thinking.trim()) {
    const details = document.createElement("details");
    details.className = "think";
    const summary = el("summary", "", "思考过程");
    const thinkBody = el("div", "think-body", message.thinking.trim());
    details.append(summary, thinkBody);
    body.append(details);
  }

  if (message.text && message.text.trim()) {
    const bubble = el("div", "chat-text");
    bubble.innerHTML = renderMarkdown(message.text);
    body.append(bubble);
  }

  if (body.childNodes.length > 0) {
    group.append(body);
    dom.messages.append(group);
  }
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
