"""
Momo Desk Buddy — Flask backend
Run: python app.py
Set: export OPENAI_API_KEY=sk-...
"""
import random
import os
import signal
import atexit
import re
import json
import time
import asyncio
import threading
import subprocess
import queue
import requests
from flask import Flask, render_template, jsonify, request

JOKES = [
"My real-time system missed a deadline… now it’s just real-late.",
"Hard real-time fails once and dies, soft real-time fails and hopes nobody noticed.",
"My ISR is so fast, it interrupts my thoughts.",
"I tried polling once… still waiting for it to be efficient.",
"Interrupts are just hardware saying excuse me.",
"Jitter is just your system having performance anxiety.",
"Concurrency is easy until two things happen at the same time.",
"I had a race condition… now I have two bugs.",
"Threads are like roommates, they fight over shared resources.",
"Mutexes are just polite arguments: after you.",
"Deadlock is when everyone is waiting and nobody moves.",
"Context switching is multitasking for CPUs.",
"Shared variables are where friendships go to die.",
"A lock without discipline is just a suggestion.",
"Critical section: enter carefully, exit quickly.",
"Mutex means my turn.",
"Spinlock is are we there yet for CPUs.",
"The scheduler is the real boss of your program.",
"My scheduler has favorites, it’s called priority.",
"Preemptive scheduling says you’re done now.",
"Ready queue is where dreams wait.",
"Flip-flops don’t flop, they just flip twice.",
"D flip-flop is memory with a clock.",
"Truth tables never lie, unlike your code.",
"Setup time: don’t be late or else.",
"Ethernet is WiFi with a leash.",
"A switch doesn’t switch, it directs traffic.",
"Routers are internet GPS devices.",
"Packets are tiny envelopes of chaos.",
"Ping is just are you alive.",
"Bluetooth pairing is awkward introductions.",
"Connected doesn’t mean working.",
"Bluetooth replaces cables with confusion.",
"ADC turns reality into numbers.",
"DAC turns numbers into reality.",
"Nyquist says sample twice or regret it.",
"Capacitors are tiny batteries with trust issues.",
"Noise is system gossip.",
"Debugging is reverse engineering your mistakes.",
"If all else fails, add delay.",
"You are the Joke."
]

# ── Optional imports ───────────────────────────────────────────────────────────
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

try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False
    print("[init] opencv not installed — camera presence disabled")

try:
    import serial as pyserial
    SERIAL_AVAILABLE = True
except ImportError:
    SERIAL_AVAILABLE = False
    print("[init] pyserial not installed — servo disabled (pip install pyserial)")

# ── Configuration ──────────────────────────────────────────────────────────────
CITY             = "New Haven"
TASKS_FILE       = os.path.join(os.path.dirname(__file__), "tasks.json")
OPENAI_API_KEY   = os.environ.get("OPENAI_API_KEY", "")
VOICE_ENABLED    = True
MIC_DEVICE_INDEX = None   # kept for reference; audio capture uses ALSA_MIC_DEVICE directly
ALSA_MIC_DEVICE  = "pulse"   # PipeWire-pulse (owns all hardware devices on this Pi)
MIC_BOOST_PERCENT = 150      # system mic gain % (100 = default, 150–200 = louder pickup)
CAMERA_ENABLED   = True
CAMERA_INDEX     = 0      # Brio 100 webcam (OpenCV index) — /dev/video1

# ── Servo / Arduino config ─────────────────────────────────────────────────────
SERVO_ENABLED    = True
ARDUINO_PORT     = "/dev/ttyACM1"
ARDUINO_BAUD     = 9600
SERVO_REVERSE    = False  # set True if servo moves opposite to face direction
SERVO_START_ANGLE = 90   # degrees — center position sent on startup
SERVO_MIN_ANGLE  = 0
SERVO_MAX_ANGLE  = 180
SERVO_DEAD_ZONE  = 25    # px — ignore face offset smaller than this (prevents jitter)
SERVO_STEP_SIZE  = 4     # degrees to move per camera frame (higher = faster tracking)

# ── Flask ──────────────────────────────────────────────────────────────────────
app = Flask(__name__)
print("Mic device index is", MIC_DEVICE_INDEX)

# ── Shared state + lock ────────────────────────────────────────────────────────
# RULE: all reads/writes to `state` must be inside `with state_lock`.
# Blocking calls (network, subprocess, sleep, listen) must be OUTSIDE the lock.
state_lock = threading.Lock()

state = {
    "screen":      "clock",
    "voice_state": "idle",      # idle | listening | thinking | speaking
    "face":        "idle",      # idle | happy | excited | sad | sleeping | timer
    "last_spoken": "",
    "weather": {"city": CITY, "temp": "--", "description": "Not loaded"},
    "tasks":    [],
    "timer": {
        "active":        False,
        "seconds_left":  0,
        "total_seconds": 0,
        "label":         "Timer",
    },
    "reminders": [],    # [{"id": int, "text": str, "fire_at": float, "fired": bool}]
    "presence":  True,  # True = someone detected at desk
    "sleep_mode": False, # True = Momo is asleep, only wakes on wake command
    "music_song": "",    # non-empty string while music is playing
    "servo": {
        "enabled":   SERVO_ENABLED,
        "connected": False,
        "angle":     SERVO_START_ANGLE,
    },
}

# ── Task persistence ───────────────────────────────────────────────────────────
def load_tasks():
    if os.path.exists(TASKS_FILE):
        try:
            with open(TASKS_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"[tasks] Could not load: {e}")
    return [
        {"id": 1, "name": "Finish homework", "done": False},
        {"id": 2, "name": "Work on Momo",    "done": False},
        {"id": 3, "name": "Check email",     "done": False},
    ]

def save_tasks(tasks):
    try:
        with open(TASKS_FILE, "w") as f:
            json.dump(tasks, f, indent=2)
    except IOError as e:
        print(f"[tasks] Save failed: {e}")

def next_task_id(tasks):
    return max((t["id"] for t in tasks), default=0) + 1

# ── Weather ────────────────────────────────────────────────────────────────────
def fetch_weather(city=CITY):
    """Network call — must NOT be called while holding state_lock."""
    try:
        data = requests.get(f"https://wttr.in/{city}?format=j1", timeout=10).json()
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
VOICE = "en-US-AvaNeural"   # VOICE CHANGES HERE!

