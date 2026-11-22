"use strict";

// All text from the server is inserted with textContent. Only the report HTML (escaped by the backend
// before markdown conversion) goes through innerHTML.

const $ = (sel) => document.querySelector(sel);

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key === "onclick") node.addEventListener("click", value);
    else if (value === true) node.setAttribute(key, "");
    else if (value !== false && value != null) node.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child == null || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

const G = {
  id: null,
  state: null,
  world: null,
  health: null,
  briefing: null,
  summary: null, // {text} shown once between days
  transcript: [],
  endedLog: null,
  notice: "",
  cutscene: null,
  report: null,
  reportError: null,
  busy: false,
  pending: null, // "say" | "callback" while waiting for the caller
  menu: null, // "callback" | "transfer"
  confirmTransfer: null, // employee id awaiting a second click
  draft: "",
  lang: "en",
  worlds: {},
};

// ------------------------------------------------------------------ language

const I18N = {
  en: {
    logo: "FRONT DESK", your_name: "Your name", difficulty: "Difficulty", easy: "Easy", normal: "Normal", hard: "Hard",
    replies_title: "Replies you can still send today", replies_left: "Replies left", security: "Security", service: "Service",
    scores_hidden: "Hard: your scores stay hidden until the end of the week",
    tab_directory: "Directory", tab_desk: "Desk", tab_policy: "Policy", tab_notes: "My notes",
    notes_placeholder: "Write your own notes here…", notes_hint: "Kept only in this page's memory — refreshing the page clears it.",
    net_error: "Could not reach the server.", bad_request: "That request was not valid.", failed: "Request failed ({status}).",
    preparing: "Preparing today's calls… (this can take a few seconds)", start_shift: "Start your shift",
    start_day: "Start the day", end_of_day: "End of day",
    tally: "Tonight's tally: Security {sec} ({secd}), Service {svc} ({svcd})", lost_to: ". Service lost to: {list}", sec_moved: ". Security changed by: {list}", end_dot: ".",
    unhappy: ". {n} caller(s) left unhappy today", list_sep: "; ", next_day: "Next day",
    answer: "📞 Answer next call", calls_left: "Calls left today: {n}", no_more: "No more calls today.", end_day: "End the day",
    flag: "🚩 Flag last call as suspicious", last_call: "Last call", caller_id: "Caller ID: {id}", impatient: "😤 sounds impatient",
    ph_none: "No replies left today. Transfer, take a message or hang up.",
    ph_first: "The caller has already said why they called. Don't spend a reply on a greeting. (Enter to send)",
    ph_more: "Say something… (Enter to send, Shift+Enter for a new line)", send: "Send",
    budget: "Replies left: {call} on this call · {day} today", hold: "⏸ Hold & call back… (1 reply · {n} left today)",
    transfer: "↪ Transfer…", message: "📝 Take a message", hangup: "✖ Hang up",
    hold_warning: "Checking someone keeps the caller waiting, and real callers do not enjoy it.",
    the_caller: "The caller", caller_number: "Number the caller gave", employees: "Employees", vendors: "Vendors",
    no_undo: "This cannot be undone", confirm_transfer: "Transfer this caller to {name}?", them: "them",
    yes_transfer: "Yes, transfer", back: "Back",
    click_name: "Click a name for numbers and notes.", f_ext: "Ext", f_mobile: "Mobile", f_empid: "Employee ID",
    f_status: "Status", f_note: "Note", f_phone: "Official phone", f_contact: "Contact", company: "Company",
    main_line: "Main line {n}", sticky: "Sticky notes", internal: "Internal notes", bulletin: "Bulletin",
    day_n: "Day {n}", policy_update: "Policy update", heads_up: "Heads up", standing_policy: "Standing policy",
    playbook: "Playbook: how con artists work", sounds_like: "Sounds like", what_to_do: "What to do",
    end_caller: "— The caller hung up. —", end_patience: "— The caller lost patience and hung up. —",
    end_max: "— The caller has nothing more to say and hung up. —", end_exposed: "— The caller hung up on you. —",
    end_other: "— The call has ended. —", you_hung_up: "You hung up.",
    try_again: "Try again", writing_report: "Writing the post-game report…",
    really: "What was really going on", their_goal: "Their goal: ", the_crew: "The crew: ", why_now: "Why now: ",
    their_method: "Their method, “{name}”: ", their_plan: "Their plan: ", plan_day: "Day {n}: {p}",
    how_spot: "How to spot it: ", switched: "{day}, after being spotted, they switched to “{name}”: ",
    incident_label: "The {title} ({day}): ", every_call: "Every call, revealed", notebook: "The con artist's notebook",
    learned_title: "What they learned", learned_none: "Nothing at all. The con artist learned nothing from you.",
    th_day: "Day", th_time: "Time", th_info: "Information", th_words: "Your words", th_source: "Source",
    stats: "Stats", s_calls: "Calls taken", s_callbacks: "Callbacks made", s_exposures: "Callers exposed",
    s_correct: "Correct flags", s_wrong: "Wrong flags", s_overshare: "Overshares to real callers",
    s_leaked: "Facts the con learned", download: "Download game log (JSON)", play_again: "Play again",
    real: "REAL", con: "CON", day_time: "Day {n} · {time}", pretending: "Pretending to be: ", move: "Move: ", special: "Special move: ",
    counter: "Counter: ", purpose: "What it was for: ", slip: "Their slip: ", script: "Their script: ",
    ended: "How it ended: ", you: "You: ", careful: "Careful: ",
    accomplice: "the number they gave you was their accomplice. Whoever answers there will vouch for a con.",
    they_learned: "They learned: ", src_llm: "LLM", src_pattern: "pattern", src_both: "LLM + pattern",
    src_recon: "crew's own research", report_failed: "Could not load the report: {msg}",
    no_directory: "Could not load the directory.", llm_banner: "LLM is not configured — see README",
  },
  zh: {
    logo: "前台", your_name: "你的名字", difficulty: "难度", easy: "简单", normal: "普通", hard: "困难",
    replies_title: "今天还能发送的回复数", replies_left: "剩余回复", security: "安全", service: "服务",
    scores_hidden: "困难模式：分数要到一周结束才揭晓",
    tab_directory: "名录", tab_desk: "桌面", tab_policy: "规定", tab_notes: "我的笔记",
    notes_placeholder: "在这里写你自己的笔记…", notes_hint: "只保存在本页面内存里，刷新页面就会清空。",
    net_error: "连不上服务器。", bad_request: "请求无效。", failed: "请求失败（{status}）。",
    preparing: "正在准备今天的来电…（可能要几秒钟）", start_shift: "开始上班",
    start_day: "开始今天的工作", end_of_day: "今天结束",
    tally: "今晚结算：安全 {sec}（{secd}），服务 {svc}（{svcd}）", lost_to: "。服务分扣在：{list}", sec_moved: "。安全分变动：{list}", end_dot: "。",
    unhappy: "。今天有 {n} 位来电者不满意", list_sep: "；", next_day: "下一天",
    answer: "📞 接听下一通", calls_left: "今天还有 {n} 通来电", no_more: "今天没有来电了。", end_day: "结束今天",
    flag: "🚩 把上一通标记为可疑", last_call: "上一通电话", caller_id: "来电号码：{id}", impatient: "😤 听起来不耐烦",
    ph_none: "今天的回复次数用完了。请转接、留言或挂断。",
    ph_first: "来电者已经说了来意，别把回复浪费在寒暄上。（回车发送）",
    ph_more: "说点什么…（回车发送，Shift+回车换行）", send: "发送",
    budget: "剩余回复：本通 {call} 次 · 今天 {day} 次", hold: "⏸ 挂起并回拨核实…（1 次回复 · 今天还剩 {n} 次）",
    transfer: "↪ 转接…", message: "📝 记留言", hangup: "✖ 挂断",
    hold_warning: "核实会让来电者一直等着，真人来电者不会喜欢。",
    the_caller: "来电者", caller_number: "来电者给的号码", employees: "员工", vendors: "供应商",
    no_undo: "此操作无法撤销", confirm_transfer: "把这通来电转给 {name}？", them: "对方",
    yes_transfer: "确定转接", back: "返回",
    click_name: "点击名字查看号码和备注。", f_ext: "分机", f_mobile: "手机", f_empid: "工号",
    f_status: "状态", f_note: "备注", f_phone: "官方电话", f_contact: "联系人", company: "公司",
    main_line: "总机 {n}", sticky: "便利贴", internal: "内部备忘", bulletin: "公告栏",
    day_n: "第 {n} 天", policy_update: "规定更新", heads_up: "注意", standing_policy: "常规守则",
    playbook: "骗术手册：骗子惯用的套路", sounds_like: "听起来像", what_to_do: "怎么应对",
    end_caller: "— 来电者挂断了。—", end_patience: "— 来电者没了耐心，挂断了。—",
    end_max: "— 来电者没什么可说的了，挂断了。—", end_exposed: "— 对方挂了你的电话。—",
    end_other: "— 通话结束。—", you_hung_up: "你挂断了电话。",
    try_again: "重试", writing_report: "正在撰写复盘报告…",
    really: "真相", their_goal: "他们的目标：", the_crew: "团伙：", why_now: "为什么是现在：",
    their_method: "他们的手法“{name}”：", their_plan: "他们的计划：", plan_day: "第 {n} 天：{p}",
    how_spot: "如何识破：", switched: "{day}，被识破后他们改用“{name}”：",
    incident_label: "{title}（{day}）：", every_call: "每通来电的真相", notebook: "骗子的笔记本",
    learned_title: "他们套到了什么", learned_none: "什么也没有。骗子没从你这儿得到任何东西。",
    th_day: "天", th_time: "时间", th_info: "信息", th_words: "你的原话", th_source: "来源",
    stats: "统计", s_calls: "接听来电", s_callbacks: "回拨核实", s_exposures: "戳穿骗子",
    s_correct: "正确标记", s_wrong: "错误标记", s_overshare: "对真人说多了",
    s_leaked: "骗子套到的信息", download: "下载游戏记录（JSON）", play_again: "再玩一局",
    real: "真人", con: "骗子", day_time: "第 {n} 天 · {time}", pretending: "冒充：", move: "套路：", special: "特别招数：",
    counter: "对策：", purpose: "目的：", slip: "露出的破绽：", script: "他们的台本：",
    ended: "结果：", you: "你：", careful: "注意：",
    accomplice: "他们给你的号码是同伙接的。打那个号码，接电话的人会替骗子担保。",
    they_learned: "他们套到了：", src_llm: "LLM", src_pattern: "关键词", src_both: "LLM + 关键词",
    src_recon: "团伙自己的调查", report_failed: "报告加载失败：{msg}",
    no_directory: "名录加载失败。", llm_banner: "尚未配置 LLM，请参阅 README",
  },
};

