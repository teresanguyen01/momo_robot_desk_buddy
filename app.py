"""
Momo Desk Buddy — Flask backend
Run: python app.py
Set: export OPENAI_API_KEY=sk-...
"""

import os
import json
import time
import asyncio
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
MIC_DEVICE_INDEX = 1      # USB PnP Sound Device (hw:1,0)

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
# Voice character — swap to any en-US-*Neural name from:
# https://speech.microsoft.com/portal (free preview in browser)
# Good options: JennyNeural (warm), AriaNeural (natural), SaraNeural (friendly)
VOICE = "en-US-AnaNeural"

async def _edge_tts(text, path):
    """Generate speech MP3 via edge-tts (Microsoft neural TTS, free)."""
    try:
        import edge_tts
        comm = edge_tts.Communicate(text, VOICE)
        await comm.save(path)
        return True
    except Exception as e:
        print(f"[speak] edge-tts error: {e}")
        return False

def speak(text):
    """
    Blocking TTS using edge-tts (neural voice) → mpg123.
    Falls back to pico2wave → pw-play/aplay if edge-tts fails.
    Must NOT be called while holding state_lock.
    """
    print(f"[speak] {text}")
    safe = text.replace('"', "'").replace("`", "'").replace("\\", "")
    mp3  = "/tmp/momo_speech.mp3"
    wav  = "/tmp/momo_speech.wav"

    # Try edge-tts (best quality)
    success = asyncio.run(_edge_tts(safe, mp3))
    if success:
        # mpg123 is the simplest mp3 player on Pi
        if subprocess.run("which mpg123", shell=True, capture_output=True).returncode == 0:
            subprocess.run(f"mpg123 -q {mp3}", shell=True)
            return
        # fallback: pw-play can handle mp3 on most Pi setups
        subprocess.run(f"pw-play {mp3}", shell=True)
        return

    # Fallback: pico2wave → wav
    print("[speak] falling back to pico2wave")
    r = subprocess.run(f'pico2wave -w {wav} "{safe}"', shell=True)
    if r.returncode != 0:
        subprocess.run(f'espeak -a 200 -s 150 "{safe}" -w {wav}', shell=True)

    if subprocess.run("which pw-play", shell=True, capture_output=True).returncode == 0:
        subprocess.run(f"pw-play {wav}", shell=True)
    else:
        subprocess.run(f"aplay {wav}", shell=True)

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
    """Keyword matching used when OpenAI is unavailable or fails."""
    cmd = command.lower()

    # task mutations — check before generic "task" keyword
    if any(w in cmd for w in ("add", "create", "new", "remember")):
        # try to extract what comes after "add"/"create"/etc.
        for kw in ("add", "create", "new task", "remember"):
            if kw in cmd:
                task_name = cmd.split(kw, 1)[-1].strip().strip(".,!?")
                if task_name:
                    return {"intent": "add_task", "screen": "tasks", "task": task_name,
                            "spoken_response": f"Added {task_name} to your tasks."}

    if any(w in cmd for w in ("complete", "done", "finish", "finished", "check off", "mark")):
        for kw in ("complete", "done with", "finished", "finish", "check off", "mark"):
            if kw in cmd:
                task_name = cmd.split(kw, 1)[-1].strip().strip(".,!? as done")
                if task_name:
                    return {"intent": "complete_task", "screen": "tasks", "task": task_name,
                            "spoken_response": f"Marked {task_name} as done."}

    if any(w in cmd for w in ("remove", "delete", "cancel")):
        for kw in ("remove", "delete", "cancel"):
            if kw in cmd:
                task_name = cmd.split(kw, 1)[-1].strip().strip(".,!?")
                if task_name:
                    return {"intent": "remove_task", "screen": "tasks", "task": task_name,
                            "spoken_response": f"Removed {task_name}."}

    if "weather" in cmd or "temperature" in cmd or "forecast" in cmd or "outside" in cmd:
        return {"intent": "get_weather",  "screen": "weather", "task": "",
                "spoken_response": "Here is the weather."}

    if any(w in cmd for w in ("task", "todo", "to do", "list", "to-do")):
        return {"intent": "show_tasks",   "screen": "tasks",   "task": "",
                "spoken_response": "Here are your tasks."}

    if any(w in cmd for w in ("time", "clock", "what time")):
        t = time.strftime("%I:%M %p")
        return {"intent": "show_clock",   "screen": "clock",   "task": "",
                "spoken_response": f"The time is {t}."}

    return {"intent": "unknown", "screen": "clock", "task": "",
            "spoken_response": "Sorry, I did not catch that. Try asking about the weather, time, or tasks."}

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

