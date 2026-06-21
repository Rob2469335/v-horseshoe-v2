import logging
import asyncio
import os
from pathlib import Path
from typing import Dict, Any, List
from swarm_os.capabilities.models import VSCodeAutomationRequest, VSCodeAutomationResponse
from swarm_os.lib.safety import validate_path

logger = logging.getLogger(__name__)


class VSCodeAutomationHandler:
    def __init__(self, workspace_root: str = str(Path(__file__).resolve().parents[1])):
        self.workspace_root = workspace_root
        self.allowed_commands = {
            "test": ["python", "-m", "pytest"],
            "format": ["python", "-m", "black", "."],
            "list_files": [],
            "status": ["git", "status"],
            "lint": ["python", "-m", "flake8"],
            "grep": ["grep", "-r"],
            "ls": ["ls", "-R"],
            "cat": ["cat"],
            "diff": ["git", "diff"],
            "log": ["git", "log", "-n", "5"],
            "scout": ["powershell.exe", "-Command", "Write-Host 'Active Branch:'; git branch --show-current; Write-Host '`nRecent Changes:'; git status -s; Write-Host '`nProject Tree:'; Get-ChildItem -Depth 1"],
        }
        logger.info(f"Initialized operational secure VSCodeAutomationHandler at {workspace_root}")

    def _resolve_path(self, rel_path: str) -> Path:
        root_path = Path(self.workspace_root).resolve()
        target_path = (root_path / rel_path).resolve()
        try:
            target_path.relative_to(root_path)
        except ValueError:
            raise ValueError(f"Security Containment Error: Path is outside workspace: {rel_path}")
        return target_path

    async def execute(self, payload: Any) -> Dict[str, Any]:
        if isinstance(payload, dict):
            command = payload.get("command", "")
            args = payload.get("args", [])
        else:
            command = getattr(payload, "command", "")
            args = getattr(payload, "args", [])

        command = str(command).lower().strip()
        
        try:
            if command == "cat":
                if not args:
                    raise ValueError("cat requires a path argument")
                target_file = self._resolve_path(args[0])
                if not target_file.is_file():
                    raise FileNotFoundError(f"File not found: {args[0]}")
                output = target_file.read_text(encoding="utf-8", errors="replace")
                
            elif command == "grep":
                if not args:
                    raise ValueError("grep requires a pattern argument")
                pattern = args[0]
                search_path = args[1] if len(args) > 1 else ""
                
                recursive = True
                if len(args) > 2 and args[2] == "--recursive":
                    recursive = True
                
                target_dir = self._resolve_path(search_path)
                matches = []
                
                def grep_file(file_path: Path):
                    try:
                        content = file_path.read_text(encoding="utf-8", errors="ignore")
                        for i, line in enumerate(content.splitlines(), 1):
                            if pattern in line:
                                rel_file = str(file_path.relative_to(Path(self.workspace_root).resolve())).replace("\\", "/")
                                matches.append(f"{rel_file}:{i}:{line}")
                    except Exception:
                        pass

                if target_dir.is_file():
                    grep_file(target_dir)
                elif target_dir.is_dir():
                    if recursive:
                        for p in target_dir.rglob("*"):
                            if p.is_file() and not any(part.startswith('.') or part in ('node_modules', '.venv') for part in p.parts):
                                grep_file(p)
                    else:
                        for p in target_dir.glob("*"):
                            if p.is_file():
                                grep_file(p)
                output = "\n".join(matches)

            elif command == "ls":
                search_path = args[0] if args else ""
                target_dir = self._resolve_path(search_path)
                if not target_dir.exists():
                    raise FileNotFoundError(f"Path not found: {search_path}")
                
                if target_dir.is_file():
                    output = f"{target_dir.name} ({target_dir.stat().st_size} bytes)"
                else:
                    entries = []
                    for item in target_dir.iterdir():
                        size_str = f" ({item.stat().st_size} bytes)" if item.is_file() else " (dir)"
                        entries.append(f"{item.name}{size_str}")
                    output = "\n".join(entries)

            elif command == "find":
                search_path = args[0] if args else ""
                glob_pattern = args[1] if len(args) > 1 else "*"
                target_dir = self._resolve_path(search_path)
                
                found_files = []
                if target_dir.is_dir():
                    for p in target_dir.rglob(glob_pattern):
                        if p.is_file() and not any(part.startswith('.') or part in ('node_modules', '.venv') for part in p.parts):
                            rel_file = str(p.relative_to(Path(self.workspace_root).resolve())).replace("\\", "/")
                            found_files.append(rel_file)
                output = "\n".join(found_files)

            elif command == "scout":
                if not args:
                    raise ValueError("scout requires a path argument")
                file_path = args[0]
                start_line = int(args[1]) if len(args) > 1 else 1
                end_line = int(args[2]) if len(args) > 2 else 100
                
                target_file = self._resolve_path(file_path)
                if not target_file.is_file():
                    raise FileNotFoundError(f"File not found: {file_path}")
                    
                lines = target_file.read_text(encoding="utf-8", errors="replace").splitlines()
                sliced_lines = lines[start_line-1:end_line]
                output = "\n".join(sliced_lines)

            else:
                raise ValueError(f"Security Error: Command '{command}' is not on the safety allowlist.")

            return {
                "ok": True,
                "output": output,
                "error": "",
                "status": "executed",
                "command": command,
                "stdout": output,
                "stderr": "",
                "exit_code": 0,
                "message": "Command executed successfully."
            }

        except Exception as e:
            return {
                "ok": False,
                "output": "",
                "error": str(e),
                "status": "failed",
                "command": command,
                "stdout": "",
                "stderr": str(e),
                "exit_code": 1,
                "message": str(e)
            }

