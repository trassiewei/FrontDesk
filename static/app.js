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
};

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
    throw new Error("Could not reach the server.");
  }
  let data = null;
  try { data = await response.json(); } catch (err) { /* not JSON */ }
  if (!response.ok) {
    let detail = data && data.detail;
    if (Array.isArray(detail)) detail = "That request was not valid.";
    const error = new Error(detail || `Request failed (${response.status}).`);
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
  $("#start-button").textContent = G.busy ? "Preparing today's calls… (this can take a few seconds)" : "Start your shift";
}

function renderTopbar() {
  const s = G.state;
  $("#top-clock").textContent = `${s.day_name} · ${s.clock}`;
  $("#top-replies b").textContent = s.replies_left_today;
  $("#top-replies").classList.toggle("low", s.replies_left_today <= 3);
  $("#top-security b").textContent = s.security;
  $("#top-service b").textContent = s.service;
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
      el("button", { class: "primary big", disabled: G.busy, onclick: () => run(startDay) }, "Start the day")));
}

function noticeBox(n) {
  return el("div", { class: `bulletin-item ${n.kind}` },
    el("div", { class: "bulletin-head" }, n.header),
    el("b", {}, n.title),
    el("div", {}, n.text));
}

function summaryView() {
  return el("div", { class: "briefing" },
    el("h2", {}, "End of day"),
    el("div", { class: "body" }, G.summary.text),
    el("div", { class: "row" },
      el("button", { class: "primary big", onclick: () => { G.summary = null; render(); } }, "Next day")));
}

