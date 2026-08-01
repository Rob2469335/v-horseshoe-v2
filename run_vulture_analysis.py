#!/usr/bin/env python3
"""
Script to run Vulture static analysis on the codebase.
This script installs Vulture and runs it to detect dead code.
"""

import subprocess
import sys

def install_vulture():
    """Install vulture static analysis tool."""
    print("Installing Vulture...")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "vulture"])
        print("Vulture installed successfully.")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Failed to install Vulture: {e}")
        return False


def run_vulture_analysis():
    """Run vulture analysis on the codebase and return JSON output."""
    print("Running Vulture analysis...")
    try:
        result = subprocess.run([
            sys.executable, "-m", "vulture", "./", "--min-confidence", "80"], 
            capture_output=True, text=True, check=True, timeout=60
        )
        return result.stdout
    except subprocess.TimeoutExpired:
        print("Vulture analysis timed out.")
        return None
    except subprocess.CalledProcessError as e:
        print(f"Vulture analysis failed: {e}")
        return None


def main():
    if not install_vulture():
        sys.exit(1)
    
    output = run_vulture_analysis()
    if output is None:
        print("No output from Vulture.")
        sys.exit(1)
    
    print("Vulture Analysis Results:")
    print(output)

if __name__ == "__main__":
    main()