const LANG_KEY = "frontdesk.lang";
function savedLang() {
  try { const v = localStorage.getItem(LANG_KEY); if (v === "en" || v === "zh") return v; } catch (e) { /* ignore */ }
  return (navigator.language || "").toLowerCase().startsWith("zh") ? "zh" : "en";
}
function saveLang(lang) { try { localStorage.setItem(LANG_KEY, lang); } catch (e) { /* ignore */ } }
function lang() { return (G.state && G.state.lang) || G.lang; }
function t(key, vars = {}) {
  const table = I18N[lang()] || I18N.en;
  const text = table[key] != null ? table[key] : I18N.en[key];
  return text.replace(/\{(\w+)\}/g, (m, k) => (vars[k] != null ? vars[k] : m));
}

function applyStatic() {
  document.documentElement.lang = lang() === "zh" ? "zh-CN" : "en";
  for (const node of document.querySelectorAll("[data-i18n]")) node.textContent = t(node.dataset.i18n);
  for (const node of document.querySelectorAll("[data-i18n-title]")) node.title = t(node.dataset.i18nTitle);
  for (const node of document.querySelectorAll("[data-i18n-placeholder]")) node.placeholder = t(node.dataset.i18nPlaceholder);
  for (const radio of document.querySelectorAll("input[name=lang]")) radio.checked = radio.value === lang();
}