function idleView() {
  const s = G.state;
  const children = [];
  if (G.notice) children.push(el("div", { class: "notice" }, G.notice));
  const row = el("div", { class: "row" });
  if (s.calls_remaining_today > 0) {
    children.push(el("div", { class: "center" },
      el("button", { class: "primary big", disabled: G.busy, onclick: () => run(nextCall) }, "📞 Answer next call"),
      el("div", { class: "sub" }, `Calls left today: ${s.calls_remaining_today}`)));
  } else {
    children.push(el("div", { class: "center" },
      el("div", {}, "No more calls today."),
      el("div", { class: "row" },
        el("button", { class: "primary big", disabled: G.busy, onclick: () => run(endDay) }, "End the day"))));
  }
  if (s.can_flag_last_call) {
    row.append(el("button", { disabled: G.busy, onclick: () => run(flagLast) }, "🚩 Flag last call as suspicious"));
    children.push(row);
  }
  if (G.endedLog) {
    children.push(el("h3", { class: "section-title" }, "Last call"), transcriptBox(G.endedLog, "ended"));
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
    el("div", { class: "cid" }, `Caller ID: ${call.caller_id}`),
    call.patience_hint === "impatient" ? el("div", { class: "hint" }, "😤 sounds impatient") : null);

  const noReplies = G.state.replies_left_today <= 0;
  const input = el("textarea", {
    id: "composer-input", maxlength: 500, disabled: G.busy || noReplies,
    placeholder: noReplies ? "No replies left today. Transfer, take a message or hang up."
      : G.transcript.length <= 1 ? "The caller has already said why they called. Don't spend a reply on a greeting. (Enter to send)"
      : "Say something… (Enter to send, Shift+Enter for a new line)",
  });
  input.value = G.draft;
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); sendDraft(); }
  });
  const send = el("button", { class: "primary", disabled: G.busy || noReplies, onclick: sendDraft }, "Send");
  const budget = el("div", { class: "budget" },
    `Replies left: ${Math.min(call.replies_left_in_call, G.state.replies_left_today)} on this call · ` +
    `${G.state.replies_left_today} today`);

  const actions = el("div", { class: "actions" },
    menuButton("callback", "⏸ Hold & call back… (1 reply)", callbackMenu, noReplies),
    menuButton("transfer", "↪ Transfer…", transferMenu),
    el("button", { disabled: G.busy, onclick: () => run(takeMessage) }, "📝 Take a message"),
    el("button", { disabled: G.busy, onclick: () => run(hangUp) }, "✖ Hang up"));

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
  if (call) {
    menu.append(el("div", { class: "group" }, "The caller"),
      el("button", { onclick: () => pick(() => callbackTo("CALLER_NUMBER")) },
        el("span", { class: "num" }, call.caller_id.split(" (")[0]), "Number the caller gave"));
  }
  menu.append(el("div", { class: "group" }, "Employees"));
  for (const e of w.employees) {
    menu.append(el("button", { onclick: () => pick(() => callbackTo(e.id)) },
      el("span", { class: "num" }, e.dept), e.name));
  }
  menu.append(el("div", { class: "group" }, "Vendors"));
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
    menu.append(el("div", { class: "group" }, "This cannot be undone"),
      el("div", { class: "confirm-text" }, `Transfer this caller to ${target ? target.name : "them"}?`),
      el("button", { class: "confirm-yes", onclick: () => { const id = G.confirmTransfer; G.confirmTransfer = null; pick(() => transferTo(id)); } }, "Yes, transfer"),
      el("button", { onclick: (event) => { event.stopPropagation(); G.confirmTransfer = null; render(); } }, "Back"));
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
    el("p", { class: "fine-print" }, "Click a name for numbers and notes."),
    el("h3", {}, "Employees"),
    ...w.employees.map((e) => entry(e.name, `${e.title} · ${e.dept}`,
      field("Ext", e.ext), field("Mobile", e.mobile), field("Employee ID", e.employee_id),
      field("Status", e.status), field("Note", e.directory_note))),
    el("h3", {}, "Vendors"),
    ...w.vendors.map((v) => entry(v.name, v.role,
      field("Official phone", v.official_number), field("Contact", v.contact))));
  $("#tab-directory").replaceChildren(directory);
  $("#tab-desk").replaceChildren(
    el("h3", {}, "Sticky notes"), ...w.sticky_notes.map((n) => el("div", { class: "sticky" }, n)),
    el("h3", {}, "Internal notes"), el("ul", { class: "internal" }, w.internal_notes.map((n) => el("li", {}, n))));
  const level = G.state ? G.state.difficulty : "normal";
  const bulletin = (G.state && G.state.bulletin) || [];
  const policyTab = [];
  if (bulletin.length) {
    policyTab.push(el("h3", {}, "Bulletin"));
    for (const n of [...bulletin].reverse()) {
      policyTab.push(el("div", { class: `bulletin-item ${n.kind}` },
        el("div", { class: "bulletin-head" }, `Day ${n.day} · ${n.kind === "policy" ? "Policy update" : "Heads up"}`),
        el("b", {}, n.title), el("div", {}, n.text)));
    }
    policyTab.push(el("h3", {}, "Standing policy"));
  }
  policyTab.push(el("div", { class: "policy" }, w.policies[level] || w.policies.normal));
  if (level !== "hard" && w.playbook) {
    policyTab.push(el("h3", {}, "Playbook: how con artists work"), el("p", { class: "fine-print" }, w.playbook.intro),
      ...w.playbook.tactics.map((t) => el("details", { class: "entry" },
        el("summary", {}, el("span", {}, t.name)),
        el("div", { class: "detail" }, el("div", {}, el("span", { class: "label" }, "Sounds like"), t.sign),
          el("div", {}, el("span", { class: "label" }, "What to do"), t.counter)))),
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

function applyCallEnd(lines) {
  for (const text of lines) if (text) G.transcript.push({ speaker: "system", text });
  G.endedLog = G.transcript.slice();
  G.transcript = [];
}

async function startNewGame(name, difficulty) {
  const data = await api("POST", "/api/game", { player_name: name, difficulty });
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
    if (data.call_ended) applyCallEnd(["— The call has ended. —"]);
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
  if (data.call_ended) applyCallEnd(["— The call has ended. —"]);
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
  applyCallEnd(["You hung up."]);
}

async function flagLast() {
  const data = await gameApi("POST", "flag");
  G.notice = data.result_text;
  G.state = data.state;
}

async function endDay() {
  const data = await gameApi("POST", "day/end");
  G.state = data.state;
  G.endedLog = null;
  G.notice = "";
  if (data.game_over) { G.cutscene = data.cutscene; return; }
  G.briefing = data.briefing;
  G.summary = data.day_summary ? { text: data.day_summary } : null;
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
        el("div", { class: "row" }, el("button", { onclick: loadReport }, "Try again"))));
    } else {
      parts.push(el("div", { class: "notice fade-in" }, "Writing the post-game report…"));
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
      el("div", {}, `🛡 Security ${r.scores.security} — ${r.labels.security}`),
      el("div", {}, `🙂 Service ${r.scores.service} — ${r.labels.service}`)))));
  const report = el("div", { class: "report" });
  report.innerHTML = r.html;
  parts.push(fade(report));

  if (r.scenario) {
    const sc = r.scenario;
    parts.push(fade(el("h2", { class: "section-title" }, "What was really going on")));
    parts.push(fade(el("div", { class: "reveal" },
      el("b", {}, sc.title),
      el("p", {}, sc.premise),
      el("p", {}, el("span", { class: "label" }, "Their goal: "), sc.con_goal),
      sc.incident ? el("p", {}, el("span", { class: "label" }, `The ${sc.incident.title.toLowerCase()} (${sc.incident.day_name}): `), sc.incident.method) : null)));
  }

  if (r.calls && r.calls.length) {
    parts.push(fade(el("h2", { class: "section-title" }, "Every call, revealed")));
    for (const c of r.calls) parts.push(fade(revealCard(c)));
  }

  parts.push(fade(el("h2", { class: "section-title" }, "The con artist's notebook")));
  for (const [day, text] of Object.entries(r.notebooks)) {
    parts.push(fade(el("div", { class: "notebook-page" }, el("h4", {}, `Day ${day}`), text)));
  }

  parts.push(fade(el("h2", { class: "section-title" }, "What they learned")));
  if (r.intel_timeline.length === 0) {
    parts.push(fade(el("p", {}, "Nothing at all. The con artist learned nothing from you.")));
  } else {
    parts.push(fade(el("div", { class: "scroll-x" }, el("table", { class: "intel-table" },
      el("tr", {}, ["Day", "Time", "Information", "Your words", "Source"].map((h) => el("th", {}, h))),
      r.intel_timeline.map((row) => el("tr", {},
        el("td", {}, row.day), el("td", { class: "mono" }, row.time),
        el("td", {}, row.description), el("td", {}, `“${row.quote}”`), el("td", {}, sourceLabel(row.source))))))));
  }

  parts.push(fade(el("h2", { class: "section-title" }, "Stats")));
  const labels = {
    calls_taken: "Calls taken", callbacks: "Callbacks made", exposures: "Callers exposed",
    correct_flags: "Correct flags", false_flags: "Wrong flags", overshares_to_legit: "Overshares to real callers",
    facts_leaked_to_con: "Facts the con learned",
  };
  parts.push(fade(el("div", { class: "stats" }, Object.entries(labels).map(([key, label]) =>
    el("div", { class: "stat" }, el("b", {}, r.stats[key]), label)))));

  parts.push(fade(el("div", { class: "end-buttons" },
    el("a", { class: "button", href: `/api/game/${G.id}/export`, download: `frontdesk-${G.id}.json` },
      "Download game log (JSON)"),
    el("button", { class: "primary", onclick: playAgain }, "Play again"))));
  root.replaceChildren(...parts);
}