async def _edge_tts(text, path):
    try:
        import edge_tts
        await edge_tts.Communicate(text, VOICE).save(path)
        return True
    except Exception as e:
        print(f"[speak] edge-tts error: {e}")
        return False

def speak(text):
    """Blocking TTS. Must NOT be called while holding state_lock."""
    print(f"[speak] {text}")
    safe = text.replace('"', "'").replace("`", "'").replace("\\", "")
    mp3  = "/tmp/momo_speech.mp3"
    wav  = "/tmp/momo_speech.wav"

    if asyncio.run(_edge_tts(safe, mp3)):
        if subprocess.run("which mpg123", shell=True, capture_output=True).returncode == 0:
            subprocess.run(f"mpg123 -q -o pulse {mp3}", shell=True)
        else:
            subprocess.run(f"aplay -D pulse {mp3}", shell=True)
        return

    print("[speak] falling back to pico2wave")
    if subprocess.run(f'pico2wave -w {wav} "{safe}"', shell=True).returncode != 0:
        subprocess.run(f'espeak -a 200 -s 150 "{safe}" -w {wav}', shell=True)

    subprocess.run(f"aplay -D pulse {wav}", shell=True)

# ── Music playback ────────────────────────────────────────────────────────────
_music_process = None   # holds the running yt-dlp | mpg123 subprocess

def play_music(song_name):
    """Search YouTube for song_name, convert with ffmpeg, play via aplay."""
    global _music_process
    stop_music()
    print(f"[music] Searching for: {song_name}")

    # Resolve full paths so Flask's restricted PATH doesn't break things
    ytdlp  = subprocess.run(["which", "yt-dlp"],  capture_output=True, text=True).stdout.strip()
    ffmpeg = subprocess.run(["which", "ffmpeg"],  capture_output=True, text=True).stdout.strip()
    aplay  = subprocess.run(["which", "aplay"],   capture_output=True, text=True).stdout.strip()

    print(f"[music] yt-dlp={ytdlp!r}  ffmpeg={ffmpeg!r}  aplay={aplay!r}")

    if not ytdlp:
        print("[music] ERROR: yt-dlp not found — install with: pip install yt-dlp")
        return
    if not ffmpeg:
        print("[music] ERROR: ffmpeg not found — install with: sudo apt install ffmpeg -y")
        return
    if not aplay:
        print("[music] ERROR: aplay not found")
        return

    # Use paplay (PulseAudio/PipeWire native) to avoid ALSA dmix conflicts
    paplay = subprocess.run(["which", "paplay"], capture_output=True, text=True).stdout.strip()
    player = paplay if paplay else f"{aplay} -D pulse"
    player_args = "--raw --channels=2 --rate=44100 --format=s16le" if paplay else ""
    cmd = (
        f'{ytdlp} -f bestaudio --no-playlist '
        f'"ytsearch1:{song_name}" -o - 2>/tmp/momo_ytdlp.log | '
        f'{ffmpeg} -hide_banner -loglevel error -i pipe:0 -f s16le -ar 44100 -ac 2 pipe:1 2>/tmp/momo_ffmpeg.log | '
        f'{player} {player_args}'
    )
    # Use a new process group so stop_music() can kill the whole pipeline (shell + children)
    _music_process = subprocess.Popen(cmd, shell=True, preexec_fn=os.setsid)
    with state_lock:
        state["music_song"] = song_name
    print(f"[music] Playing: {song_name} (logs: /tmp/momo_ytdlp.log, /tmp/momo_ffmpeg.log)")

    # Monitor thread: clear music_song when the process finishes on its own
    proc = _music_process
    def _watch():
        proc.wait()
        with state_lock:
            if state["music_song"] == song_name:
                state["music_song"] = ""
        print(f"[music] Process ended for: {song_name}")
    threading.Thread(target=_watch, daemon=True).start()

def stop_music():
    """Stop any currently playing music (kills entire pipeline process group)."""
    global _music_process
    if _music_process and _music_process.poll() is None:
        try:
            os.killpg(os.getpgid(_music_process.pid), signal.SIGTERM)
        except Exception:
            _music_process.terminate()
        try:
            _music_process.wait(timeout=2)
        except Exception:
            pass
        print("[music] Stopped")
    _music_process = None
    with state_lock:
        state["music_song"] = ""

def is_music_playing():
    return _music_process is not None and _music_process.poll() is None

# ── Spoken response helpers ────────────────────────────────────────────────────
def _time_response():
    hour, minute, ampm = int(time.strftime("%I")), time.strftime("%M"), time.strftime("%p").lower()
    if minute == "00":
        return f"It's {hour} o'clock {ampm}."
    elif minute.startswith("0"):
        return f"It's {hour} oh {minute[1]} {ampm}."
    return f"It's {hour} {minute} {ampm}."

def _weather_response(weather):
    try:
        temp = int(weather["temp"])
    except (ValueError, KeyError, TypeError):
        return "I couldn't get the weather right now. Try again in a moment!"

    desc, city, d = weather.get("description",""), weather.get("city",""), weather.get("description","").lower()

    if   temp <= 32: rec = "It's freezing! Bundle up with a heavy coat, gloves, and a hat."
    elif temp <= 45: rec = "Pretty cold out there. I'd recommend a warm jacket."
    elif temp <= 55: rec = "It's chilly. A light jacket or sweater should do the trick."
    elif temp <= 65: rec = "Mild today! A light layer or long sleeves works well."
    elif temp <= 75: rec = "Comfortable outside! A t-shirt is totally fine."
    elif temp <= 85: rec = "It's warm! Go with something light."
    else:            rec = "It's really hot! Stay hydrated and wear something breathable."

    if   any(w in d for w in ["rain","drizzle","shower"]): rec += " Oh, and grab your umbrella!"
    elif any(w in d for w in ["snow","blizzard","sleet"]): rec += " Watch out for slippery conditions."
    elif any(w in d for w in ["sunny","clear"]):           rec += " Sunglasses would be a great call!"
    elif "wind" in d:                                       rec += " It's also quite windy, so an extra layer helps."

    return f"The weather in {city} is {temp} degrees and {desc}. {rec}"

def _startup_greeting():
    hour = int(time.strftime("%H"))
    if hour < 12:
        return ("Good morning! I'm Momo, your personal desk buddy! "
                "Let's make today super productive. Say momo to get started!")
    elif hour < 17:
        return ("Hey there! I'm Momo. Hope your afternoon is going great! "
                "Just say momo whenever you need me.")
    elif hour < 21:
        return ("Good evening! I'm Momo. Still at it? I love the dedication! "
                "Say momo if you need anything.")
    return ("Hey night owl! I'm Momo. Burning the midnight oil, huh? "
            "I'm here for you. Say momo anytime!")

