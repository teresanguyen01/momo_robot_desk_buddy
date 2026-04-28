from flask import Flask, render_template, jsonify, request
import time
import requests

app = Flask(__name__)

state = {
    "screen": "clock",
    "tasks": [
        {"name": "Finish homework", "done": False},
        {"name": "Work on Momo", "done": False},
        {"name": "Check email", "done": False}
    ],
    "weather": {
        "city": "New Haven",
        "temp": "--",
        "description": "Not loaded"
    }
}

def get_weather(city="New Haven"):
    url = f"https://wttr.in/{city}?format=j1"
    data = requests.get(url, timeout=10).json()
    current = data["current_condition"][0]

    return {
        "city": city,
        "temp": current["temp_F"],
        "description": current["weatherDesc"][0]["value"]
    }

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/api/state")
def api_state():
    state["time"] = time.strftime("%I:%M %p")
    state["date"] = time.strftime("%A, %B %d")
    return jsonify(state)

@app.route("/api/screen", methods=["POST"])
def set_screen():
    data = request.json
    state["screen"] = data.get("screen", "clock")
    return jsonify({"ok": True})

@app.route("/api/weather", methods=["POST"])
def update_weather():
    state["weather"] = get_weather("New Haven")
    state["screen"] = "weather"
    return jsonify(state["weather"])

@app.route("/api/task/complete", methods=["POST"])
def complete_task():
    data = request.json
    task_name = data.get("task", "").lower()

    for task in state["tasks"]:
        if task_name in task["name"].lower():
            task["done"] = True
            state["screen"] = "tasks"
            return jsonify({"ok": True, "task": task})

    return jsonify({"ok": False, "error": "Task not found"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)