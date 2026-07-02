import urllib.request
import json

def test_fetch_weather():
    url = "https://api.weather.gov/points/39.7456,-97.0892"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "(v-horseshoe-v2 test suite, robert@example.com)",
            "Accept": "application/geo+json",
        },
    )
    resp = urllib.request.urlopen(req)
    assert resp.getcode() == 200
