// ── Fit #wrapper to the actual screen size ─────────────────────────────────
// #wrapper is always 480×320 in CSS. This scales it down (or up) to fill
// whatever Chromium reports as the viewport — fixes screen-cutoff on the Pi.
function fitScreen() {
  const el = document.getElementById("wrapper");
  const sx = window.innerWidth  / 480;
  const sy = window.innerHeight / 320;
  const s  = Math.min(sx, sy);
  el.style.transform = `scale(${s})`;
  el.style.left = Math.round((window.innerWidth  - 480 * s) / 2) + "px";
  el.style.top  = Math.round((window.innerHeight - 320 * s) / 2) + "px";
}
window.addEventListener("resize", fitScreen);
fitScreen();

// ── Face expressions ────────────────────────────────────────────────────────
const FACES = {
  idle:     "◕‿◕",
  happy:    "◕ω◕",
  excited:  "★‿★",
  sad:      "◕︵◕",
  sleeping: "－ω－",
  timer:    "◑‿◑",
};

// ── State ───────────────────────────────────────────────────────────────────
let prevVoiceState = null;
let prevFace       = null;

// ── Polling (every 1 s) ─────────────────────────────────────────────────────
async function loadState() {
  try {
    const res   = await fetch("/api/state");
    const state = await res.json();
    renderState(state);
  } catch (err) {
    console.error("[momo] poll failed:", err);
  }
}

// ── Main render ─────────────────────────────────────────────────────────────
function renderState(state) {
  // ── sidebar clock
  document.getElementById("sb-time").innerText = state.time;
  document.getElementById("sb-date").innerText = state.date;

  // ── sidebar weather
  const wIcon = weatherEmoji(state.weather.description);
  document.getElementById("sb-w-icon").innerText = wIcon;
  document.getElementById("sb-w-temp").innerText = state.weather.temp + "°F";
  document.getElementById("sb-w-desc").innerText = state.weather.description;

  // ── sidebar task count
  const done  = state.tasks.filter(t => t.done).length;
  const total = state.tasks.length;
  document.getElementById("sb-tasks").innerText =
    total ? `${done}/${total} done` : "no tasks";

  // ── main detail clock
  document.getElementById("time").innerText = state.time;
  document.getElementById("date").innerText = state.date;

  // ── main detail weather
  document.getElementById("weather-icon").innerText = wIcon;
  document.getElementById("weather-temp").innerText = state.weather.temp + "°F";
  document.getElementById("weather-desc").innerText = state.weather.description;
  document.getElementById("weather-city").innerText = state.weather.city;

  const ws   = document.getElementById("weather-screen");
  const temp = parseInt(state.weather.temp, 10);
  ws.dataset.feel = isNaN(temp) ? "mild"
    : temp < 45 ? "cold" : temp < 65 ? "mild" : temp < 82 ? "warm" : "hot";

  // ── face expressions
  updateFace(state.face || "idle");

  // ── timer
  updateTimer(state.timer);

  // ── tasks
  renderTasks(state.tasks);

  // ── screen + voice banner
  showScreen(state.screen);
  updateBanner(state.voice_state);
  updateMicDot(state.voice_state);

  // ── music banner + stop button
  updateMusicBanner(state.music_song);

  // ── presence dot
  updatePresence(state.presence);

  // ── sleep mode
  updateSleepMode(state.sleep_mode);

  prevVoiceState = state.voice_state;
}

// ── Face update ─────────────────────────────────────────────────────────────
function updateFace(face) {
  if (face === prevFace) return;
  prevFace = face;

  const emoji = FACES[face] || FACES.idle;

  const sbFace   = document.getElementById("sb-face");
  const mainFace = document.getElementById("main-face");

  sbFace.innerText      = emoji;
  sbFace.dataset.face   = face;
  mainFace.innerText    = emoji;
  mainFace.dataset.face = face;

  // Also update the voice banner face
  document.getElementById("banner-face").innerText = emoji;
}

// ── Timer update ─────────────────────────────────────────────────────────────
function updateTimer(timer) {
  const block = document.getElementById("timer-block");
  const quick = document.getElementById("timer-quick");

  if (!timer || !timer.active) {
    block.classList.add("hidden");
    quick.classList.remove("hidden");
    return;
  }

  block.classList.remove("hidden");
  quick.classList.add("hidden");

  const s   = timer.seconds_left;
  const m   = Math.floor(s / 60);
  const sec = s % 60;
  document.getElementById("timer-label").innerText     = timer.label || "Timer";
  document.getElementById("timer-countdown").innerText =
    `${String(m).padStart(2,"0")}:${String(sec).padStart(2,"0")}`;

  const pct = timer.total_seconds > 0
    ? (timer.seconds_left / timer.total_seconds) * 100
    : 0;
  document.getElementById("timer-bar").style.width = pct + "%";
}

// ── Sleep mode ───────────────────────────────────────────────────────────────
function updateSleepMode(sleeping) {
  document.getElementById("sleep-badge").classList.toggle("hidden", !sleeping);
}

