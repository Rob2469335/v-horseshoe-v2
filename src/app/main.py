import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from security.xss_fix import sanitize_user_input
from core.refactor_critical_modules import CriticalModuleRefactor
from utils.input_validator import InputValidator

def main():
    """
    Main application entry point.
    """
    print("Starting application with XSS protection and refactored modules...")
    
    # Example usage of XSS protection
    user_input = "<script>alert('XSS')</script>"
    sanitized = sanitize_user_input(user_input)
    print(f"Original input: {user_input}")
    print(f"Sanitized input: {sanitized}")
    
    # Example usage of input validation
    email = "test@example.com"
    is_valid = InputValidator.is_valid_email(email)
    print(f"Email '{email}' is valid: {is_valid}")
    
    # Example usage of critical module refactor
    data_processor = CriticalModuleRefactor()
    sample_data = [1, 2, None, 3, 4]
    optimized_data = data_processor.optimize_data_processing(sample_data)
    print(f"Optimized data: {optimized_data}")
    
    print("Application started successfully.")

if __name__ == "__main__":
    main()