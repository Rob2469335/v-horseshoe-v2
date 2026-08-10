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
    assert is_permanent_error("404 Not Found") is True
    assert is_permanent_error("status 404: no such model") is True


def test_genuine_auth_and_billing_still_permanent():
    assert is_permanent_error("HTTP 401 Unauthorized") is True
    assert is_permanent_error("insufficient balance") is True
    assert is_permanent_error("invalid api key provided") is True


def test_transient_error_no_markers_is_not_permanent():
    assert is_permanent_error("upstream timed out after 5.0s, retry once") is False