async function loadWorld() {
  const wanted = lang();
  G.worlds = G.worlds || {};
  if (!G.worlds[wanted]) G.worlds[wanted] = await api("GET", `/api/world?lang=${wanted}`);
  G.world = G.worlds[wanted];
}

const STORAGE_KEY = "frontdesk.game_id";
function remember(id) {
  try { id ? localStorage.setItem(STORAGE_KEY, id) : localStorage.removeItem(STORAGE_KEY); } catch (e) { /* ignore */ }
}
function remembered() {
  try { return localStorage.getItem(STORAGE_KEY); } catch (e) { return null; }
}

// ------------------------------------------------------------------ api

async function api(method, path, body) {
  const options = { method, headers: {} };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(path, options);
  } catch (err) {
    throw new Error(t("net_error"));
  }
  let data = null;
  try { data = await response.json(); } catch (err) { /* not JSON */ }
  if (!response.ok) {
    let detail = data && data.detail;
    if (Array.isArray(detail)) detail = t("bad_request");
    const error = new Error(detail || t("failed", { status: response.status }));
    error.status = response.status;
    throw error;
  }
  return data;
}

const gameApi = (method, action, body) => api(method, `/api/game/${G.id}/${action}`, body);

function toast(message) {
  const node = $("#toast");
  node.textContent = message;
  node.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { node.hidden = true; }, 5000);
}

async function run(task) {
  if (G.busy) return;
  G.busy = true;
  render();
  try {
    await task();
  } catch (err) {
    toast(err.message);
    if (G.id && err.status === 409) await refresh();
  } finally {
    G.busy = false;
    G.pending = null;
    render();
  }
}

async function refresh() {
  try {
    const data = await api("GET", `/api/game/${G.id}`);
    syncFromState(data.state);
    if (data.briefing) G.briefing = data.briefing;
  } catch (err) { /* keep what we have */ }
}

function syncFromState(state) {
  const previous = G.state;
  G.state = state;
  if (state.active_call && (!previous || !previous.active_call)) G.transcript = state.active_call.transcript.slice();
  if (!state.active_call && previous && previous.active_call && !G.endedLog) G.endedLog = G.transcript.slice();
}

// ------------------------------------------------------------------ rendering

function showOnly(id) {
  for (const name of ["start-screen", "game-screen", "end-screen"]) $("#" + name).hidden = name !== id;
}

function render() {
  applyStatic();
  const keepDraft = $("#composer-input");
  if (keepDraft) G.draft = keepDraft.value;
  if (!G.state) return renderStart();
  if (G.state.phase === "GAME_OVER") return renderEnd();
  showOnly("game-screen");
  renderTopbar();
  renderLeft();
  renderTabs();
}

function renderStart() {
  showOnly("start-screen");
  const ui = G.world && G.world.ui && G.world.ui.start_page;
  if (ui) {
    $("#start-title").textContent = ui.title;
    $("#start-lines").replaceChildren(...ui.lines.map((line) => el("p", {}, line)));
    $("#start-footer").replaceChildren(...ui.footer.flatMap((line, i) => (i ? [el("br"), line] : [line])));
  }
  $("#start-button").disabled = G.busy;
  $("#start-button").textContent = G.busy ? t("preparing") : ((ui && ui.start_button) || t("start_shift"));
}

