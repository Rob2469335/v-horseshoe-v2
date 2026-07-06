import pytest
from src.calculator import divide

def test_divide_by_zero():
    """Test that divide by zero raises ZeroDivisionError."""
    with pytest.raises(ZeroDivisionError):
        divide(10, 0)

def test_divide_normal():
    """Test normal division works correctly."""
    assert divide(10, 2) == 5
    assert divide(9, 3) == 3
    assert divide(7, 2) == 3.5

def test_divide_negative():
    """Test division with negative numbers."""
    assert divide(-10, 2) == -5
    assert divide(10, -2) == -5
    assert divide(-10, -2) == 5

def test_divide_by_zero_float():
    """Test that divide by zero with floats raises ZeroDivisionError."""
    with pytest.raises(ZeroDivisionError):
        divide(10.0, 0.0)

def test_divide_zero_by_number():
    """Test that dividing zero by a number works."""
    assert divide(0, 10) == 0
    assert divide(0, -5) == 0
