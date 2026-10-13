const state = {
  policy: "v0",
  v1Ready: false,
  view: "desk",
  session: null,
  scenarios: [],
  busy: false,
};

const $ = (id) => document.getElementById(id);

function node(tag, className, text) {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (text != null) el.textContent = text;
  return el;
}

async function api(path, body) {
  const response = await fetch(path, {
    method: body ? "POST" : "GET",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || "The desk could not complete that.");
  return data;
}

function showBanner(text) {
  const banner = $("banner");
  if (!text) {
    banner.hidden = true;
    banner.textContent = "";
    return;
  }
  banner.hidden = false;
  banner.textContent = text;
}

function setView(name) {
  state.view = name;
  $("desk").hidden = name !== "desk";
  $("evaluation").hidden = name !== "eval";
  document.querySelectorAll(".view").forEach((button) => {
    button.classList.toggle("on", button.dataset.view === name);
  });
}

function paintPolicy() {
  document.querySelectorAll(".policy").forEach((button) => {
    button.classList.toggle("on", button.dataset.policy === state.policy);
  });
  $("policyV1").disabled = !state.v1Ready;
  $("policyV1").title = state.v1Ready ? "Use the installed red-flag rule" : "Run the evaluation first";
}

function paintSession(data) {
  state.session = data;
  $("clock").textContent = data.clock;
  paintChart(data.chart);
  paintTalk(data);
  paintTrace(data);
}

function paintChart(chart) {
  const root = $("chartBody");
  root.replaceChildren();
  root.append(section("On the book", chart.appointments.map((row) => {
    const item = node("div", row.fresh ? "row fresh" : "row");
    item.append(node("strong", null, row.patient));
    item.append(node("span", null, `${row.when} · ${row.provider}`));
    if (row.fresh) item.append(node("span", null, "Booked this visit"));
    return item;
  }), "No booked visits."));
  root.append(section("Openings", chart.openings.map((row) => {
    const item = node("div", "row");
    item.append(node("strong", null, row.when));
    item.append(node("span", null, `${row.provider} · ${row.specialty}`));
    return item;
  }), "The book is full."));
  const alerts = node("div", "block");
  alerts.append(node("h2", null, "Escalations"));
  if (!chart.escalations.length) {
    alerts.append(node("p", "quiet", "None this visit."));
  }
  chart.escalations.forEach((item) => {
    const card = node("div", "esc");
    card.append(node("b", null, item.category.replaceAll("_", " ")));
    card.append(document.createElement("br"));
    card.append(document.createTextNode(item.summary));
    alerts.append(card);
  });
  root.append(alerts);
}

function section(title, rows, empty) {
  const block = node("div", "block");
  block.append(node("h2", null, title));
  if (!rows.length) block.append(node("p", "quiet", empty));
  rows.forEach((row) => block.append(row));
  return block;
}

function paintTalk(data) {
  const log = $("log");
  const grade = $("grade");
  log.replaceChildren();
  if (data.grade) {
    grade.hidden = false;
    grade.className = `grade ${data.grade.passed ? "pass" : "fail"}`;
    const mark = data.grade.passed ? "PASS" : "FAIL";
    grade.textContent = `${data.grade.title} · ${mark} · ${data.grade.outcome}`;
  } else {
    grade.hidden = true;
  }
  if (!data.transcript.length) {
    const empty = node("div", "empty");
    empty.append(node("strong", null, "Start a visit"));
    empty.append(node("p", null, "Type as the patient and a live model answers. Scripted visits on the side are the fixed test. After the evaluation, switch the policy and run the same visit again."));
    log.append(empty);
    return;
  }
  const urgent = data.signals.escalated;
  data.transcript.forEach((item) => {
    const bubble = node("article", `bubble ${item.role}${item.role === "agent" && urgent ? " urgent" : ""}`);
    bubble.append(node("span", "who", item.role === "patient" ? "Patient" : "Riverside"));
    bubble.append(node("div", "text", item.text));
    log.append(bubble);
  });
  log.scrollTop = log.scrollHeight;
}

function paintTrace(data) {
  const flags = $("signals");
  flags.replaceChildren();
  const name = data.signals.verified_name;
  flags.append(chip(name ? `Chart: ${name}` : "Chart not open", Boolean(name)));
  flags.append(chip(data.signals.intent ? `Intent: ${data.signals.intent}` : "Intent not set", Boolean(data.signals.intent)));
  if (data.signals.red_flag) flags.append(chip(data.signals.red_flag.replaceAll("_", " "), true, true));
  if (data.signals.escalated) flags.append(chip("On-call alerted", true, true));
  if (data.signals.guardian_ok) flags.append(chip("Guardian confirmed", true));
  $("ruleLine").textContent = data.rules.join(" · ");
  const events = $("events");
  events.replaceChildren();
  if (!data.events.length) {
    events.append(node("p", "quiet", data.transcript.length
      ? "No tool was called. The agent is still asking questions before it touches the chart."
      : "Tool calls and policy decisions show up here after the first reply."));
    return;
  }
  data.events.forEach((event) => {
    const row = node("div", `event ${event.kind}`);
    row.append(node("span", "turn", `T${event.turn}`));
    row.append(document.createTextNode(event.text));
    events.append(row);
  });
  events.scrollTop = events.scrollHeight;
}

function chip(text, on, warn) {
  return node("span", `chip${on ? " on" : ""}${warn ? " warn" : ""}`, text);
}

function paintScripts() {
  const root = $("scripts");
  root.replaceChildren();
  state.scenarios.forEach((scenario) => {
    const button = node("button", "script");
    button.type = "button";
    button.append(node("span", null, scenario.title));
    button.append(node("small", null, scenario.why));
    button.addEventListener("click", () => replay(scenario.id));
    root.append(button);
  });
}

async function openSession(policy) {
  const data = await api("/api/sessions", { policy });
  state.policy = policy;
  paintPolicy();
  paintSession(data);
  showBanner("");
}

async function replay(scenarioId) {
  if (state.busy) return;
  state.busy = true;
  try {
    setView("desk");
    const data = await api("/api/replay", { policy: state.policy, scenario_id: scenarioId });
    paintSession(data);
  } catch (error) {
    showBanner(error.message);
  } finally {
    state.busy = false;
  }
}

async function sendMessage(text) {
  if (!state.session || state.busy) return;
  state.busy = true;
  $("send").disabled = true;
  try {
    const data = await api(`/api/sessions/${state.session.id}/messages`, { text });
    paintSession(data);
    $("message").value = "";
  } catch (error) {
    showBanner(error.message);
  } finally {
    state.busy = false;
    $("send").disabled = false;
    $("message").focus();
  }
}

function paintEval(report) {
  const root = $("evalBody");
  root.replaceChildren();
  const grid = node("div", "score-grid");
  grid.append(scoreCard("Before", report.before, "v0"));
  if (report.after) grid.append(scoreCard("After", report.after, "v1"));
  root.append(grid);
  const list = node("div", "candidates");
  report.candidates.forEach((candidate) => {
    const card = node("article", `candidate ${candidate.accepted ? "accept" : "reject"}`);
    const head = node("header");
    head.append(node("strong", null, candidate.id));
    head.append(node("span", `tag ${candidate.accepted ? "ok" : "bad"}`, candidate.accepted ? "Accepted" : "Rejected"));
    card.append(head);
    card.append(node("p", null, candidate.note));
    const bits = [];
    if (candidate.fixed.length) bits.push(`Fixed: ${candidate.fixed.join(", ")}`);
    if (candidate.still_failing.length) bits.push(`Still failing: ${candidate.still_failing.join(", ")}`);
    if (candidate.regressions.length) bits.push(`Regressions: ${candidate.regressions.join(", ")}`);
    card.append(node("p", "who-line", bits.join("  ·  ")));
    list.append(card);
  });
  root.append(list);
  const spots = node("ul", "spots");
  [
    "A transcript alone cannot see whether the slot was free or whether the cancel hit the right row.",
    "A tool trace alone cannot see a dose, a leaked time, or a yes that never named a slot.",
  ].forEach((line) => spots.append(node("li", null, line)));
  root.append(spots);
}

function scoreCard(label, block, policy) {
  const card = node("section", "score-card");
  const head = node("header");
  head.append(node("span", "tag", label));
  head.append(node("span", "who-line", block.version));
  card.append(head);
  card.append(node("div", "big", `${block.passed}/${block.total}`));
  block.rows.forEach((row) => {
    const line = node("div", "visit");
    line.append(node("span", null, row.passed ? "·" : "!"));
    const name = node("span", null, row.id);
    line.append(name);
    const action = node("button", null, "Replay");
    action.type = "button";
    action.title = row.outcome;
    action.addEventListener("click", async () => {
      state.policy = policy;
      paintPolicy();
      await replay(row.id);
    });
    line.append(action);
    card.append(line);
  });
  return card;
}

async function runEval() {
  const button = $("runEval");
  button.disabled = true;
  button.textContent = "Scoring…";
  showBanner("");
  try {
    const report = await api("/api/eval", {});
    state.v1Ready = report.v1_ready;
    paintPolicy();
    paintEval(report);
    if (report.v1_ready) {
      showBanner("Rule installed. Switch to After the rule, then replay chest pain or self-harm.");
    }
  } catch (error) {
    showBanner(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "Run the loop";
  }
}

function bind() {
  document.querySelectorAll(".view").forEach((button) => {
    button.addEventListener("click", () => setView(button.dataset.view));
  });
  document.querySelectorAll(".policy").forEach((button) => {
    button.addEventListener("click", async () => {
      if (button.disabled || button.dataset.policy === state.policy) return;
      try {
        await openSession(button.dataset.policy);
      } catch (error) {
        showBanner(error.message);
      }
    });
  });
  $("newVisit").addEventListener("click", () => openSession(state.policy).catch((error) => showBanner(error.message)));
  $("composer").addEventListener("submit", (event) => {
    event.preventDefault();
    const text = $("message").value.trim();
    if (text) sendMessage(text);
  });
  $("message").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      $("composer").requestSubmit();
    }
  });
  $("runEval").addEventListener("click", runEval);
}

async function boot() {
  bind();
  try {
    const meta = await api("/api/meta");
    state.v1Ready = meta.v1_ready;
    state.scenarios = meta.scenarios;
    $("clock").textContent = meta.clock;
    $("live").textContent = meta.live && meta.live.ready
      ? `Live model ${meta.live.model}`
      : "Typed chat needs OPENAI_API_KEY";
    paintPolicy();
    paintScripts();
    await openSession("v0");
  } catch (error) {
    showBanner(error.message);
  }
}

boot();