function renderTopbar() {
  const s = G.state;
  $("#top-clock").textContent = `${s.day_name} · ${s.clock}`;
  $("#top-replies b").textContent = s.replies_left_today;
  $("#top-replies").classList.toggle("low", s.replies_left_today <= 3);
  $("#top-security b").textContent = s.scores_hidden ? "?" : s.security;
  $("#top-service b").textContent = s.scores_hidden ? "?" : s.service;
  $("#top-security").title = s.scores_hidden ? t("scores_hidden") : t("security");
  $("#top-service").title = s.scores_hidden ? t("scores_hidden") : t("service");
  $("#top-player").textContent = s.player_name;
}

function renderLeft() {
  const s = G.state;
  const left = $("#left");
  let view;
  if (s.phase === "DAY_BRIEFING") view = G.summary ? summaryView() : briefingView();
  else if (s.phase === "CALL_ACTIVE") view = callView();
  else view = idleView();
  left.replaceChildren(view);
  const box = $(".transcript");
  if (box) box.scrollTop = box.scrollHeight;
}

function briefingView() {
  const b = G.briefing || { sender: "", text: "" };
  return el("div", { class: "briefing" },
    el("h2", {}, G.state.day_name),
    el("div", { class: "from" }, b.sender),
    el("div", { class: "body" }, b.text),
    ...(b.notices || []).map(noticeBox),
    el("div", { class: "row" },
      el("button", { class: "primary big", disabled: G.busy, onclick: () => run(startDay) }, t("start_day"))));
}

function noticeBox(n) {
  return el("div", { class: `bulletin-item ${n.kind}` },
    el("div", { class: "bulletin-head" }, n.header),
    el("b", {}, n.title),
    el("div", {}, n.text));
}

function fmtDelta(n) { return n > 0 ? `+${n}` : `${n}`; }

function summaryView() {
  return el("div", { class: "briefing" },
    el("h2", {}, t("end_of_day")),
    el("div", { class: "body" }, G.summary.text),
    G.summary.scores ? el("div", { class: "body" },
      t("tally", { sec: G.summary.scores.security, secd: fmtDelta(G.summary.scores.security_delta),
                   svc: G.summary.scores.service, svcd: fmtDelta(G.summary.scores.service_delta) }) +
      ((G.summary.scores.service_reasons || []).length
        ? t("lost_to", { list: G.summary.scores.service_reasons.join(t("list_sep")) })
        : (G.summary.scores.unhappy_callers ? t("unhappy", { n: G.summary.scores.unhappy_callers }) : "")) +
      ((G.summary.scores.security_reasons || []).length
        ? t("sec_moved", { list: G.summary.scores.security_reasons.join(t("list_sep")) }) : "") + t("end_dot")) : null,
    el("div", { class: "row" },
      el("button", { class: "primary big", onclick: () => { G.summary = null; render(); } }, t("next_day"))));
}

function idleView() {
  const s = G.state;
  const children = [];
  if (G.notice) children.push(el("div", { class: "notice" }, G.notice));
  const row = el("div", { class: "row" });
  if (s.calls_remaining_today > 0) {
    children.push(el("div", { class: "center" },
      el("button", { class: "primary big", disabled: G.busy, onclick: () => run(nextCall) }, t("answer")),
      el("div", { class: "sub" }, t("calls_left", { n: s.calls_remaining_today }))));
  } else {
    children.push(el("div", { class: "center" },
      el("div", {}, t("no_more")),
      el("div", { class: "row" },
        el("button", { class: "primary big", disabled: G.busy, onclick: () => run(endDay) }, t("end_day")))));
  }
  if (s.can_flag_last_call) {
    row.append(el("button", { disabled: G.busy, onclick: () => run(flagLast) }, t("flag")));
    children.push(row);
  }
  if (G.endedLog) {
    children.push(el("h3", { class: "section-title" }, t("last_call")), transcriptBox(G.endedLog, "ended"));
  }
  return el("div", {}, ...children);
}

function transcriptBox(messages, extraClass = "", typing = false) {
  const box = el("div", { class: `transcript ${extraClass}` });
  for (const m of messages) box.append(el("div", { class: `msg ${m.speaker}` }, m.text));
  if (typing) box.append(el("div", { class: "msg caller typing" }, el("span", {}, "•"), el("span", {}, "•"), el("span", {}, "•")));
  return box;
}

