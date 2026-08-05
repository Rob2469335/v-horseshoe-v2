"""
cli/cli.py - CLI Entry Point
"""
import argparse
import sys


def main():
    parser = argparse.ArgumentParser(description="OmniDev CLI")
    parser.add_argument("command", nargs="?", help="Command to run")
    parser.add_argument("--task", help="Task description")
    
    args = parser.parse_args()
    
    if args.command == "run" and args.task:
        print(f"Running task: {args.task}")
        return 0
    elif args.command:
        print(f"Unknown command: {args.command}")
        return 1
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