def _presence_greeting():
    hour = int(time.strftime("%H"))
    greetings = {
        range(0,12):  "Good morning! Ready to crush it today?",
        range(12,17): "Hey, welcome back! Miss me?",
        range(17,21): "Hey there! How's your evening going?",
        range(21,24): "Still here? You're dedicated! Need anything?",
    }
    for r, g in greetings.items():
        if hour in r:
            return g
    return "Hey! Welcome back!"

def _daily_briefing(weather):
    """Compose time + weather + tasks into one spoken briefing."""
    with state_lock:
        tasks = list(state["tasks"])

    pending = [t for t in tasks if not t["done"]]
    parts   = [_time_response(), _weather_response(weather)]

    if not pending:
        parts.append("You have no pending tasks — you're all caught up! Amazing!")
    elif len(pending) == 1:
        parts.append(f"You have one task: {pending[0]['name']}. You've got this!")
    elif len(pending) <= 3:
        names = ", ".join(t["name"] for t in pending)
        parts.append(f"You have {len(pending)} tasks today: {names}.")
    else:
        names = ", ".join(t["name"] for t in pending[:3])
        parts.append(f"You have {len(pending)} tasks. Starting with: {names}.")

    return " ".join(parts)

def _parse_duration_minutes(text):
    """Extract a duration in minutes from text like 'in 30 minutes' or '1 hour'."""
    t = text.lower()
    m = re.search(r'(\d+)\s*(?:min(?:ute)?s?)', t)
    if m: return int(m.group(1))
    m = re.search(r'(\d+)\s*(?:hour|hr)s?', t)
    if m: return int(m.group(1)) * 60
    if "half" in t and "hour" in t: return 30
    return None

# ── OpenAI command parser ──────────────────────────────────────────────────────
_INTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {
            "type": "string",
            "enum": [
                "show_clock", "get_weather", "show_tasks",
                "add_task", "complete_task", "remove_task",
                "start_timer", "cancel_timer",
                "set_reminder",
                "tell_joke",
                "daily_briefing",
                "rotate_servo",
                "play_music",
                "stop_music",
                "clear_all_tasks",
                "unknown",
            ],
        },
        "screen":          {"type": "string", "enum": ["clock","weather","tasks"]},
        "task":            {"type": "string"},
        "spoken_response": {"type": "string"},
    },
    "required": ["intent","screen","task","spoken_response"],
    "additionalProperties": False,
}

_SYSTEM_PROMPT = """You are Momo, a cheerful and slightly nerdy desk robot with a warm, encouraging personality.
You love helping the user stay productive and always root for them. Use light humor when appropriate.
Occasionally say things like "On it!", "You've got this!", or "Beep boop, processing!".

Parse the user's command into EXACTLY ONE intent:
- show_clock / get_weather / show_tasks: navigation
- add_task: put task name in 'task'
- complete_task / remove_task: put task name in 'task'
- start_timer: put duration in MINUTES as a plain number string in 'task' (e.g. "25" for 25 min, "5" for a 5 min break). Default to "25" for pomodoro.
- set_reminder: put the FULL reminder phrase in 'task' (e.g. "drink water in 30 minutes")
- tell_joke: put a short, clean, funny joke in 'spoken_response'
- daily_briefing: user wants a morning/daily summary
- rotate_servo: user wants to rotate/turn the camera. Put the target angle (0-180) OR a relative instruction like "left 30" or "right 45" as a plain string in 'task'. Examples: "90" for center, "left 30", "right 45". If they say "center" or "straight" use "90".
- play_music: user wants to play a song. Put the song/artist name in 'task'.
- stop_music: user wants to stop the music.
- clear_all_tasks: user wants to delete/clear ALL tasks at once (e.g. "delete all tasks", "clear my list", "wipe everything"). Requires confirmation — just classify the intent, do not act yet.
- unknown: anything else

Keep spoken_response warm and encouraging (under 20 words), except for tell_joke where the joke itself goes in spoken_response."""

def parse_command(text):
    """Returns a dict or None. Must NOT be called while holding state_lock."""
    if not OPENAI_API_KEY or not OPENAI_AVAILABLE:
        return None
    try:
        client   = _OpenAI(api_key=OPENAI_API_KEY)
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.3,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user",   "content": text},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "momo_action", "strict": True, "schema": _INTENT_SCHEMA},
            },
        )
        result = json.loads(response.choices[0].message.content)
        print(f"[openai] → {result}")
        return result
    except Exception as e:
        print(f"[openai] Error: {e}")
        return None

