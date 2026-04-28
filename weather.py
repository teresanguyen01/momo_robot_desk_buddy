import requests

def get_weather(city="New Haven"):
    url = f"https://wttr.in/{city}?format=j1"
    data = requests.get(url, timeout=10).json()

    current = data["current_condition"][0]
    temp_f = current["temp_F"]
    desc = current["weatherDesc"][0]["value"]

    return {
        "city": city,
        "temp_f": temp_f,
        "description": desc
    }