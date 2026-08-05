import requests

def get_current_temperature(latitude, longitude, temperature_unit='fahrenheit', timezone='America/New_York'):
    url = f'https://api.open-meteo.com/v1/forecast?latitude={latitude}&longitude={longitude}&current_weather=true&temperature_unit={temperature_unit}&timezone={timezone}'
    try:
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        return data['current_weather']['temperature']
    except requests.exceptions.RequestException as e:
        print(f'Error fetching temperature: {e}')
        return None

# Example usage
if __name__ == '__main__':
    temp = get_current_temperature(40.7128, -74.0060)
    if temp is not None:
        print(f'Current temperature: {temp}°F')