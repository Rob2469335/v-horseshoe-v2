import urllib.request
# This API endpoint requires a special User-Agent and JSON parsing, 
# but we are doing it completely wrong and it crashes.
def fetch_weather():
    url = "https://api.weather.gov/points/39.7456,-97.0892"
    # This fails because we are omitting the User-Agent parameter
    req = urllib.request.Request(url)
    resp = urllib.request.urlopen(req)
    return resp.read()
    
if __name__ == '__main__':
    print(fetch_weather())