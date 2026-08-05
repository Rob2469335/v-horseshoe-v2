"""Allow `python -m organism_console` to launch the CLI."""

from organism_console.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