function callView() {
  const call = G.state.active_call;
  const card = el("div", { class: "call-card" + (G.transcript.length <= 1 ? " ringing" : "") },
    el("div", { class: "claims" }, call.claims),
    el("div", { class: "cid" }, t("caller_id", { id: call.caller_id })),
    call.patience_hint === "impatient" ? el("div", { class: "hint" }, t("impatient")) : null);

  const noReplies = G.state.replies_left_today <= 0;
  const input = el("textarea", {
    id: "composer-input", maxlength: 500, disabled: G.busy || noReplies,
    placeholder: noReplies ? t("ph_none") : G.transcript.length <= 1 ? t("ph_first") : t("ph_more"),
  });
  input.value = G.draft;
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); sendDraft(); }
  });
  const send = el("button", { class: "primary", disabled: G.busy || noReplies, onclick: sendDraft }, t("send"));
  const budget = el("div", { class: "budget" },
    t("budget", { call: Math.min(call.replies_left_in_call, G.state.replies_left_today), day: G.state.replies_left_today }));

  const holdsLeft = G.state.callbacks_left_today;
  const actions = el("div", { class: "actions" },
    menuButton("callback", t("hold", { n: holdsLeft }), callbackMenu, noReplies || holdsLeft <= 0),
    menuButton("transfer", t("transfer"), transferMenu),
    el("button", { disabled: G.busy, onclick: () => run(takeMessage) }, t("message")),
    el("button", { disabled: G.busy, onclick: () => run(hangUp) }, t("hangup")));

  const view = el("div", {}, card, transcriptBox(G.transcript, "", G.busy && !!G.pending),
    budget, el("div", { class: "composer" }, input, send), actions);
  if (!G.busy) setTimeout(() => { const box = $("#composer-input"); if (box && !G.menu) box.focus(); }, 0);
  return view;
}

function menuButton(name, label, build, off = false) {
  const wrap = el("div", { class: "menu-wrap" },
    el("button", {
      disabled: G.busy || off,
      onclick: (event) => { event.stopPropagation(); G.menu = G.menu === name ? null : name; G.confirmTransfer = null; render(); },
    }, label));
  if (G.menu === name) wrap.append(build());
  return wrap;
}

function callbackMenu() {
  const w = G.world;
  const call = G.state && G.state.active_call;
  const menu = el("div", { class: "menu" });
  menu.append(el("div", { class: "confirm-text" }, t("hold_warning")));
  if (call) {
    menu.append(el("div", { class: "group" }, t("the_caller")),
      el("button", { onclick: () => pick(() => callbackTo("CALLER_NUMBER")) },
        el("span", { class: "num" }, call.caller_id.split(" (")[0]), t("caller_number")));
  }
  menu.append(el("div", { class: "group" }, t("employees")));
  for (const e of w.employees) {
    menu.append(el("button", { onclick: () => pick(() => callbackTo(e.id)) },
      el("span", { class: "num" }, e.dept), e.name));
  }
  menu.append(el("div", { class: "group" }, t("vendors")));
  for (const v of w.vendors) {
    menu.append(el("button", { onclick: () => pick(() => callbackTo(v.id)) },
      el("span", { class: "num" }, v.role), v.name));
  }
  return menu;
}

function transferMenu() {
  const menu = el("div", { class: "menu" });
  if (G.confirmTransfer) {
    const target = G.world.employees.find((e) => e.id === G.confirmTransfer);
    menu.append(el("div", { class: "group" }, t("no_undo")),
      el("div", { class: "confirm-text" }, t("confirm_transfer", { name: target ? target.name : t("them") })),
      el("button", { class: "confirm-yes", onclick: () => { const id = G.confirmTransfer; G.confirmTransfer = null; pick(() => transferTo(id)); } }, t("yes_transfer")),
      el("button", { onclick: (event) => { event.stopPropagation(); G.confirmTransfer = null; render(); } }, t("back")));
    return menu;
  }
  for (const e of G.world.employees) {
    menu.append(el("button", { onclick: (event) => { event.stopPropagation(); G.confirmTransfer = e.id; render(); } },
      el("span", { class: "num" }, e.dept), `${e.name} — ${e.title}`));
  }
  return menu;
}

function pick(task) {
  G.menu = null;
  run(task);
}

function closeMenu() { G.menu = null; G.confirmTransfer = null; render(); }
document.addEventListener("click", () => { if (G.menu) closeMenu(); });
document.addEventListener("keydown", (event) => { if (event.key === "Escape" && G.menu) closeMenu(); });

// ------------------------------------------------------------------ side tabs

