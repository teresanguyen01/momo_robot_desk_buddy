async function loadState() {
  const res = await fetch("/api/state");
  const state = await res.json();

  document.getElementById("time").innerText = state.time;
  document.getElementById("date").innerText = state.date;

  document.getElementById("weather-temp").innerText = state.weather.temp + "°F";
  document.getElementById("weather-desc").innerText = state.weather.description;
  document.getElementById("weather-city").innerText = state.weather.city;

  const tasksDiv = document.getElementById("tasks");
  tasksDiv.innerHTML = "";

  state.tasks.forEach((task) => {
    const div = document.createElement("div");
    div.className = "task" + (task.done ? " done" : "");
    div.innerText = (task.done ? "✓ " : "☐ ") + task.name;
    tasksDiv.appendChild(div);
  });

  showScreen(state.screen);
}

function showScreen(screen) {
  document
    .querySelectorAll(".screen")
    .forEach((s) => s.classList.add("hidden"));
  document.getElementById(screen + "-screen").classList.remove("hidden");
}

async function setScreen(screen) {
  await fetch("/api/screen", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ screen }),
  });
  loadState();
}

async function getWeather() {
  await fetch("/api/weather", { method: "POST" });
  loadState();
}

setInterval(loadState, 1000);
loadState();