def keyword_fallback(command):
    """Used when OpenAI is unavailable or fails."""
    cmd = command.lower()

    # Timer
    if any(w in cmd for w in ("timer","pomodoro","focus","concentrate")):
        mins = _parse_duration_minutes(cmd) or 25
        return {"intent":"start_timer","screen":"clock","task":str(mins),"spoken_response":""}
    if "cancel" in cmd and "timer" in cmd:
        return {"intent":"cancel_timer","screen":"clock","task":"","spoken_response":""}

    # Reminder
    if "remind" in cmd or "reminder" in cmd:
        after = re.split(r'remind(?:er)?\s*(?:me\s*(?:to)?)?', cmd, maxsplit=1)[-1].strip()
        return {"intent":"set_reminder","screen":"clock","task":after,"spoken_response":""}

    # Daily briefing
    if any(w in cmd for w in ("briefing","good morning","what's today","today's summary")):
        return {"intent":"daily_briefing","screen":"clock","task":"","spoken_response":""}

    # Joke
    if any(w in cmd for w in ("joke", "funny", "laugh", "fun fact")):
        return {
            "intent": "tell_joke",
            "screen": "clock",
            "task": "",
            "spoken_response": random.choice(JOKES)
        }

    # Task mutations
    if any(w in cmd for w in ("add","create","new","remember")):
        for kw in ("add","create","new task","remember"):
            if kw in cmd:
                name = cmd.split(kw,1)[-1].strip().strip(".,!?")
                if name:
                    return {"intent":"add_task","screen":"tasks","task":name,"spoken_response":""}
        return {"intent":"add_task","screen":"tasks","task":"","spoken_response":""}

    if any(w in cmd for w in ("complete","done","finish","check off","mark")):
        for kw in ("complete","done with","finished","finish","check off","mark"):
            if kw in cmd:
                name = cmd.split(kw,1)[-1].strip().strip(".,!? as done")
                if name:
                    return {"intent":"complete_task","screen":"tasks","task":name,"spoken_response":""}
        return {"intent":"complete_task","screen":"tasks","task":"","spoken_response":""}

    if any(w in cmd for w in ("remove","delete","clear","wipe","erase")):
        # "all" signals clear-all before checking single-task remove
        if any(w in cmd for w in ("all", "everything", "every task", "entire list", "whole list")):
            return {"intent":"clear_all_tasks","screen":"tasks","task":"","spoken_response":""}
        for kw in ("remove","delete"):
            if kw in cmd:
                name = cmd.split(kw,1)[-1].strip().strip(".,!?")
                if name:
                    return {"intent":"remove_task","screen":"tasks","task":name,"spoken_response":""}
        return {"intent":"remove_task","screen":"tasks","task":"","spoken_response":""}

    # Music — stop check first so "stop playing X" doesn't match play
    if any(w in cmd for w in ("stop music","stop playing","stop the music","pause","mute","quiet","silence","no more music")):
        return {"intent":"stop_music","screen":"clock","task":"","spoken_response":""}

    # Music — play (broad match so song names like "Stateside" aren't lost)
    _play_triggers = ("play","put on","start playing","listen to","i want to hear",
                      "can you play","could you play","please play","queue")
    if any(w in cmd for w in _play_triggers):
        for kw in ("please play","can you play","could you play","i want to hear",
                   "listen to","start playing","put on","play some","play"):
            if kw in cmd:
                song = cmd.split(kw, 1)[-1].strip().strip(".,!?")
                if song:
                    print(f"[music] Extracted song name: '{song}'")
                    return {"intent":"play_music","screen":"clock","task":song,"spoken_response":""}
        # Trigger word found but no song name after it — ask
        return {"intent":"play_music","screen":"clock","task":"","spoken_response":""}

    # Servo rotation
    if any(w in cmd for w in ("rotate","turn","spin","look","face","swing")):
        if any(w in cmd for w in ("left","counter")):
            degs = re.search(r'(\d+)', cmd)
            task = f"left {degs.group(1)}" if degs else "left 30"
        elif any(w in cmd for w in ("right","clockwise")):
            degs = re.search(r'(\d+)', cmd)
            task = f"right {degs.group(1)}" if degs else "right 30"
        elif any(w in cmd for w in ("center","centre","straight","forward","middle")):
            task = "90"
        else:
            degs = re.search(r'(\d+)', cmd)
            task = degs.group(1) if degs else "90"
        return {"intent":"rotate_servo","screen":"clock","task":task,"spoken_response":""}

    # Info
    if any(w in cmd for w in ("weather","temperature","forecast","outside","wear")):
        return {"intent":"get_weather","screen":"weather","task":"","spoken_response":""}
    if any(w in cmd for w in ("task","todo","list")):
        return {"intent":"show_tasks","screen":"tasks","task":"","spoken_response":"Here are your tasks!"}
    if any(w in cmd for w in ("time","clock","what time")):
        return {"intent":"show_clock","screen":"clock","task":"","spoken_response":_time_response()}

    return {"intent":"unknown","screen":"clock","task":"",
            "spoken_response":"I didn't get that."}

# ── Intent execution ───────────────────────────────────────────────────────────
def execute_action(action):
    """
    Apply action to state. Fills in action["spoken_response"] when needed.
    Network/blocking calls happen BEFORE acquiring the lock.
    """
    intent    = action.get("intent",  "unknown")
    screen    = action.get("screen",  "clock")
    task_name = action.get("task",    "").strip()

    # ── pre-fetch outside the lock ─────────────────────────────────────────────
    new_weather = None
    if intent in ("get_weather", "daily_briefing"):
        new_weather = fetch_weather()

    briefing_text = None
    if intent == "daily_briefing" and new_weather:
        briefing_text = _daily_briefing(new_weather)

    # ── mutate state inside the lock ──────────────────────────────────────────
    with state_lock:

        if intent == "get_weather" and new_weather:
            state["weather"] = new_weather
            state["screen"]  = "weather"
            action["spoken_response"] = _weather_response(new_weather)

        elif intent == "show_clock":
            state["screen"] = "clock"
            action["spoken_response"] = _time_response()

        elif intent == "daily_briefing":
            if new_weather:
                state["weather"] = new_weather
            state["screen"] = "clock"
            action["spoken_response"] = briefing_text or "Here's your daily briefing!"
            state["face"] = "happy"

        elif intent == "tell_joke":
            state["screen"] = "clock"
            state["face"]   = "happy"

            if not action.get("spoken_response"):
                action["spoken_response"] = random.choice(JOKES)

        elif intent == "start_timer":
            try:
                minutes = int(task_name)
            except (ValueError, TypeError):
                minutes = _parse_duration_minutes(task_name) or 25

            label = "Pomodoro" if minutes == 25 else "Break" if minutes <= 10 else "Focus Timer"
            total = minutes * 60
            state["timer"] = {
                "active":        True,
                "seconds_left":  total,
                "total_seconds": total,
                "label":         label,
            }
            state["screen"] = "clock"
            state["face"]   = "timer"
            action["spoken_response"] = (
                f"Starting a {minutes} minute {label}. You've got this!"
            )

        elif intent == "cancel_timer":
            state["timer"]["active"] = False
            state["screen"] = "clock"
            state["face"]   = "idle"
            action["spoken_response"] = "Timer cancelled! Take it easy."

        elif intent == "rotate_servo":
            t = task_name.lower().strip()
            # Parse "left N", "right N", plain "N", or "center"
            left_m  = re.match(r'left\s+(\d+)', t)
            right_m = re.match(r'right\s+(\d+)', t)
            if left_m:
                new_angle = max(SERVO_MIN_ANGLE, _servo_angle - int(left_m.group(1)))
                direction_word = f"left {left_m.group(1)} degrees"
            elif right_m:
                new_angle = min(SERVO_MAX_ANGLE, _servo_angle + int(right_m.group(1)))
                direction_word = f"right {right_m.group(1)} degrees"
            else:
                # Treat as absolute angle
                try:
                    new_angle = int(re.search(r'\d+', t).group())
                except (AttributeError, ValueError):
                    new_angle = SERVO_START_ANGLE
                direction_word = f"to {new_angle} degrees"
            state["screen"] = "clock"
            action["spoken_response"] = f"Rotating {direction_word}!"
            # move_servo is called outside the lock below
            action["_servo_angle"] = new_angle

        elif intent == "play_music":
            state["screen"] = "clock"
            state["face"]   = "happy"
            if task_name:
                action["spoken_response"] = f"On it! Playing {task_name}."
            else:
                action["spoken_response"] = "What song should I play?"
            # actual play_music() call happens outside the lock below
            action["_play_song"] = task_name

        elif intent == "stop_music":
            state["screen"] = "clock"
            state["face"]   = "idle"
            action["spoken_response"] = "Stopping the music!"
            # actual stop_music() call happens outside the lock below
            action["_stop_music"] = True

        elif intent == "set_reminder":
            minutes = _parse_duration_minutes(task_name) or 30
            # Strip the "in X minutes" part to get just the reminder text
            clean = re.sub(r'\s*in\s+\d+\s*(?:min(?:ute)?s?|hours?|hrs?)\s*', '', task_name).strip()
            text  = clean or task_name
            state["reminders"].append({
                "id":      int(time.time()),
                "text":    text,
                "fire_at": time.time() + minutes * 60,
                "fired":   False,
            })
            action["spoken_response"] = (
                f"Got it! I'll remind you to {text} in {minutes} minutes."
            )

        elif intent == "add_task" and task_name:
            task = {"id": next_task_id(state["tasks"]), "name": task_name, "done": False}
            state["tasks"].append(task)
            save_tasks(state["tasks"])
            state["screen"] = "tasks"
            state["face"]   = "happy"
            if not action.get("spoken_response"):
                action["spoken_response"] = f"Got it! Added '{task_name}' to your tasks."

        elif intent == "complete_task" and task_name:
            matched = None
            for t in state["tasks"]:
                if task_name.lower() in t["name"].lower():
                    t["done"]  = True
                    matched    = t["name"]
            save_tasks(state["tasks"])
            state["screen"] = "tasks"
            state["face"]   = "excited"
            action["spoken_response"] = (
                f"Awesome! I've checked off '{matched}'. Keep it up!" if matched
                else f"Hmm, I couldn't find a task matching '{task_name}'."
            )

        elif intent == "remove_task" and task_name:
            state["tasks"] = [t for t in state["tasks"]
                               if task_name.lower() not in t["name"].lower()]
            save_tasks(state["tasks"])
            state["screen"] = "tasks"
            if not action.get("spoken_response"):
                action["spoken_response"] = f"Done! Removed '{task_name}'."

        elif intent == "clear_all_tasks":
            state["screen"] = "tasks"
            # Deletion happens only after confirmation in handle_multi_turn

        elif intent == "show_tasks":
            state["screen"] = "tasks"

        else:
            state["screen"] = screen

