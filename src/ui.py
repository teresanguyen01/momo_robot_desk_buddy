import pygame
import time

WIDTH = 480
HEIGHT = 320

pygame.init()
screen = pygame.display.set_mode((480, 320), pygame.FULLSCREEN)
pygame.mouse.set_visible(False)
pygame.display.set_caption("DeskBuddy")

font_big = pygame.font.SysFont(None, 72)
font_med = pygame.font.SysFont(None, 38)
font_small = pygame.font.SysFont(None, 28)

def draw_clock():
    screen.fill((18, 18, 28))

    now = time.strftime("%I:%M %p")
    date = time.strftime("%a %b %d")

    time_text = font_big.render(now, True, (255, 255, 255))
    date_text = font_med.render(date, True, (180, 180, 180))
    hint = font_small.render("Say or type: weather", True, (130, 130, 130))

    screen.blit(time_text, (70, 105))
    screen.blit(date_text, (145, 180))
    screen.blit(hint, (135, 265))

    pygame.display.flip()

def draw_weather(weather):
    screen.fill((20, 45, 75))

    title = font_med.render(weather["city"], True, (255, 255, 255))
    temp = font_big.render(f'{weather["temp_f"]}°F', True, (255, 255, 255))
    desc = font_med.render(weather["description"], True, (230, 230, 230))

    screen.blit(title, (30, 40))
    screen.blit(temp, (30, 120))
    screen.blit(desc, (30, 215))

    pygame.display.flip()

def draw_message(message):
    screen.fill((25, 25, 35))
    text = font_med.render(message, True, (255, 255, 255))
    screen.blit(text, (30, 140))
    pygame.display.flip()