function revealCard(c) {
  if (!c.is_con && !c.player.length) {
    return el("div", { class: "reveal reveal-line" }, el("span", { class: "tag legit" }, "REAL"),
      ` Day ${c.day} · ${c.time} ${c.claims} — ${c.outcome}`);
  }
  const rows = [
    el("div", { class: "reveal-head" },
      el("span", { class: `tag ${c.is_con ? "con" : "legit"}` }, c.is_con ? "CON" : "REAL"),
      el("b", {}, `Day ${c.day} · ${c.time}`), ` ${c.claims}`),
  ];
  if (c.impersonating) rows.push(el("p", {}, el("span", { class: "label" }, "Pretending to be: "), c.impersonating));
  if (c.tactic) {
    rows.push(el("p", {}, el("span", { class: "label" }, "Move: "), `${c.tactic.name}. ${c.tactic.sign}`),
      el("p", {}, el("span", { class: "label" }, "Counter: "), c.tactic.counter));
  }
  if (c.script && c.script.length) {
    rows.push(el("p", {}, el("span", { class: "label" }, "Their script: "), c.script.map((b, i) => `${i + 1}. ${b}`).join("  ")));
  }
  rows.push(el("p", {}, el("span", { class: "label" }, "How it ended: "), c.outcome));
  if (c.player.length) rows.push(el("p", {}, el("span", { class: "label" }, "You: "), c.player.join("; ")));
  if (c.vouched) rows.push(el("p", {}, el("span", { class: "label" }, "Careful: "),
    "the number they gave you was their accomplice. Whoever answers there will vouch for a con."));
  if (c.learned.length) rows.push(el("p", {}, el("span", { class: "label" }, "They learned: "), c.learned.join("; ")));
  return el("div", { class: "reveal" }, ...rows);
}

function sourceLabel(source) {
  return { llm: "LLM", pattern: "pattern", both: "LLM + pattern", recon: "crew's own research" }[source] || source;
}

async function loadReport() {
  if (G.reportLoading) return;
  G.reportLoading = true;
  G.reportError = null;
  try {
    G.report = await api("GET", `/api/game/${G.id}/report`);
  } catch (err) {
    G.reportError = `Could not load the report: ${err.message}`;
  } finally {
    G.reportLoading = false;
    render();
  }
}

function playAgain() {
  remember(null);
  Object.assign(G, { id: null, state: null, briefing: null, summary: null, transcript: [], endedLog: null,
    notice: "", cutscene: null, report: null, reportError: null, draft: "", menu: null, confirmTransfer: null });
  window.scrollTo(0, 0);
  render();
}

// ------------------------------------------------------------------ boot

async function boot() {
  setupTabs();
  $("#start-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const name = $("#name-input").value;
    const difficulty = document.querySelector("input[name=difficulty]:checked").value;
    run(() => startNewGame(name, difficulty));
  });

  const [world, health] = await Promise.allSettled([api("GET", "/api/world"), api("GET", "/api/health")]);
  if (world.status === "fulfilled") G.world = world.value;
  else toast("Could not load the directory.");
  if (health.status === "fulfilled") {
    G.health = health.value;
    if (!health.value.llm_configured) {
      const banner = $("#banner");
      banner.textContent = (G.world && G.world.ui && G.world.ui.llm_banner) || "LLM is not configured — see README";
      banner.hidden = false;
    }
  }

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
  render();
}

boot();
