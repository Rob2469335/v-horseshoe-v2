import subprocess
import sys

def run_vulture_scan(directory: str):
    """
    Run Vulture static analysis scan on the specified directory.
    """
    try:
        result = subprocess.run([
            sys.executable, '-m', 'vulture', directory,
            '--min-confidence', '80'
        ], capture_output=True, text=True, check=True)
        
        if result.stdout:
            print('Vulture scan results:')
            print(result.stdout)
        else:
            print('No dead code found!')
            
        return result.stdout
    except subprocess.CalledProcessError as e:
        print(f'Vulture scan failed: {e}')
        return None


def run_dead_code_cleaner(file_path: str):
    """
    Run the dead code cleaner on a specific file.
    """
    try:
        result = subprocess.run([
            sys.executable, 'dead_code_cleaner.py', file_path
        ], capture_output=True, text=True, check=True)
        
        if result.stdout:
            print('Dead code cleaner results:')
            print(result.stdout)
        
        return result.stdout
    except subprocess.CalledProcessError as e:
        print(f'Dead code cleaner failed: {e}')
        return None


def integrate_with_ci_cd():
    """
    Integrate dead code detection with CI/CD pipeline.
    """
    print('Integrating with CI/CD pipeline...')
    
    # Run Vulture scan on the entire project
    run_vulture_scan('.')
    
    # You can also add more sophisticated checks here
    # For example, checking for unused imports in different scopes
    # or running the dead code cleaner on specific files
    
    print('CI/CD integration complete!')


if __name__ == '__main__':
    integrate_with_ci_cd()
