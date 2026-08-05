import requests

def fetch_nyc_temperature():
    url = "https://api.open-meteo.com/v1/forecast?latitude=40.7128&longitude=-74.0060&current=temperature_2m"
    try:
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        data = response.json()
        if 'current' in data and 'temperature_2m' in data['current']:
            temperature_celsius = data['current']['temperature_2m']
            print(f"Current temperature in NYC: {temperature_celsius:.1f}°C")
        else:
            raise ValueError("Unexpected API response format. 'temperature_2m' not found.")
    except requests.exceptions.RequestException as e:
        print(f"Request failed: {e}")
    except (KeyError, ValueError) as e:
        print(f"Data parsing error: {e}")

if __name__ == "__main__":
    fetch_nyc_temperature()