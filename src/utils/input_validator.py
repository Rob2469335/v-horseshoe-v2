import re
from typing import Optional

class InputValidator:
    """
    A utility class for validating and sanitizing user inputs.
    """
    
    @staticmethod
    def is_valid_email(email: str) -> bool:
        """
        Validate if the provided string is a valid email address.
        
        Args:
            email (str): The email address to validate.
            
        Returns:
            bool: True if valid, False otherwise.
        """
        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        return re.match(pattern, email) is not None
    
    @staticmethod
    def is_valid_username(username: str) -> bool:
        """
        Validate if the provided string is a valid username.
        
        Args:
            username (str): The username to validate.
            
        Returns:
            bool: True if valid, False otherwise.
        """
        # Username must be 3-20 characters long and contain only alphanumeric characters and underscores
        pattern = r'^[a-zA-Z0-9_]{3,20}$'
        return re.match(pattern, username) is not None
    
    @staticmethod
    def is_valid_password(password: str) -> bool:
        """
        Validate if the provided string is a valid password.
        
        Args:
            password (str): The password to validate.
            
        Returns:
            bool: True if valid, False otherwise.
        """
        # Password must be at least 8 characters long
        return len(password) >= 8
    
    @staticmethod
    def sanitize_input(input_str: str) -> str:
        """
        Sanitize input string by removing or escaping dangerous characters.
        
        Args:
            input_str (str): The input string to sanitize.
            
        Returns:
            str: The sanitized string.
        """
        # Remove or escape potentially dangerous characters
        return re.sub(r'[<>&"\']', '', input_str)
    
    @staticmethod
    def validate_and_clean_input(input_str: str) -> Optional[str]:
        """
        Validate and clean user input.
        
        Args:
            input_str (str): The input string to validate and clean.
            
        Returns:
            Optional[str]: The cleaned input string if valid, None otherwise.
        """
        if not isinstance(input_str, str):
            return None
        
        # Check for empty or whitespace-only strings
        if not input_str.strip():
            return None
        
        # Sanitize the input
        sanitized = InputValidator.sanitize_input(input_str)
        
        return sanitized