from runtime_v2.services.fallback_manager import is_permanent_error


def test_transient_error_containing_404_digits_is_not_permanent():
    """Substring matching falsely pinned this as a permanent 404-not-found:
    '404' appears inside '4040ms', not as an HTTP status. The numeric status
    markers must match as standalone tokens (\b404\b), so a transient
    connection-timeout message stays retryable."""
    assert is_permanent_error("connection timeout after 4040ms") is False


def test_transient_error_containing_401_digits_is_not_permanent():
    assert is_permanent_error("retrying in 4010 ms") is False


def test_genuine_404_not_found_is_permanent():
    assert is_permanent_error("404") is True
    assert is_permanent_error("HTTP 404") is True
    assert is_permanent_error("status code 404") is True
    assert is_permanent_error("404 Not Found") is True
    assert is_permanent_error("status 404: no such model") is True


def test_genuine_404_with_timing_info_still_permanent():
    """A genuine 404 that merely CO-OCCURS with timing info must stay a
    permanent error. A substring "ms" exclusion was too broad and masked this:
    the standalone 404 here is a real status, not a millisecond measure."""
    assert is_permanent_error("request failed after 404ms with status 404") is True


def test_ms_following_digits_is_not_an_http_status():
    """The numeric status token must not be immediately followed by 'ms' (a
    millisecond measure). 'timeout after 404 ms' contains the digits but no
    HTTP status."""
    assert is_permanent_error("timed out after 404ms") is False
    assert is_permanent_error("timeout after 404 ms") is False


def test_genuine_auth_and_billing_still_permanent():
    assert is_permanent_error("HTTP 401 Unauthorized") is True
    assert is_permanent_error("insufficient balance") is True
    assert is_permanent_error("invalid api key provided") is True


def test_transient_error_no_markers_is_not_permanent():
    assert is_permanent_error("upstream timed out after 5.0s, retry once") is False