// ── Presence dot ─────────────────────────────────────────────────────────────
function updatePresence(present) {
  const dot = document.getElementById("presence-dot");
  dot.className = present ? "present" : "absent";
  dot.title     = present ? "Someone at desk" : "No one detected";
}

// ── Screen switching ────────────────────────────────────────────────────────
function showScreen(name) {
  document.querySelectorAll(".screen").forEach(s => s.classList.add("hidden"));
  const el = document.getElementById(name + "-screen");
  if (el) el.classList.remove("hidden");

  document.querySelectorAll("#sidebar-nav button").forEach(b =>
    b.classList.remove("active"));
  const idx = { clock: 0, weather: 1, tasks: 2 }[name];
  if (idx !== undefined)
    document.querySelectorAll("#sidebar-nav button")[idx].classList.add("active");
}

async function setScreen(name) {
  try {
    await fetch("/api/screen", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ screen: name }),
    });
  } catch (e) { console.error("[momo] setScreen:", e); }
  loadState();
}

async function triggerWeather() {
  try { await fetch("/api/weather", { method: "POST" }); }
  catch (e) { console.error("[momo] weather:", e); }
  loadState();
}

// ── Timer controls ──────────────────────────────────────────────────────────
async function startTimer(minutes, label) {
  try {
    await fetch("/api/timer/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ minutes, label }),
    });
  } catch (e) { console.error("[momo] startTimer:", e); }
  loadState();
}

async function cancelTimer() {
  try { await fetch("/api/timer/cancel", { method: "POST" }); }
  catch (e) { console.error("[momo] cancelTimer:", e); }
  loadState();
}

function updateMusicBanner(song) {
  const banner      = document.getElementById("music-banner");
  const voiceBanner = document.getElementById("voice-banner");
  const btn         = document.getElementById("music-stop-btn");
  const playing     = !!song;
  banner.classList.toggle("visible", playing);
  voiceBanner.classList.toggle("music-up", playing);
  btn.classList.toggle("hidden", !playing);
  if (playing) {
    document.getElementById("music-banner-song").innerText = song;
  }
}

async function stopMusicNow() {
  try { await fetch("/api/music/stop", { method: "POST" }); }
  catch (e) { console.error("[momo] stopMusic:", e); }
  loadState();
}

// ── Voice banner ────────────────────────────────────────────────────────────
const BANNER_LABELS = { listening: "Listening...", thinking: "Thinking...", speaking: "Speaking..." };

function updateBanner(vs) {
  const banner = document.getElementById("voice-banner");
  const active = vs === "listening" || vs === "thinking" || vs === "speaking";
  banner.classList.toggle("visible", active);
  if (!active || vs === prevVoiceState) return;

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

// ── Sidebar mic dot ─────────────────────────────────────────────────────────
function updateMicDot(vs) {
  const dot = document.getElementById("mic-dot");
  dot.className = ["listening","thinking","speaking"].includes(vs) ? vs : "";
}

// ── Weather emoji ───────────────────────────────────────────────────────────
function weatherEmoji(desc) {
  if (!desc) return "🌤️";
  const d = desc.toLowerCase();
  if (d.includes("thunder") || d.includes("storm"))  return "⛈️";
  if (d.includes("snow")    || d.includes("blizzard"))return "❄️";
  if (d.includes("rain")    || d.includes("drizzle")) return "🌧️";
  if (d.includes("fog")     || d.includes("mist"))    return "🌫️";
  if (d.includes("overcast"))                         return "☁️";
  if (d.includes("cloudy"))                           return "🌥️";
  if (d.includes("partly"))                           return "⛅";
  if (d.includes("sun")     || d.includes("clear"))   return "☀️";
  return "🌤️";
}

// ── Task rendering ──────────────────────────────────────────────────────────
function renderTasks(tasks) {
  const list = document.getElementById("tasks-list");
  list.innerHTML = "";

  const done  = tasks.filter(t => t.done).length;
  const prog  = document.getElementById("tasks-progress");
  prog.innerText     = tasks.length ? `${done} / ${tasks.length}` : "";
  prog.style.display = tasks.length ? "" : "none";

  if (!tasks.length) {
    list.innerHTML = '<div class="tasks-empty">No tasks — add one!</div>';
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

    row.append(check, label, rm);
    list.appendChild(row);
  });
}

// ── Task actions ────────────────────────────────────────────────────────────
async function completeTask(id) {
  try {
    await fetch("/api/task/complete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id }),
    });
  } catch (e) { console.error("[momo] complete:", e); }
  loadState();
}

async function removeTask(id) {
  try {
    await fetch("/api/task/remove", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id }),
    });
  } catch (e) { console.error("[momo] remove:", e); }
  loadState();
}

// ── Add task modal ──────────────────────────────────────────────────────────
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
  } catch (e) { console.error("[momo] add:", e); }
  hideAddTask();
  setScreen("tasks");
}

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("task-input").addEventListener("keydown", e => {
    if (e.key === "Enter")  addTask();
    if (e.key === "Escape") hideAddTask();
  });
});

// ── Start ───────────────────────────────────────────────────────────────────
setInterval(loadState, 1000);
loadState();
