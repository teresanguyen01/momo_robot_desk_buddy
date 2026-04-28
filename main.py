import time
import pyttsx3
import pygame

from ui import draw_clock, draw_weather, draw_message
from weather import get_weather

engine = pyttsx3.init()
engine.setProperty("rate", 160)

def speak(text):
    print("Robot:", text)
    engine.say(text)
    engine.runAndWait()

def handle_command(command):
    command = command.lower().strip()

    if "weather" in command:
        draw_message("Checking weather...")
        weather = get_weather("New Haven")
        draw_weather(weather)
        speak(f'The weather in {weather["city"]} is {weather["temp_f"]} degrees and {weather["description"]}.')
        time.sleep(6)

    elif "hello" in command:
        draw_message("Hi! I am DeskBuddy.")
        speak("Hi! I am Desk Buddy.")
        time.sleep(3)

    elif "time" in command:
        current_time = time.strftime("%I:%M %p")
        draw_message(current_time)
        speak(f"The time is {current_time}.")
        time.sleep(3)

    elif "quit" in command:
        speak("Goodbye.")
        return False

    return True

def main():
    speak("Desk Buddy is ready.")

    running = True
    while running:
        draw_clock()

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False

        command = input("Type a command: ")
        running = handle_command(command)

    pygame.quit()

if __name__ == "__main__":
    main()