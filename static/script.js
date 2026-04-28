// ── State ──────────────────────────────────────────────────────────────────────
let prevVoiceState = null;

// ── Polling ─────────────────────────────────────────────────────────────────────
async function loadState() {
  try {
    const res   = await fetch("/api/state");
    const state = await res.json();
    renderState(state);
  } catch (err) {
    console.error("[momo] poll failed:", err);
  }
}

// ── Main render ─────────────────────────────────────────────────────────────────
function renderState(state) {
  // clock
  document.getElementById("time").innerText = state.time;
  document.getElementById("date").innerText = state.date;

  // weather
  const temp = parseInt(state.weather.temp, 10);
  document.getElementById("weather-temp").innerText = state.weather.temp + "°F";
  document.getElementById("weather-desc").innerText = state.weather.description;
  document.getElementById("weather-city").innerText = state.weather.city;
  document.getElementById("weather-icon").innerText = weatherEmoji(state.weather.description);

  // temperature colour
  const ws = document.getElementById("weather-screen");
  ws.dataset.feel = temp < 45 ? "cold" : temp < 65 ? "mild" : temp < 82 ? "warm" : "hot";

  // tasks
  renderTasks(state.tasks);

  // always show the base screen
  showScreen(state.screen);

  // voice banner (overlaid — doesn't replace the screen)
  const vs = state.voice_state;
  updateBanner(vs);
  prevVoiceState = vs;
}

// ── Screen switching ────────────────────────────────────────────────────────────
function showScreen(name) {
  document.querySelectorAll(".screen").forEach(s => s.classList.add("hidden"));
  const el = document.getElementById(name + "-screen");
  if (el) el.classList.remove("hidden");

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
  } catch (err) { console.error("[momo] setScreen:", err); }
  loadState();
}

async function triggerWeather() {
  try { await fetch("/api/weather", { method: "POST" }); }
  catch (err) { console.error("[momo] weather:", err); }
  loadState();
}

// ── Voice banner ────────────────────────────────────────────────────────────────
const BANNER_FACES = {
  listening: "◑‿◑",
  thinking:  "⊙_⊙",
  speaking:  "◕ω◕",
};

const BANNER_LABELS = {
  listening: "Listening...",
  thinking:  "Thinking...",
  speaking:  "Speaking...",
};

function updateBanner(vs) {
  const banner = document.getElementById("voice-banner");
  const active = vs === "listening" || vs === "thinking" || vs === "speaking";

  banner.classList.toggle("visible", active);

  if (!active || vs === prevVoiceState) return;

  document.getElementById("banner-face").innerText   = BANNER_FACES[vs]  || "◕‿◕";
  document.getElementById("banner-status").innerText = BANNER_LABELS[vs] || "";

  const anim = document.getElementById("banner-anim");
  if (vs === "thinking") {
    anim.className = "banner-anim";
    anim.innerHTML = '<div class="spinner"></div>';
  } else {
    anim.innerHTML = "<span></span><span></span><span></span>";
    anim.className = "banner-anim" + (vs === "listening" ? " listening" : "");
  }
}

// ── Weather emoji ────────────────────────────────────────────────────────────────
function weatherEmoji(desc) {
  if (!desc) return "🌤️";
  const d = desc.toLowerCase();
  if (d.includes("thunder") || d.includes("storm")) return "⛈️";
  if (d.includes("snow") || d.includes("blizzard"))  return "❄️";
  if (d.includes("rain") || d.includes("drizzle"))   return "🌧️";
  if (d.includes("fog")  || d.includes("mist"))      return "🌫️";
  if (d.includes("overcast") || d.includes("cloudy"))return "☁️";
  if (d.includes("partly"))                          return "⛅";
  if (d.includes("sun") || d.includes("clear"))      return "☀️";
  return "🌤️";
}

// ── Task rendering ──────────────────────────────────────────────────────────────
function renderTasks(tasks) {
  const list = document.getElementById("tasks-list");
  list.innerHTML = "";

  // progress badge
  const done  = tasks.filter(t => t.done).length;
  const prog  = document.getElementById("tasks-progress");
  prog.innerText  = tasks.length ? `${done} / ${tasks.length}` : "";
  prog.style.display = tasks.length ? "" : "none";

  if (!tasks || tasks.length === 0) {
    list.innerHTML = '<div class="tasks-empty">No tasks yet!</div>';
    return;
  }

  tasks.forEach((task, i) => {
    const row = document.createElement("div");
    row.className    = "task-item" + (task.done ? " done" : "");
    row.dataset.color = i % 4;

    const check = document.createElement("span");
    check.className = "task-check";
    check.innerText = task.done ? "✓" : "";
    check.onclick   = () => completeTask(task.id);

    const label = document.createElement("span");
    label.className = "task-name";
    label.innerText = task.name;
    label.onclick   = () => completeTask(task.id);

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
  } catch (err) { console.error("[momo] complete:", err); }
  loadState();
}

async function removeTask(id) {
  try {
    await fetch("/api/task/remove", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id }),
    });
  } catch (err) { console.error("[momo] remove:", err); }
  loadState();
}

// ── Add task modal ──────────────────────────────────────────────────────────────
function showAddTask() {
  document.getElementById("modal").classList.remove("hidden");
  setTimeout(() => document.getElementById("task-input").focus(), 50);
}

function hideAddTask() {
  document.getElementById("task-input").value = "";
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
  } catch (err) { console.error("[momo] add:", err); }
  hideAddTask();
  setScreen("tasks");
}

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("task-input").addEventListener("keydown", e => {
    if (e.key === "Enter")  addTask();
    if (e.key === "Escape") hideAddTask();
  });
});

// ── Poll every second ───────────────────────────────────────────────────────────
setInterval(loadState, 1000);
loadState();
