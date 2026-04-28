import time
import subprocess
import pygame
import speech_recognition as sr

from ui import draw_clock, draw_weather, draw_message
from weather import get_weather


def speak(text):
    print("Robot:", text)
    subprocess.run(["espeak", "-a", "200", "-s", "160", text])


def check_quit_events():
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            return True

        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE or event.key == pygame.K_q:
                return True

    return False


def listen_for_command():
    recognizer = sr.Recognizer()

    try:
        with sr.Microphone() as source:
            print("Listening...")
            recognizer.adjust_for_ambient_noise(source, duration=1)
            audio = recognizer.listen(source, timeout=5, phrase_time_limit=4)

        command = recognizer.recognize_google(audio)
        print("You said:", command)
        return command.lower()

    except sr.WaitTimeoutError:
        print("No speech detected.")
        return ""

    except sr.UnknownValueError:
        print("Could not understand audio.")
        return ""

    except sr.RequestError as e:
        print("Speech recognition error:", e)
        return ""


def handle_command(command):
    command = command.lower().strip()

    if command == "":
        return True

    if "weather" in command:
        draw_message("Checking weather...")
        weather = get_weather("New Haven")
        draw_weather(weather)
        speak(
            f'The weather in {weather["city"]} is '
            f'{weather["temp_f"]} degrees and {weather["description"]}.'
        )
        time.sleep(6)

    elif "hello" in command or "hi" in command:
        draw_message("Hi! I am DeskBuddy.")
        speak("Hi! I am Desk Buddy.")
        time.sleep(3)

    elif "time" in command:
        current_time = time.strftime("%I:%M %p")
        draw_message(current_time)
        speak(f"The time is {current_time}.")
        time.sleep(3)

    elif "quit" in command or "exit" in command or "stop" in command:
        speak("Goodbye.")
        return False

    else:
        draw_message("I did not understand.")
        speak("I did not understand.")
        time.sleep(2)

    return True


def main():
    speak("Desk Buddy is ready.")

    running = True

    while running:
        draw_clock()

        if check_quit_events():
            running = False
            break

        command = listen_for_command()

        if check_quit_events():
            running = False
            break

        running = handle_command(command)

    pygame.quit()


if __name__ == "__main__":
    main()