"""
Momo Desk Buddy — Flask backend
Run: python app.py
Set: export OPENAI_API_KEY=sk-...
"""

import os
import json
import time
import threading
import subprocess
import requests
from flask import Flask, render_template, jsonify, request

# ── Optional imports (degrade gracefully if not installed) ─────────────────────
try:
    import speech_recognition as sr
    SR_AVAILABLE = True
except ImportError:
    SR_AVAILABLE = False
    print("[init] speech_recognition not installed — voice disabled")

try:
    from openai import OpenAI as _OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False
    print("[init] openai not installed — using keyword fallback")

# ── Configuration ──────────────────────────────────────────────────────────────
CITY             = "New Haven"
TASKS_FILE       = os.path.join(os.path.dirname(__file__), "tasks.json")
OPENAI_API_KEY   = os.environ.get("OPENAI_API_KEY", "")
VOICE_ENABLED    = True   # set False to disable voice loop (useful for UI debugging)
MIC_DEVICE_INDEX = None   # set to int (e.g. 1) if wrong mic is selected on Pi

# ── Flask app ──────────────────────────────────────────────────────────────────
app = Flask(__name__)

# ── Shared state + lock ────────────────────────────────────────────────────────
# RULE: every read/write of `state` must happen inside `with state_lock`.
# Blocking calls (network, subprocess, sleep) must happen OUTSIDE the lock.
state_lock = threading.Lock()

state = {
    "screen":      "clock",    # clock | weather | tasks   (shown when voice idle)
    "voice_state": "idle",     # idle | listening | thinking | speaking
    "last_spoken": "",
    "weather": {
        "city":        CITY,
        "temp":        "--",
        "description": "Not loaded",
    },
    "tasks": [],
}

# ── Task persistence ───────────────────────────────────────────────────────────
def load_tasks():
    """Load tasks from disk. Returns defaults if file missing or corrupt."""
    if os.path.exists(TASKS_FILE):
        try:
            with open(TASKS_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"[tasks] Could not load {TASKS_FILE}: {e}")
    return [
        {"id": 1, "name": "Finish homework", "done": False},
        {"id": 2, "name": "Work on Momo",    "done": False},
        {"id": 3, "name": "Check email",     "done": False},
    ]

def save_tasks(tasks):
    """Write tasks to disk. Call while holding state_lock (file I/O is fast)."""
    try:
        with open(TASKS_FILE, "w") as f:
            json.dump(tasks, f, indent=2)
    except IOError as e:
        print(f"[tasks] Save failed: {e}")

def next_task_id(tasks):
    """Return next available task id. Call while holding state_lock."""
    return max((t["id"] for t in tasks), default=0) + 1

# ── Weather ────────────────────────────────────────────────────────────────────
def fetch_weather(city=CITY):
    """Network call — must NOT be called while holding state_lock."""
    try:
        url  = f"https://wttr.in/{city}?format=j1"
        data = requests.get(url, timeout=10).json()
        cur  = data["current_condition"][0]
        return {
            "city":        city,
            "temp":        cur["temp_F"],
            "description": cur["weatherDesc"][0]["value"],
        }
    except Exception as e:
        print(f"[weather] Error: {e}")
        return {"city": city, "temp": "--", "description": "Unavailable"}

# ── Speech output ──────────────────────────────────────────────────────────────
def speak(text):
    """
    Blocking TTS via espeak piped to pw-play.
    Must NOT be called while holding state_lock.
    """
    print(f"[speak] {text}")
    safe = text.replace('"', "'").replace("`", "'").replace("\\", "")
    subprocess.run(
        f'espeak -a 200 -s 160 "{safe}" --stdout | pw-play',
        shell=True
    )

# ── OpenAI command parser ──────────────────────────────────────────────────────
_INTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {
            "type": "string",
            "enum": [
                "show_clock", "get_weather", "show_tasks",
                "add_task", "complete_task", "remove_task", "unknown"
            ],
        },
        "screen": {
            "type": "string",
            "enum": ["clock", "weather", "tasks"],
        },
        "task":             {"type": "string"},
        "spoken_response":  {"type": "string"},
    },
    "required": ["intent", "screen", "task", "spoken_response"],
    "additionalProperties": False,
}

def parse_command(text):
    """
    Ask OpenAI to parse a voice command into structured JSON.
    Returns a dict, or None if unavailable/failed.
    Network call — must NOT be called while holding state_lock.
    """
    if not OPENAI_API_KEY or not OPENAI_AVAILABLE:
        print("[openai] Not configured — using keyword fallback")
        return None
    try:
        client   = _OpenAI(api_key=OPENAI_API_KEY)
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are Momo, a cute Raspberry Pi desk robot assistant. "
                        "Parse the user's voice command into exactly one intent. "
                        "For add_task, put the task name in 'task'. "
                        "For complete_task / remove_task, put the task name in 'task'. "
                        "Set 'screen' to the best screen to show after the action. "
                        "Keep spoken_response short, friendly, and under 15 words."
                    ),
                },
                {"role": "user", "content": text},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name":   "momo_action",
                    "strict": True,
                    "schema": _INTENT_SCHEMA,
                },
            },
        )
        result = json.loads(response.choices[0].message.content)
        print(f"[openai] Parsed: {result}")
        return result
    except Exception as e:
        print(f"[openai] Error: {e}")
        return None

