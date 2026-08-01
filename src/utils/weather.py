# utils/weather.py

import requests


def get_current_temperature(latitude=40.7128, longitude=-74.0060, temperature_unit='fahrenheit', timezone='America/New_York'):
    url = 'https://api.open-meteo.com/v1/forecast'
    params = {
        'latitude': latitude,
        'longitude': longitude,
        'current': 'temperature_2m',
        'temperature_unit': temperature_unit,
        'timezone': timezone
    }
    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        # Assuming the temperature is under a specific key in the JSON structure
        temperature = data['current_weather']['temperature']
        return temperature
    except requests.exceptions.RequestException as e:
        print(f'Error fetching temperature: {e}')
        return None