def roman_to_int(s: str) -> int:
    # Define a mapping of Roman numerals to integers
    roman_map = {
        'I': 1,
        'V': 5,
        'X': 10,
        'L': 50,
        'C': 100,
        'D': 500,
        'M': 1000
    }
    
    # Initialize the previous value and total sum
    prev_value = 0
    total_sum = 0
    
    # Iterate over the string in reverse order
    for char in reversed(s):
        current_value = roman_map[char]
        if current_value < prev_value:
            total_sum -= current_value
        else:
            total_sum += current_value
        prev_value = current_value
    
    return total_sum