# ── Multi-turn conversation handler ───────────────────────────────────────────
def handle_multi_turn(recognizer, action):
    """
    Handles follow-up questions for intents that need more info.
    Returns the final spoken response string.
    """
    intent = action.get("intent","unknown")
    task   = action.get("task","").strip()

    def ask_and_listen(question, duration=5):
        """Speak a question, show listening banner, return transcript or None."""
        with state_lock:
            state["voice_state"] = "speaking"
        speak(question)
        with state_lock:
            state["voice_state"] = "listening"
        return _record_and_transcribe(recognizer, duration=duration)

    if intent == "add_task" and not task:
        answer = ask_and_listen("Sure! What task would you like to add?")
        if answer:
            action["task"] = answer
            execute_action(action)
            return action.get("spoken_response", f"Added '{answer}' to your tasks!")
        return "I didn't catch that. Try: momo, add a task."

    if intent == "complete_task" and not task:
        with state_lock:
            pending = [t["name"] for t in state["tasks"] if not t["done"]]
        if not pending:
            return "You have no pending tasks to check off — great job!"
        answer = ask_and_listen("Which task would you like to check off?")
        if answer:
            action["task"] = answer
            execute_action(action)
            return action.get("spoken_response", "Checked it off!")
        return "I didn't catch that. Try: momo, check off a task."

    if intent == "remove_task" and not task:
        answer = ask_and_listen("Which task would you like to remove?")
        if answer:
            action["task"] = answer
            execute_action(action)
            return action.get("spoken_response", f"Removed '{answer}'.")
        return "I didn't catch that. Try: momo, remove a task."

    if intent == "clear_all_tasks":
        answer = ask_and_listen("Are you sure you want to delete all your tasks? Say yes to confirm.")
        if answer and any(w in answer for w in ("yes", "yeah", "yep", "sure", "confirm", "do it", "delete them", "go ahead")):
            with state_lock:
                count = len(state["tasks"])
                state["tasks"] = []
                state["face"]  = "happy"
            save_tasks([])
            return f"Done! I deleted all {count} task{'s' if count != 1 else ''}. Your list is squeaky clean!"
        return "No worries, your tasks are safe! I'll keep them right here."

    return action.get("spoken_response", "Okay!")

# ── Background: timer tick ─────────────────────────────────────────────────────
def timer_tick():
    """Decrements the timer every second. Fires a spoken notification when done."""
    while True:
        time.sleep(1)
        label = None
        with state_lock:
            if not state["timer"]["active"]:
                continue
            state["timer"]["seconds_left"] -= 1
            if state["timer"]["seconds_left"] > 0:
                continue
            state["timer"]["active"] = False
            label = state["timer"]["label"]
            state["face"] = "excited"

        print(f"[timer] '{label}' finished!")
        speak(f"Time's up! Your {label} is complete. Amazing work, you crushed it!")
        time.sleep(2)
        with state_lock:
            if state["face"] == "excited":
                state["face"] = "idle"

# ── Background: reminder checker ──────────────────────────────────────────────
def reminder_checker():
    """Checks for due reminders every 20 seconds."""
    while True:
        time.sleep(20)
        now, to_fire = time.time(), []
        with state_lock:
            for r in state["reminders"]:
                if not r["fired"] and r["fire_at"] <= now:
                    r["fired"] = True
                    to_fire.append(r["text"])
        for text in to_fire:
            print(f"[reminder] Firing: {text}")
            speak(f"Hey! Just a reminder: {text}. Hope you're staying on top of things!")

