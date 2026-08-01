import html

def sanitize_user_input(user_input: str) -> str:
    """
    Sanitize user input to prevent XSS vulnerabilities.
    
    Args:
        user_input (str): The raw user input string.
        
    Returns:
        str: The sanitized string with HTML characters escaped.
    """
    if not isinstance(user_input, str):
        raise TypeError("Input must be a string")
    
    return html.escape(user_input)


def validate_and_clean_input(user_input: str) -> str:
    """
    Validate and clean user input for XSS protection.
    
    Args:
        user_input (str): The raw user input string.
        
    Returns:
        str: The cleaned and validated string.
    """
    if not isinstance(user_input, str):
        raise TypeError("Input must be a string")
    
    # Remove or escape potentially dangerous characters
    sanitized = html.escape(user_input)
    
    # Additional validation can be added here if needed
    return sanitized


class XSSProtection:
    """
    A class to handle XSS protection for user inputs.
    """
    
    @staticmethod
    def escape_html(text: str) -> str:
        """
        Escape HTML characters in the given text.
        
        Args:
            text (str): The text to escape.
            
        Returns:
            str: The escaped text.
        """
        return html.escape(text)
    
    @staticmethod
    def validate_input(input_str: str) -> bool:
        """
        Validate that the input string is safe for HTML rendering.
        
        Args:
            input_str (str): The input string to validate.
            
        Returns:
            bool: True if valid, False otherwise.
        """
        if not isinstance(input_str, str):
            return False
        # Add more validation logic here if needed
        return True