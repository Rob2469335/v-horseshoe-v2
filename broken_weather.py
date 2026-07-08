import urllib.request
import json

# This API endpoint requires a special User-Agent and JSON parsing
def fetch_weather():
    url = "https://api.weather.gov/points/39.7456,-97.0892"
    # Add required User-Agent header
    req = urllib.request.Request(
        url,
        headers={
            'User-Agent': 'HorseshoeSwarm/1.0 (https://github.com/your-username/v-horseshoe-v2)',
            'Accept': 'application/json'
        }
    )
    try:
        resp = urllib.request.urlopen(req)
        data = json.loads(resp.read().decode('utf-8'))
        return data
    except Exception as e:
        return f"Error fetching weather data: {e}"
    
if __name__ == '__main__':
    result = fetch_weather()
    print(json.dumps(result, indent=2))