# ── Arduino servo control ──────────────────────────────────────────────────────
# The Arduino sketch reads a plain integer (0–180) followed by '\n' on Serial
# and moves the servo to that angle. We just write that over pyserial.

_arduino = None          # holds the open serial.Serial object
_servo_angle = SERVO_START_ANGLE   # tracks the last angle we sent
_serial_lock = threading.Lock()    # guards concurrent serial writes

def init_servo():
    """Open the serial connection to the Arduino and send the start angle.
    Sets state["servo"]["connected"] = True on success, False on failure.
    Safe to call even if Arduino is unplugged — will just print a warning."""
    global _arduino, _servo_angle

    if not SERVO_ENABLED or not SERIAL_AVAILABLE:
        return

    try:
        _arduino = pyserial.Serial(ARDUINO_PORT, ARDUINO_BAUD, timeout=1)
        time.sleep(2)   # wait for Arduino to reset after serial open
        _servo_angle = SERVO_START_ANGLE
        _arduino.write(f"{SERVO_START_ANGLE}\n".encode())
        print(f"[servo] Arduino servo controller connected on {ARDUINO_PORT}")
        print(f"[servo] Sent start angle: {SERVO_START_ANGLE}")
        with state_lock:
            state["servo"]["connected"] = True
            state["servo"]["angle"]     = SERVO_START_ANGLE
    except Exception as e:
        _arduino = None
        print(f"[servo] Servo disabled: could not open serial port — {e}")
        with state_lock:
            state["servo"]["connected"] = False

def move_servo(angle):
    """Send a new angle to the Arduino servo (0–180).
    Clamps to min/max, skips tiny changes under 2°, catches serial errors."""
    global _arduino, _servo_angle

    if not SERVO_ENABLED:
        return

    if _arduino is None:
        try:
            _arduino = pyserial.Serial(ARDUINO_PORT, ARDUINO_BAUD, timeout=1)
            time.sleep(2)
            print(f"[servo] Reconnected to {ARDUINO_PORT}")
            with state_lock:
                state["servo"]["connected"] = True
        except Exception as e:
            print(f"[servo] Reconnect failed: {e}")
            return

    # Clamp to safe range
    angle = max(SERVO_MIN_ANGLE, min(SERVO_MAX_ANGLE, angle))

    # Skip if change is too small (avoids constant micro-jitter)
    if abs(angle - _servo_angle) < 2:
        return

    with _serial_lock:
        try:
            _arduino.write(f"{angle}\n".encode())
            _servo_angle = angle
            print(f"[servo] Sent servo angle: {angle}")
            with state_lock:
                state["servo"]["angle"] = angle
        except Exception as e:
            print(f"[servo] Serial write error: {e}")
            with state_lock:
                state["servo"]["connected"] = False
            _arduino = None

# ── Background: camera presence detection ─────────────────────────────────────
def camera_thread():
    """Uses OpenCV face detection for presence detection and servo face-tracking."""
    if not CV2_AVAILABLE:
        return
    try:
        cap = cv2.VideoCapture(CAMERA_INDEX)
        if not cap.isOpened():
            print(f"[camera] Could not open camera on index {CAMERA_INDEX}")
            return
        print(f"[camera] Camera opened on index {CAMERA_INDEX}")

        # Face detector — scaleFactor 1.1 catches more angles, minNeighbors 3 is more lenient
        face_cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        )
        # Full-body detector — fallback when face isn't frontal (e.g. looking sideways)
        body_cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_fullbody.xml"
        )
        was_present   = True
        absent_frames = 0
        # Track how long the person has been gone so we only greet after a real absence
        # (not a brief dropout during servo tracking). ~20 frames × 0.15s = ~3 seconds.
        GONE_THRESHOLD = 20
        gone_frames    = 0
        print("[camera] Presence detection + face tracking active")

        while True:
            ret, frame = cap.read()
            if not ret:
                time.sleep(2)
                continue

            # Resize to 320×240 — faster detection, Pi can handle it easily
            small  = cv2.resize(frame, (320, 240))
            frame_center_x = 320 // 2

            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            # equalizeHist boosts contrast so the detector finds faces in dim light
            gray = cv2.equalizeHist(gray)

            # Try face detection first (more precise for tracking)
            faces = face_cascade.detectMultiScale(
                gray,
                scaleFactor=1.1,
                minNeighbors=3,
                minSize=(40, 40),
            )

            # If no face found, try full-body detection as fallback
            if len(faces) == 0:
                faces = body_cascade.detectMultiScale(
                    gray,
                    scaleFactor=1.1,
                    minNeighbors=3,
                    minSize=(40, 80),
                )

            # ── Presence logic ─────────────────────────────────────────────────
            detected = len(faces) > 0

            if detected:
                absent_frames = 0
                gone_frames   = 0
            else:
                absent_frames += 1

            # Still count as present for a few frames to avoid flickering
            present_now = detected or absent_frames < 4

            if not present_now:
                gone_frames += 1
            else:
                gone_frames = 0

            with state_lock:
                sleeping = state["sleep_mode"]
                state["presence"] = present_now
                # Face/eyes are ONLY controlled by sleep_mode.
                # Presence detection no longer opens or closes the eyes.
                if sleeping and state["face"] != "sleeping":
                    state["face"] = "sleeping"
                elif not sleeping and state["face"] == "sleeping":
                    state["face"] = "idle"

            # Only greet when returning after a sustained real absence (not a brief dropout)
            if present_now and not was_present and gone_frames >= GONE_THRESHOLD:
                # greeting = _presence_greeting()
                # threading.Thread(target=speak, args=(greeting,), daemon=True).start()
                with state_lock:
                    if not state["sleep_mode"]:
                        state["face"] = "happy"

            was_present = present_now

            # ── Servo face-tracking ────────────────────────────────────────────
            if SERVO_ENABLED and len(faces) > 0 and not sleeping:

                # Pick the largest detected region (most likely the main person)
                largest = max(faces, key=lambda f: f[2] * f[3])
                fx, fy, fw, fh = largest

                face_center_x = fx + fw // 2
                offset        = face_center_x - frame_center_x

                if abs(offset) > SERVO_DEAD_ZONE:
                    if offset < 0:
                        direction = -1 if not SERVO_REVERSE else 1
                        print(f"[camera] Face left (offset {offset}px), moving servo")
                    else:
                        direction = 1 if not SERVO_REVERSE else -1
                        print(f"[camera] Face right (offset {offset}px), moving servo")

                    # Move faster the further the face is from center
                    # (proportional control — smoother than fixed step)
                    scale     = min(abs(offset) / frame_center_x, 1.0)
                    step      = max(1, int(SERVO_STEP_SIZE * scale * 2))
                    new_angle = _servo_angle + direction * step
                    move_servo(new_angle)

            time.sleep(0.15)   # ~6 frames/sec — fast enough to track smoothly

    except Exception as e:
        print(f"[camera] Error: {e}")


