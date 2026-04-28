// ── State tracking ─────────────────────────────────────────────────────────────
let prevVoiceState = null;

// ── Polling ─────────────────────────────────────────────────────────────────────
async function loadState() {
  try {
    const res   = await fetch("/api/state");
    const state = await res.json();
    renderState(state);
  } catch (err) {
    console.error("[momo] Failed to load state:", err);
  }
}

// ── Main render ─────────────────────────────────────────────────────────────────
function renderState(state) {
  // clock
  document.getElementById("time").innerText = state.time;
  document.getElementById("date").innerText = state.date;

  // weather
  document.getElementById("weather-temp").innerText = state.weather.temp + "°F";
  document.getElementById("weather-desc").innerText = state.weather.description;
  document.getElementById("weather-city").innerText = state.weather.city;

  // tasks
  renderTasks(state.tasks);

  // screen routing: voice states override the base screen
  const vs = state.voice_state;
  if (vs === "listening" || vs === "thinking" || vs === "speaking") {
    showScreen("voice");
    updateVoiceScreen(vs);
  } else {
    showScreen(state.screen);
  }

  prevVoiceState = vs;
}

// ── Screen switching ────────────────────────────────────────────────────────────
function showScreen(name) {
  document.querySelectorAll(".screen").forEach(s => s.classList.add("hidden"));

  const el = document.getElementById(name + "-screen");
  if (el) el.classList.remove("hidden");

  // highlight matching nav button
  ["clock", "weather", "tasks"].forEach((s, i) => {
    document.querySelectorAll("nav button")[i]
            .classList.toggle("active", s === name);
  });
}

async function setScreen(name) {
  try {
    await fetch("/api/screen", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ screen: name }),
    });
  } catch (err) {
    console.error("[momo] setScreen failed:", err);
  }
  loadState();
}

async function triggerWeather() {
  try {
    await fetch("/api/weather", { method: "POST" });
  } catch (err) {
    console.error("[momo] weather fetch failed:", err);
  }
  loadState();
}

// ── Voice screen ────────────────────────────────────────────────────────────────
const VOICE_FACES = {
  listening: "◑‿◑",
  thinking:  "⊙_⊙",
  speaking:  "◕ω◕",
};

const VOICE_LABELS = {
  listening: "Listening...",
  thinking:  "Thinking...",
  speaking:  "Speaking...",
};

function updateVoiceScreen(vs) {
  if (vs === prevVoiceState) return;   // nothing changed

  document.getElementById("voice-face").innerText   = VOICE_FACES[vs]  || "◕‿◕";
  document.getElementById("voice-status").innerText = VOICE_LABELS[vs] || "";

  const anim = document.getElementById("voice-anim");

  if (vs === "thinking") {
    // replace spans with a CSS spinner
    anim.className = "voice-anim";
    anim.innerHTML = '<div class="spinner"></div>';
  } else {
    // restore 3 spans for bar (speaking) or dot (listening) animation
    anim.innerHTML = "<span></span><span></span><span></span>";
    anim.className = "voice-anim" + (vs === "listening" ? " listening" : "");
  }
}

// ── Task rendering ──────────────────────────────────────────────────────────────
function renderTasks(tasks) {
  const list = document.getElementById("tasks-list");
  list.innerHTML = "";

  if (!tasks || tasks.length === 0) {
    list.innerHTML = '<div class="tasks-empty">No tasks yet — add one below!</div>';
    return;
  }

  tasks.forEach(task => {
    const row = document.createElement("div");
    row.className = "task-item" + (task.done ? " done" : "");

    // checkbox
    const check = document.createElement("span");
    check.className = "task-check";
    check.innerText = task.done ? "✓" : "";
    check.onclick   = () => completeTask(task.id);

    // label
    const label = document.createElement("span");
    label.className = "task-name";
    label.innerText = task.name;
    label.onclick   = () => completeTask(task.id);

    // remove button
    const rm = document.createElement("span");
    rm.className = "task-remove";
    rm.innerText  = "✕";
    rm.onclick    = e => { e.stopPropagation(); removeTask(task.id); };

    row.appendChild(check);
    row.appendChild(label);
    row.appendChild(rm);
    list.appendChild(row);
  });
}

// ── Task actions ────────────────────────────────────────────────────────────────
async function completeTask(id) {
  try {
    await fetch("/api/task/complete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id }),
    });
  } catch (err) {
    console.error("[momo] completeTask failed:", err);
  }
  loadState();
}

async function removeTask(id) {
  try {
    await fetch("/api/task/remove", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id }),
    });
  } catch (err) {
    console.error("[momo] removeTask failed:", err);
  }
  loadState();
}

// ── Add task modal ──────────────────────────────────────────────────────────────
function showAddTask() {
  const input = document.getElementById("task-input");
  input.value = "";
  document.getElementById("modal").classList.remove("hidden");
  // slight delay lets the modal paint before focus
  setTimeout(() => input.focus(), 50);
}

function hideAddTask() {
  document.getElementById("modal").classList.add("hidden");
}

async function addTask() {
  const input = document.getElementById("task-input");
  const name  = input.value.trim();
  if (!name) return;

  try {
    await fetch("/api/task/add", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
  } catch (err) {
    console.error("[momo] addTask failed:", err);
  }

  hideAddTask();
  await setScreen("tasks");
}

// keyboard shortcuts for the modal
document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("task-input").addEventListener("keydown", e => {
    if (e.key === "Enter")  addTask();
    if (e.key === "Escape") hideAddTask();
  });
});

// ── Start polling (1 s interval) ────────────────────────────────────────────────
setInterval(loadState, 1000);
loadState();
