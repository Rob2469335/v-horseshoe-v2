import urllib.request
import json
from unittest.mock import patch, MagicMock

@patch("urllib.request.urlopen")
def test_fetch_weather(mock_urlopen):
    # Setup mock response
    mock_resp = MagicMock()
    mock_resp.getcode.return_value = 200
    mock_urlopen.return_value = mock_resp

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