# ── Sleep / wake helpers ───────────────────────────────────────────────────────
_SLEEP_KEYWORDS = {
    "go to sleep", "sleep", "turn off", "shut down", "shutdown",
    "don't need you", "i don't need you", "leave me alone",
    "go away", "goodbye", "bye", "that's all", "that's it",
    "i'm done", "im done", "you can go", "go rest",
}

_WAKE_KEYWORDS = {
    "wake up", "turn on", "i need you", "come back",
    "hello", "hey", "good morning", "good afternoon",
    "good evening", "are you there", "start", "activate",
}

def _is_sleep_command(text):
    t = text.lower().strip()
    return any(kw in t for kw in _SLEEP_KEYWORDS)

def _is_wake_command(text):
    t = text.lower().strip()
    return any(kw in t for kw in _WAKE_KEYWORDS)

# ── Continuous background listener ────────────────────────────────────────────
_bg_queue = queue.Queue(maxsize=4)  # transcripts from background recorder
_bg_pause = threading.Event()       # set → background loop pauses (mic handoff)
_bg_proc  = None                    # current parecord Popen (for early termination)

def _bg_record_loop(recognizer):
    """
    Double-buffer pipeline: records 3s chunks with no gap.
    While one chunk is being transcribed in a thread, the next is already recording.
    Pushes transcripts to _bg_queue. Pauses when _bg_pause is set.
    """
    global _bg_proc
    files = ["/tmp/momo_bg_0.wav", "/tmp/momo_bg_1.wav"]
    slot  = 0
    while True:
        if _bg_pause.is_set():
            time.sleep(0.05)
            continue

        wav  = files[slot]
        slot ^= 1

        try:
            _bg_proc = subprocess.Popen(
                ["timeout", "3", "parecord",
                 "--channels=1", "--rate=16000", "--format=s16le", wav],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            _bg_proc.wait()
            _bg_proc = None
        except Exception as e:
            _bg_proc = None
            print(f"[voice-bg] record error: {e}")
            time.sleep(0.2)
            continue

        if _bg_pause.is_set():
            continue

        # Transcribe in a background thread so the next recording starts immediately
        wav_snap = wav
        def _tx(w):
            try:
                with sr.AudioFile(w) as src:
                    audio = recognizer.record(src)
                text = recognizer.recognize_google(audio).lower()
                if text:
                    try:
                        _bg_queue.put_nowait(text)
                    except queue.Full:
                        try: _bg_queue.get_nowait()
                        except Exception: pass
                        _bg_queue.put_nowait(text)
            except sr.UnknownValueError:
                pass
            except Exception as e:
                print(f"[voice-bg] transcribe error: {e}")
        threading.Thread(target=_tx, args=(wav_snap,), daemon=True).start()

# ── Voice loop ─────────────────────────────────────────────────────────────────
WAKE_WORD = "momo"

def _record_and_transcribe(recognizer, duration=5):
    """Record audio via parecord (PipeWire-pulse) and transcribe with Google STT."""
    wav = "/tmp/momo_listen.wav"
    try:
        # parecord talks directly to PipeWire-pulse; timeout stops it after N seconds
        result = subprocess.run(
            ["timeout", str(duration), "parecord",
             "--channels=1", "--rate=16000", "--format=s16le", wav],
            capture_output=True, timeout=duration + 3
        )
        # returncode 124 = timeout killed it (expected); 0 = finished early — both are fine
        if result.returncode not in (0, 124):
            print(f"[voice] parecord failed: {result.stderr.decode().strip()}")
            return None
    except Exception as e:
        print(f"[voice] record error: {e}")
        return None
    try:
        with sr.AudioFile(wav) as source:
            audio = recognizer.record(source)
        return recognizer.recognize_google(audio).lower()
    except sr.UnknownValueError:
        return None
    except sr.RequestError as e:
        print(f"[voice] STT error: {e}")
        return None
    except Exception as e:
        print(f"[voice] Transcribe error: {e}")
        return None

def voice_loop():
    recognizer = sr.Recognizer()
    print(f"[voice] Ready — say '{WAKE_WORD} <command>'")

    # Boost system mic gain so quiet speech is picked up more reliably
    if MIC_BOOST_PERCENT != 100:
        result = subprocess.run(
            ["pactl", "set-source-volume", "@DEFAULT_SOURCE@", f"{MIC_BOOST_PERCENT}%"],
            capture_output=True
        )
        if result.returncode == 0:
            print(f"[voice] Mic gain set to {MIC_BOOST_PERCENT}%")
        else:
            print(f"[voice] Could not set mic gain: {result.stderr.decode().strip()}")

    time.sleep(1.5)
    speak(_startup_greeting())

    # Start the double-buffer background listener (records continuously, no gap)
    threading.Thread(target=_bg_record_loop, args=(recognizer,), daemon=True, name="BgRecord").start()
    print("[voice] Continuous background listener started")

    while True:
        # ══ Phase 1: read from background pipeline — no recording gap ═════
        try:
            heard = _bg_queue.get(timeout=1)
        except queue.Empty:
            continue

        if not heard:
            continue
        print(f"[voice] Heard: '{heard}'")
        if WAKE_WORD not in heard:
            continue

        # Hand off the mic: pause bg recorder and kill its current recording
        _bg_pause.set()
        if _bg_proc and _bg_proc.poll() is None:
            try:
                _bg_proc.terminate()
                _bg_proc.wait(timeout=1)
            except Exception:
                pass
        while not _bg_queue.empty():
            try: _bg_queue.get_nowait()
            except Exception: pass

        try:
            print(f"[voice] Wake word detected: '{heard}'")
            after_wake = heard.split(WAKE_WORD, 1)[-1].strip()

            # ── Read sleep_mode once outside the hot path ──────────────────
            with state_lock:
                currently_sleeping = state["sleep_mode"]

            # ══ Sleep mode: only accept wake commands ═════════════════════
            if currently_sleeping:
                if after_wake and _is_wake_command(after_wake):
                    with state_lock:
                        state["sleep_mode"] = False
                        state["face"]       = "happy"
                    speak("I'm back! What do you need?")
                    with state_lock:
                        state["face"] = "idle"
                else:
                    print("[voice] Sleeping — ignoring command")
                continue

            # ══ Phase 2: active command cycle ════════════════════════════════
            if not after_wake:
                speak("Yeah?")
                with state_lock:
                    state["voice_state"] = "listening"
                after_wake = _record_and_transcribe(recognizer, duration=5)
                if not after_wake:
                    with state_lock:
                        state["voice_state"] = "idle"
                    continue

            command = after_wake
            print(f"[voice] Command to parse: '{command}'")

            # ── Check for sleep command before processing normally ──────────
            if _is_sleep_command(command):
                with state_lock:
                    state["sleep_mode"] = True
                    state["face"]       = "sleeping"
                speak("Okay, going to sleep! Say momo wake up when you need me.")
                with state_lock:
                    state["voice_state"] = "idle"
                continue

            with state_lock:
                state["voice_state"] = "thinking"

            action = parse_command(command) or keyword_fallback(command)

            needs_followup = (
                action.get("intent") in ("add_task","complete_task","remove_task")
                and not action.get("task","").strip()
            )
            if not needs_followup:
                execute_action(action)

            if "_servo_angle" in action:
                move_servo(action["_servo_angle"])

            if "_stop_music" in action:
                stop_music()

            spoken = handle_multi_turn(recognizer, action)
            with state_lock:
                state["voice_state"] = "speaking"
                state["last_spoken"] = spoken

            speak(spoken)

            # Start music AFTER TTS finishes so aplay device isn't busy
            if "_play_song" in action and action["_play_song"]:
                threading.Thread(target=play_music, args=(action["_play_song"],), daemon=True).start()

            with state_lock:
                state["voice_state"] = "idle"
                if state["face"] in ("happy","excited"):
                    state["face"] = "idle" if state["timer"]["active"] is False else "timer"
            time.sleep(0.3)

        finally:
            # Always give the mic back to the background listener
            _bg_pause.clear()

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
            "face":        state["face"],
            "last_spoken": state["last_spoken"],
            "weather":     dict(state["weather"]),
            "tasks":       list(state["tasks"]),
            "timer":       dict(state["timer"]),
            "presence":    state["presence"],
            "sleep_mode":  state["sleep_mode"],
            "servo":       dict(state["servo"]),
            "time":          time.strftime("%I:%M %p"),
            "date":          time.strftime("%A, %B %d"),
            "music_song":    state["music_song"],
        }
    return jsonify(snapshot)