function renderTabs() {
  if (!G.world) return;
  const w = G.world;
  const field = (label, value) => value ? el("div", {}, el("span", { class: "label" }, label), el("span", { class: "mono" }, value)) : null;
  const entry = (title, sub, ...fields) => el("details", { class: "entry" },
    el("summary", {}, el("b", {}, title), el("span", { class: "sub" }, sub)),
    el("div", { class: "detail" }, ...fields));
  const directory = el("div", {},
    el("p", { class: "fine-print" }, t("click_name")),
    el("h3", {}, t("employees")),
    ...w.employees.map((e) => entry(e.name, `${e.title} · ${e.dept}`,
      field(t("f_ext"), e.ext), field(t("f_mobile"), e.mobile), field(t("f_empid"), e.employee_id),
      field(t("f_status"), e.status), field(t("f_note"), e.directory_note))),
    el("h3", {}, t("vendors")),
    ...w.vendors.map((v) => entry(v.name, v.role,
      field(t("f_phone"), v.official_number), field(t("f_contact"), v.contact))));
  $("#tab-directory").replaceChildren(directory);
  const co = w.company;
  $("#tab-desk").replaceChildren(
    el("h3", {}, t("company")),
    el("div", { class: "sticky" }, `${co.name} · ${co.address} · ${co.website}`,
      el("br"), t("main_line", { n: co.main_number }),
      el("br"), Object.entries(co.general_emails || {}).map(([k, v]) => `${k}: ${v}`).join(" · ")),
    el("h3", {}, t("sticky")), ...w.sticky_notes.map((n) => el("div", { class: "sticky" }, n)),
    el("h3", {}, t("internal")), el("ul", { class: "internal" }, w.internal_notes.map((n) => el("li", {}, n))));
  const level = G.state ? G.state.difficulty : "normal";
  const bulletin = (G.state && G.state.bulletin) || [];
  const policyTab = [];
  if (bulletin.length) {
    policyTab.push(el("h3", {}, t("bulletin")));
    for (const n of [...bulletin].reverse()) {
      policyTab.push(el("div", { class: `bulletin-item ${n.kind}` },
        el("div", { class: "bulletin-head" }, `${t("day_n", { n: n.day })} · ${n.kind === "policy" ? t("policy_update") : t("heads_up")}`),
        el("b", {}, n.title), el("div", {}, n.text)));
    }
    policyTab.push(el("h3", {}, t("standing_policy")));
  }
  policyTab.push(el("div", { class: "policy" }, w.policies[level] || w.policies.normal));
  if (level !== "hard" && w.playbook) {
    policyTab.push(el("h3", {}, t("playbook")), el("p", { class: "fine-print" }, w.playbook.intro),
      ...w.playbook.tactics.map((tac) => el("details", { class: "entry" },
        el("summary", {}, el("span", {}, tac.name)),
        el("div", { class: "detail" }, el("div", {}, el("span", { class: "label" }, t("sounds_like")), tac.sign),
          el("div", {}, el("span", { class: "label" }, t("what_to_do")), tac.counter)))),
      el("ul", { class: "internal" }, w.playbook.rules_of_thumb.map((n) => el("li", {}, n))));
  }
  $("#tab-policy").replaceChildren(...policyTab);
}

function setupTabs() {
  for (const tab of document.querySelectorAll(".tab")) {
    tab.addEventListener("click", () => {
      for (const other of document.querySelectorAll(".tab")) {
        other.classList.toggle("active", other === tab);
        $("#tab-" + other.dataset.tab).hidden = other !== tab;
      }
    });
  }
}

// ------------------------------------------------------------------ actions

const END_LINES = { caller_ended: "end_caller", patience: "end_patience", max_turns: "end_max", exposed: "end_exposed" };
const endLine = (reason) => t(END_LINES[reason] || "end_other");

function applyCallEnd(lines) {
  for (const text of lines) if (text) G.transcript.push({ speaker: "system", text });
  G.endedLog = G.transcript.slice();
  G.transcript = [];
}

async function startNewGame(name, difficulty) {
  const data = await api("POST", "/api/game", { player_name: name, difficulty, lang: G.lang });
  G.id = data.game_id;
  remember(G.id);
  Object.assign(G, { briefing: data.briefing, summary: null, transcript: [], endedLog: null, notice: "",
    cutscene: null, report: null, reportError: null, draft: "", menu: null, confirmTransfer: null });
  G.state = data.state;
}

async function startDay() {
  const data = await gameApi("POST", "day/start");
  G.notice = "";
  G.endedLog = null;
  G.state = data.state;
}

async function nextCall() {
  const data = await gameApi("POST", "call/next");
  G.notice = "";
  G.endedLog = null;
  G.transcript = data.call.transcript.slice();
  G.draft = "";
  G.state = data.state;
}

function sendDraft() {
  const input = $("#composer-input");
  const text = (input ? input.value : G.draft).trim();
  if (!text || G.busy) return;
  G.draft = "";
  if (input) input.value = "";
  run(() => say(text));
}

async function say(text) {
  G.transcript.push({ speaker: "player", text });
  G.pending = "say";
  render();
  try {
    const data = await gameApi("POST", "call/say", { text });
    G.transcript.push({ speaker: "caller", text: data.caller_reply });
    G.state = data.state;
    if (data.call_ended) applyCallEnd([endLine(data.end_reason)]);
  } catch (err) {
    G.transcript.pop();
    G.draft = text;
    throw err;
  }
}

async function callbackTo(targetId) {
  G.pending = "callback";
  render();
  const data = await gameApi("POST", "call/callback", { target_id: targetId });
  G.transcript.push({ speaker: "system", text: data.callback_result });
  if (data.caller_reply) G.transcript.push({ speaker: "caller", text: data.caller_reply });
  G.state = data.state;
  if (data.call_ended) applyCallEnd([endLine(data.end_reason)]);
  if (data.game_over) G.cutscene = data.cutscene;
}

async function transferTo(targetId) {
  const data = await gameApi("POST", "call/transfer", { target_id: targetId });
  G.state = data.state;
  applyCallEnd([data.result_text]);
  if (data.game_over) G.cutscene = data.cutscene;
}

async function takeMessage() {
  const data = await gameApi("POST", "call/message", { note: "" });
  G.state = data.state;
  applyCallEnd([data.result_text]);
}