def keyword_fallback(command):
    """Simple keyword matching used when OpenAI is unavailable."""
    cmd = command.lower()
    if "weather" in cmd:
        return {"intent": "get_weather",  "screen": "weather", "task": "",
                "spoken_response": "Here is the weather."}
    if "task" in cmd or "todo" in cmd or "list" in cmd:
        return {"intent": "show_tasks",   "screen": "tasks",   "task": "",
                "spoken_response": "Here are your tasks."}
    if "clock" in cmd or "time" in cmd:
        t = time.strftime("%I:%M %p")
        return {"intent": "show_clock",   "screen": "clock",   "task": "",
                "spoken_response": f"The time is {t}."}
    return     {"intent": "unknown",       "screen": "clock",   "task": "",
                "spoken_response": "Sorry, I did not understand that."}

# ── Intent execution ───────────────────────────────────────────────────────────
def execute_action(action):
    """
    Apply a parsed action to shared state.
    Any network/blocking work is done BEFORE acquiring the lock.
    """
    intent    = action.get("intent",   "unknown")
    screen    = action.get("screen",   "clock")
    task_name = action.get("task",     "").strip()

    # ── pre-fetch (outside lock) ───────────────────────────────────────────────
    new_weather = None
    if intent == "get_weather":
        new_weather = fetch_weather()          # network call before lock

    # ── mutate state (inside lock) ─────────────────────────────────────────────
    with state_lock:
        if intent == "get_weather" and new_weather:
            state["weather"] = new_weather
            state["screen"]  = "weather"

        elif intent == "add_task" and task_name:
            task = {
                "id":   next_task_id(state["tasks"]),
                "name": task_name,
                "done": False,
            }
            state["tasks"].append(task)
            save_tasks(state["tasks"])
            state["screen"] = "tasks"

        elif intent == "complete_task" and task_name:
            for t in state["tasks"]:
                if task_name.lower() in t["name"].lower():
                    t["done"] = True
            save_tasks(state["tasks"])
            state["screen"] = "tasks"

        elif intent == "remove_task" and task_name:
            state["tasks"] = [
                t for t in state["tasks"]
                if task_name.lower() not in t["name"].lower()
            ]
            save_tasks(state["tasks"])
            state["screen"] = "tasks"

        else:
            state["screen"] = screen

# ── Voice loop (background thread) ────────────────────────────────────────────
WAKE_WORD = "momo"

def _listen_once(recognizer, source, timeout, phrase_limit):
    """
    Block until speech is heard or timeout expires.
    Returns transcribed string, or None on silence/error.
    Caller must open the Microphone context.
    """
    try:
        audio = recognizer.listen(source, timeout=timeout,
                                  phrase_time_limit=phrase_limit)
    except sr.WaitTimeoutError:
        return None

    try:
        return recognizer.recognize_google(audio).lower()
    except sr.UnknownValueError:
        return None
    except sr.RequestError as e:
        print(f"[voice] STT network error: {e}")
        return None

