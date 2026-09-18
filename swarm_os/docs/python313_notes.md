# Python 3.13 Release Notes Summary

Python 3.13 is a major stable release with significant language, implementation, and standard library improvements. Key highlights:

- **New Interactive Interpreter**: A modern, more user-friendly REPL with colorized tracebacks and improved error messages.
- **Free-threaded Mode (PEP 703)**: Experimental support for running CPython without the Global Interpreter Lock (GIL).
- **Just-In-Time Compiler (PEP 744)**: Experimental JIT compiler for improved performance.
- **Type Parameter Defaults**: Type parameters now support default values.
- **Defined Semantics for `locals()`**: The builtin now has well-defined behavior for modifying the returned mapping.
- **Platform Support**: iOS and Android are now Tier 3 supported platforms.
- **Removed Deprecated Modules**: Several legacy standard library modules removed per PEP 594.
- **Improved Error Messages**: Tracebacks are highlighted in color by default.
- **Maintenance Releases**: The 3.13 series continues with maintenance releases (e.g., 3.13.13, 3.13.14) containing bugfixes and build improvements.

For full details, see the official [What's New in Python 3.13](https://docs.python.org/3/whatsnew/3.13.html) documentation.