async function hangUp() {
  const data = await gameApi("POST", "call/hangup");
  G.state = data.state;
  applyCallEnd([t("you_hung_up")]);
}

async function flagLast() {
  const data = await gameApi("POST", "flag");
  G.notice = data.result_text;
  G.state = data.state;
  if (data.game_over) G.cutscene = data.cutscene;
}

async function endDay() {
  const data = await gameApi("POST", "day/end");
  G.state = data.state;
  G.endedLog = null;
  G.notice = "";
  if (data.game_over) { G.cutscene = data.cutscene; return; }
  G.briefing = data.briefing;
  G.summary = data.day_summary ? { text: data.day_summary, scores: data.day_scores } : null;
}

// ------------------------------------------------------------------ end page

function renderEnd() {
  showOnly("end-screen");
  const root = $("#end-screen");
  const parts = [];
  const cutscene = G.cutscene || (G.report && G.report.cutscene);
  if (cutscene) parts.push(el("div", { class: "cutscene" }, cutscene));
  if (!G.report) {
    if (G.reportError) {
      parts.push(el("div", { class: "notice" }, G.reportError,
        el("div", { class: "row" }, el("button", { onclick: loadReport }, t("try_again")))));
    } else {
      parts.push(el("div", { class: "notice fade-in" }, t("writing_report")));
      loadReport();
    }
    root.replaceChildren(...parts);
    return;
  }
  const r = G.report;
  const fade = (node) => { node.classList.add("fade-in"); return node; };
  parts.push(fade(el("div", { class: "badge" },
    el("h2", {}, r.title_badge || r.title),
    el("div", { class: "fine-print" }, r.title),
    el("div", { class: "scores" },
      el("div", {}, `🛡 ${t("security")} ${r.scores.security} — ${r.labels.security}`),
      el("div", {}, `🙂 ${t("service")} ${r.scores.service} — ${r.labels.service}`)))));
  const report = el("div", { class: "report" });
  report.innerHTML = r.html;
  parts.push(fade(report));

  if (r.scenario) {
    const sc = r.scenario;
    parts.push(fade(el("h2", { class: "section-title" }, t("really"))));
    parts.push(fade(el("div", { class: "reveal" },
      el("b", {}, sc.title),
      el("p", {}, sc.premise),
      el("p", {}, el("span", { class: "label" }, t("their_goal")), sc.con_goal),
      sc.crew ? el("p", {}, el("span", { class: "label" }, t("the_crew")), sc.crew) : null,
      sc.motive ? el("p", {}, el("span", { class: "label" }, t("why_now")), sc.motive) : null,
      sc.gambit ? el("p", {}, el("span", { class: "label" }, t("their_method", { name: sc.gambit.name })), sc.gambit.summary) : null,
      sc.phases && sc.phases.length ? el("p", {}, el("span", { class: "label" }, t("their_plan")),
        sc.phases.map((p, i) => t("plan_day", { n: i + 1, p })).join("  ")) : null,
      sc.gambit ? el("p", {}, el("span", { class: "label" }, t("how_spot")), `${sc.gambit.sign} ${sc.gambit.counter}`) : null,
      ...(sc.switches || []).map((m) => el("p", {}, el("span", { class: "label" }, t("switched", { day: m.day_name, name: m.name })),
        `${m.summary} ${t("how_spot")}${m.sign} ${m.counter}`)),
      sc.incident ? el("p", {}, el("span", { class: "label" }, t("incident_label", {
        title: lang() === "zh" ? sc.incident.title : sc.incident.title.toLowerCase(), day: sc.incident.day_name })),
        sc.incident.method) : null)));
  }

  if (r.calls && r.calls.length) {
    parts.push(fade(el("h2", { class: "section-title" }, t("every_call"))));
    for (const c of r.calls) parts.push(fade(revealCard(c)));
  }

  parts.push(fade(el("h2", { class: "section-title" }, t("notebook"))));
  for (const [day, text] of Object.entries(r.notebooks)) {
    parts.push(fade(el("div", { class: "notebook-page" }, el("h4", {}, t("day_n", { n: day })), text)));
  }

  parts.push(fade(el("h2", { class: "section-title" }, t("learned_title"))));
  if (r.intel_timeline.length === 0) {
    parts.push(fade(el("p", {}, t("learned_none"))));
  } else {
    parts.push(fade(el("div", { class: "scroll-x" }, el("table", { class: "intel-table" },
      el("tr", {}, ["th_day", "th_time", "th_info", "th_words", "th_source"].map((h) => el("th", {}, t(h)))),
      r.intel_timeline.map((row) => el("tr", {},
        el("td", {}, row.day), el("td", { class: "mono" }, row.time),
        el("td", {}, row.description), el("td", {}, `“${row.quote}”`), el("td", {}, sourceLabel(row.source))))))));
  }

  parts.push(fade(el("h2", { class: "section-title" }, t("stats"))));
  const labels = {
    calls_taken: t("s_calls"), callbacks: t("s_callbacks"), exposures: t("s_exposures"),
    correct_flags: t("s_correct"), false_flags: t("s_wrong"), overshares_to_legit: t("s_overshare"),
    facts_leaked_to_con: t("s_leaked"),
  };
  parts.push(fade(el("div", { class: "stats" }, Object.entries(labels).map(([key, label]) =>
    el("div", { class: "stat" }, el("b", {}, r.stats[key]), label)))));

  parts.push(fade(el("div", { class: "end-buttons" },
    el("a", { class: "button", href: `/api/game/${G.id}/export`, download: `frontdesk-${G.id}.json` },
      t("download")),
    el("button", { class: "primary", onclick: playAgain }, t("play_again")))));
  root.replaceChildren(...parts);
}