@app.route("/api/screen", methods=["POST"])
def api_set_screen():
    data = request.json or {}
    with state_lock:
        state["screen"] = data.get("screen","clock")
    return jsonify({"ok": True})

@app.route("/api/weather", methods=["POST"])
def api_weather():
    weather = fetch_weather()
    with state_lock:
        state["weather"] = weather
        state["screen"]  = "weather"
    return jsonify(weather)

@app.route("/api/task/add", methods=["POST"])
def api_task_add():
    data = request.json or {}
    name = data.get("name","").strip()
    if not name:
        return jsonify({"ok": False, "error": "Name required"}), 400
    with state_lock:
        task = {"id": next_task_id(state["tasks"]), "name": name, "done": False}
        state["tasks"].append(task)
        save_tasks(state["tasks"])
    return jsonify({"ok": True, "task": task})

@app.route("/api/task/complete", methods=["POST"])
def api_task_complete():
    data = request.json or {}
    tid  = data.get("id")
    with state_lock:
        for t in state["tasks"]:
            if t["id"] == tid:
                t["done"] = True
                save_tasks(state["tasks"])
                return jsonify({"ok": True, "task": t})
    return jsonify({"ok": False, "error": "Not found"}), 404

@app.route("/api/task/remove", methods=["POST"])
def api_task_remove():
    data = request.json or {}
    tid  = data.get("id")
    with state_lock:
        state["tasks"] = [t for t in state["tasks"] if t["id"] != tid]
        save_tasks(state["tasks"])
    return jsonify({"ok": True})

@app.route("/api/timer/start", methods=["POST"])
def api_timer_start():
    data    = request.json or {}
    minutes = int(data.get("minutes", 25))
    label   = data.get("label", "Pomodoro" if minutes == 25 else "Focus Timer")
    total   = minutes * 60
    with state_lock:
        state["timer"] = {"active": True, "seconds_left": total,
                          "total_seconds": total, "label": label}
        state["face"]  = "timer"
    return jsonify({"ok": True})

@app.route("/api/timer/cancel", methods=["POST"])
def api_timer_cancel():
    with state_lock:
        state["timer"]["active"] = False
        state["face"] = "idle"
    return jsonify({"ok": True})

@app.route("/api/music/stop", methods=["POST"])
def api_music_stop():
    stop_music()
    return jsonify({"ok": True})

# ── Startup ────────────────────────────────────────────────────────────────────
atexit.register(stop_music)   # kill music pipeline on any exit (Ctrl+C, crash, etc.)

if __name__ == "__main__":
    with state_lock:
        state["tasks"] = load_tasks()
    print(f"[init] {len(state['tasks'])} task(s) loaded")
    print(f"[init] OpenAI: {'configured' if OPENAI_API_KEY else 'NOT SET — keyword fallback'}")

    # Servo init (blocking — waits 2s for Arduino reset before threads start)
    init_servo()

    # Timer tick thread
    threading.Thread(target=timer_tick, daemon=True, name="TimerTick").start()

    # Reminder checker thread
    threading.Thread(target=reminder_checker, daemon=True, name="Reminders").start()

    # Camera presence thread
    if CAMERA_ENABLED and CV2_AVAILABLE:
        threading.Thread(target=camera_thread, daemon=True, name="Camera").start()
        print("[init] Camera presence detection started")
    elif CAMERA_ENABLED and not CV2_AVAILABLE:
        print("[init] Camera disabled: pip install opencv-python")

    # Voice thread
    if VOICE_ENABLED and SR_AVAILABLE:
        threading.Thread(target=voice_loop, daemon=True, name="VoiceLoop").start()
        print("[init] Voice loop started")

    print("[init] http://0.0.0.0:5000")
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