def _transcribe(recognizer, source, timeout, phrase_limit):
    """
    Listen for one phrase and return the lowercased transcript, or None.
    Caller must already hold the Microphone context.
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
        print(f"[voice] STT error: {e}")
        return None

def voice_loop():
    """
    Runs forever in a daemon thread.

    Siri-style two-phase design
    ───────────────────────────
    Phase 1 — PASSIVE (silent wake-word watch)
      • Listens continuously with no screen changes (voice_state stays "idle").
      • Ignores everything that doesn't contain "momo".

    Phase 2 — ACTIVE (one command cycle, then back to passive)
      "momo [command]" in one breath  → command is the text after "momo"
      "momo" alone                    → play a chime, listen once for command
      After speaking the response     → immediately return to Phase 1

    Lock discipline
    ───────────────
    • state_lock is held only while reading/writing `state`.
    • Every blocking call (listen, speak, network) happens outside the lock.
    """
    recognizer = sr.Recognizer()
    recognizer.dynamic_energy_threshold = True
    print(f"[voice] Ready — say '{WAKE_WORD} <command>' to activate")

    # Startup greeting — small delay lets Flask finish initializing first
    time.sleep(1.5)
    speak(
        "Hi! I'm Momo, your personal desk buddy robot. "
        "I can give you the time, weather, and tasks. "
        "Say momo and then ask me your question!"
    )

    while True:
        # ══ Phase 1: silent passive listening ════════════════════════════════
        # voice_state = "idle" throughout; screen is NOT touched here.
        try:
            with sr.Microphone(device_index=MIC_DEVICE_INDEX) as source:
                recognizer.adjust_for_ambient_noise(source, duration=0.3)
                heard = _transcribe(recognizer, source,
                                    timeout=8, phrase_limit=5)
        except OSError as e:
            print(f"[voice] Microphone error: {e}")
            time.sleep(5)
            continue

        # Ignore silence or anything without the wake word
        if not heard or WAKE_WORD not in heard:
            continue

        print(f"[voice] Wake word detected — heard: '{heard}'")

        # Was a command bundled in the same utterance?
        # "momo show the weather" → command = "show the weather"
        after_wake = heard.split(WAKE_WORD, 1)[-1].strip()

        # ══ Phase 2: active command cycle ════════════════════════════════════

        # 2-a  If only the wake word was said, signal readiness and
        #      listen once for a follow-up phrase.
        if not after_wake:
            speak("Yeah?")    # robot acknowledgement, blocking, outside lock

            with state_lock:
                state["voice_state"] = "listening"

            try:
                with sr.Microphone(device_index=MIC_DEVICE_INDEX) as source:
                    recognizer.adjust_for_ambient_noise(source, duration=0.2)
                    after_wake = _transcribe(recognizer, source,
                                             timeout=5, phrase_limit=7)
            except OSError as e:
                print(f"[voice] Microphone error on follow-up: {e}")
                with state_lock:
                    state["voice_state"] = "idle"
                continue

            if not after_wake:
                print("[voice] No follow-up heard — returning to passive")
                with state_lock:
                    state["voice_state"] = "idle"
                continue

            print(f"[voice] Follow-up command: '{after_wake}'")

        command = after_wake

        # 2-b  THINKING — parse with OpenAI (or keyword fallback)
        with state_lock:
            state["voice_state"] = "thinking"

        action = parse_command(command) or keyword_fallback(command)
        execute_action(action)   # network + state mutation, outside lock

        # 2-c  SPEAKING — say the response aloud
        spoken = action.get("spoken_response", "Okay.")
        with state_lock:
            state["voice_state"] = "speaking"
            state["last_spoken"] = spoken

        speak(spoken)   # blocking espeak | pw-play, outside lock

        # 2-d  Done — return to passive immediately
        with state_lock:
            state["voice_state"] = "idle"
        time.sleep(0.3)

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