function revealCard(c) {
  if (!c.is_con && !c.player.length) {
    return el("div", { class: "reveal reveal-line" }, el("span", { class: "tag legit" }, t("real")),
      ` ${t("day_time", { n: c.day, time: c.time })} ${c.claims} — ${c.outcome}`);
  }
  const rows = [
    el("div", { class: "reveal-head" },
      el("span", { class: `tag ${c.is_con ? "con" : "legit"}` }, c.is_con ? t("con") : t("real")),
      el("b", {}, t("day_time", { n: c.day, time: c.time })), ` ${c.claims}`),
  ];
  const sep = t("list_sep");
  if (c.impersonating) rows.push(el("p", {}, el("span", { class: "label" }, t("pretending")), c.impersonating));
  if (c.special) rows.push(el("p", {}, el("span", { class: "label" }, t("special")), c.special));
  if (c.tactic) {
    rows.push(el("p", {}, el("span", { class: "label" }, t("move")), `${c.tactic.name}${lang() === "zh" ? "。" : ". "}${c.tactic.sign}`),
      el("p", {}, el("span", { class: "label" }, t("counter")), c.tactic.counter));
  }
  if (c.purpose) rows.push(el("p", {}, el("span", { class: "label" }, t("purpose")), c.purpose));
  if (c.slip) rows.push(el("p", {}, el("span", { class: "label" }, t("slip")), c.slip));
  if (c.script && c.script.length) {
    rows.push(el("p", {}, el("span", { class: "label" }, t("script")), c.script.map((b, i) => `${i + 1}. ${b}`).join("  ")));
  }
  rows.push(el("p", {}, el("span", { class: "label" }, t("ended")), c.outcome));
  if (c.player.length) rows.push(el("p", {}, el("span", { class: "label" }, t("you")), c.player.join(sep)));
  if (c.vouched) rows.push(el("p", {}, el("span", { class: "label" }, t("careful")), t("accomplice")));
  if (c.learned.length) rows.push(el("p", {}, el("span", { class: "label" }, t("they_learned")), c.learned.join(sep)));
  return el("div", { class: "reveal" }, ...rows);
}

function sourceLabel(source) {
  const key = { llm: "src_llm", pattern: "src_pattern", both: "src_both", recon: "src_recon" }[source];
  return key ? t(key) : source;
}

async function loadReport() {
  if (G.reportLoading) return;
  G.reportLoading = true;
  G.reportError = null;
  try {
    G.report = await api("GET", `/api/game/${G.id}/report`);
  } catch (err) {
    G.reportError = t("report_failed", { msg: err.message });
  } finally {
    G.reportLoading = false;
    render();
  }
}

function playAgain() {
  remember(null);
  if (G.state && G.state.lang) G.lang = G.state.lang;
  Object.assign(G, { id: null, state: null, briefing: null, summary: null, transcript: [], endedLog: null,
    notice: "", cutscene: null, report: null, reportError: null, draft: "", menu: null, confirmTransfer: null });
  window.scrollTo(0, 0);
  render();
}

// ------------------------------------------------------------------ boot

async function boot() {
  setupTabs();
  G.lang = savedLang();
  for (const radio of document.querySelectorAll("input[name=lang]")) {
    radio.addEventListener("change", async () => {
      if (!radio.checked || G.state) return;
      G.lang = radio.value;
      saveLang(G.lang);
      render();
      try { await loadWorld(); } catch (err) { toast(t("no_directory")); }
      render();
    });
  }
  $("#start-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const name = $("#name-input").value;
    const difficulty = document.querySelector("input[name=difficulty]:checked").value;
    run(() => startNewGame(name, difficulty));
  });

  const saved = remembered();
  if (saved) {
    try {
      G.id = saved;
      const data = await api("GET", `/api/game/${saved}`);
      G.state = data.state;
      if (data.briefing) G.briefing = data.briefing;
      if (data.state.active_call) G.transcript = data.state.active_call.transcript.slice();
    } catch (err) {
      G.id = null;
      remember(null);
    }
  }
  const [world, health] = await Promise.allSettled([loadWorld(), api("GET", "/api/health")]);
  if (world.status !== "fulfilled") toast(t("no_directory"));
  if (health.status === "fulfilled") {
    G.health = health.value;
    if (!health.value.llm_configured) {
      const banner = $("#banner");
      banner.textContent = (G.world && G.world.ui && G.world.ui.llm_banner) || t("llm_banner");
      banner.hidden = false;
    }
  }
  render();
}

boot();