def voice_loop():
    """
    Runs forever in a daemon thread.

    Two-phase design:
      Phase 1 — PASSIVE: listen silently for the wake word "momo".
                Screen stays on whatever it currently shows (idle).
      Phase 2 — ACTIVE:  wake word heard → show listening screen →
                listen for command → think → speak → idle.

    Lock discipline:
      - Acquire state_lock only to read/write `state`.
      - Release before every blocking call (network, subprocess, listen).
    """
    recognizer = sr.Recognizer()
    recognizer.dynamic_energy_threshold = True
    print(f"[voice] Voice loop running — wake word: '{WAKE_WORD}'")

    while True:
        # ── Phase 1: PASSIVE wake-word detection ──────────────────────────────
        # voice_state stays "idle"; screen is unchanged.
        try:
            with sr.Microphone(device_index=MIC_DEVICE_INDEX) as source:
                recognizer.adjust_for_ambient_noise(source, duration=0.3)
                heard = _listen_once(recognizer, source,
                                     timeout=5, phrase_limit=4)
        except OSError as e:
            print(f"[voice] Microphone error: {e}")
            time.sleep(5)
            continue

        if not heard or WAKE_WORD not in heard:
            # Nothing heard or wake word absent — stay passive
            continue

        print(f"[voice] Wake word detected in: '{heard}'")

        # Check if the command was bundled with the wake word
        # e.g. "momo what's the weather" → strip wake word and use the rest
        after_wake = heard.split(WAKE_WORD, 1)[-1].strip()

        # ── Phase 2a: LISTENING (wake word heard, wait for command) ───────────
        with state_lock:
            state["voice_state"] = "listening"

        command = None

        if after_wake:
            # Command came in the same utterance as the wake word
            print(f"[voice] Inline command: '{after_wake}'")
            command = after_wake
        else:
            # Wake word only — listen for a follow-up utterance
            speak("Yeah?")   # short acknowledgement, blocking but outside lock
            try:
                with sr.Microphone(device_index=MIC_DEVICE_INDEX) as source:
                    recognizer.adjust_for_ambient_noise(source, duration=0.2)
                    command = _listen_once(recognizer, source,
                                          timeout=5, phrase_limit=7)
            except OSError as e:
                print(f"[voice] Microphone error: {e}")
                with state_lock:
                    state["voice_state"] = "idle"
                continue

            if not command:
                print("[voice] No follow-up command heard")
                with state_lock:
                    state["voice_state"] = "idle"
                continue

            print(f"[voice] Follow-up command: '{command}'")

        # ── Phase 2b: THINKING ────────────────────────────────────────────────
        with state_lock:
            state["voice_state"] = "thinking"

        action = parse_command(command) or keyword_fallback(command)
        execute_action(action)   # may do network + state mutation (outside lock)

        # ── Phase 2c: SPEAKING ────────────────────────────────────────────────
        spoken = action.get("spoken_response", "Okay.")
        with state_lock:
            state["voice_state"] = "speaking"
            state["last_spoken"] = spoken

        speak(spoken)   # blocking subprocess, outside lock

        # ── Back to passive ───────────────────────────────────────────────────
        with state_lock:
            state["voice_state"] = "idle"
        time.sleep(0.5)   # brief pause before passive listen resumes

# ── Flask routes ───────────────────────────────────────────────────────────────
@app.route("/")
def home():
    return render_template("index.html")

@app.route("/api/state")
def api_state():
    with state_lock:
        snapshot = {
            "screen":      state["screen"],
            "voice_state": state["voice_state"],
            "last_spoken": state["last_spoken"],
            "weather":     dict(state["weather"]),
            "tasks":       list(state["tasks"]),
            "time":        time.strftime("%I:%M %p"),
            "date":        time.strftime("%A, %B %d"),
        }
    return jsonify(snapshot)

@app.route("/api/screen", methods=["POST"])
def api_set_screen():
    data   = request.json or {}
    screen = data.get("screen", "clock")
    with state_lock:
        state["screen"] = screen
    return jsonify({"ok": True})

@app.route("/api/weather", methods=["POST"])
def api_weather():
    weather = fetch_weather()              # network call outside lock
    with state_lock:
        state["weather"] = weather
        state["screen"]  = "weather"
    return jsonify(weather)

@app.route("/api/task/add", methods=["POST"])
def api_task_add():
    data = request.json or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"ok": False, "error": "Name required"}), 400
    with state_lock:
        task = {"id": next_task_id(state["tasks"]), "name": name, "done": False}
        state["tasks"].append(task)
        save_tasks(state["tasks"])
    return jsonify({"ok": True, "task": task})

@app.route("/api/task/complete", methods=["POST"])
def api_task_complete():
    data    = request.json or {}
    task_id = data.get("id")
    with state_lock:
        for t in state["tasks"]:
            if t["id"] == task_id:
                t["done"] = True
                save_tasks(state["tasks"])
                return jsonify({"ok": True, "task": t})
    return jsonify({"ok": False, "error": "Task not found"}), 404

@app.route("/api/task/remove", methods=["POST"])
def api_task_remove():
    data    = request.json or {}
    task_id = data.get("id")
    with state_lock:
        state["tasks"] = [t for t in state["tasks"] if t["id"] != task_id]
        save_tasks(state["tasks"])
    return jsonify({"ok": True})

# ── Startup ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    with state_lock:
        state["tasks"] = load_tasks()
    print(f"[init] Loaded {len(state['tasks'])} task(s)")
    print(f"[init] OpenAI: {'configured' if OPENAI_API_KEY else 'NOT SET — using keyword fallback'}")
    print(f"[init] City:   {CITY}")

    if VOICE_ENABLED and SR_AVAILABLE:
        t = threading.Thread(target=voice_loop, daemon=True, name="VoiceLoop")
        t.start()
        print("[init] Voice thread started")
    elif VOICE_ENABLED and not SR_AVAILABLE:
        print("[init] Voice disabled: speech_recognition not installed")
    else:
        print("[init] Voice disabled by config (VOICE_ENABLED=False)")

    print("[init] Starting Flask → http://0.0.0.0:5000")
    # debug=False is required: debug mode's reloader forks the process,
    # which would start the voice thread twice.
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
