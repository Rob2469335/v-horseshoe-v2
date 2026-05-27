import logging
import asyncio
import os
from typing import Dict, Any, List
from swarm_os.capabilities.models import VSCodeAutomationRequest, VSCodeAutomationResponse
from swarm_os.lib.safety import validate_path

logger = logging.getLogger(__name__)

class VSCodeAutomationHandler:
    def __init__(self, workspace_root: str = "."):
        self.workspace_root = workspace_root
        # Explicit allowlist of commands mapped to actual safe system calls
        self.allowed_commands = {
            "test": ["python", "-m", "pytest"],
            "format": ["python", "-m", "black", "."],
            "list_files": [], # Handled internally by os.listdir safely
            "status": ["git", "status"],
            "lint": ["python", "-m", "flake8"]
        }
        logger.info(f"Initialized operational secure VSCodeAutomationHandler at {workspace_root}")

    async def execute(self, payload: VSCodeAutomationRequest) -> VSCodeAutomationResponse:
        command = payload.command.lower().strip()
        
        # Sanitize and clean custom argument characters to neutralize shell injection attempts
        sanitized_args = []
        for arg in payload.args:
            clean_arg = "".join(c for c in str(arg) if c.isalnum() or c in "._-/\\")
            if clean_arg:
                try:
                    validate_path(clean_arg, self.workspace_root)
                    sanitized_args.append(clean_arg)
                except ValueError as ve:
                    return VSCodeAutomationResponse(
                        status="rejected",
                        command=command,
                        stdout="",
                        stderr=f'Security Containment Error: {str(ve)}',
                        exit_code=1,
                        message="Directory containment breach prevented."
                    )

        if command not in self.allowed_commands:
            return VSCodeAutomationResponse(
                status="rejected",
                command=command,
                stdout="",
                stderr=f"Security Error: Command '{command}' is not on the safety allowlist.",
                exit_code=1,
                message="Rejected disallowed workspace command."
            )

        # 1. Handle list_files safely using Python's built-in OS tools
        if command == "list_files":
            try:
                files = []
                for root, _, filenames in os.walk(self.workspace_root):
                    for f in filenames:
                        rel_path = os.path.relpath(os.path.join(root, f), self.workspace_root)
                        files.append(rel_path.replace("\\", "/"))
                
                return VSCodeAutomationResponse(
                    status="executed",
                    command=command,
                    stdout="\n".join(files),
                    stderr="",
                    exit_code=0,
                    message="Listed known workspace files safely."
                )
            except Exception as e:
                return VSCodeAutomationResponse(
                    status="failed",
                    command=command,
                    stdout="",
                    stderr=str(e),
                    exit_code=500,
                    message="Failed to scan directory."
                )

        # 2. Run actual safe background system subprocesses for other allowlisted commands
        base_tokens = list(self.allowed_commands[command])
        full_command = base_tokens + sanitized_args

        try:
            # Bypasses the shell entirely to guarantee security stability
            proc = await asyncio.create_subprocess_exec(
                *full_command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.workspace_root
            )
            stdout_bytes, stderr_bytes = await proc.communicate()
            
            return VSCodeAutomationResponse(
                status="executed",
                command=command,
                stdout=stdout_bytes.decode("utf-8", errors="replace"),
                stderr=stderr_bytes.decode("utf-8", errors="replace"),
                exit_code=proc.returncode if proc.returncode is not None else 0,
                message="Command executed successfully."
            )
        except Exception as e:
            return VSCodeAutomationResponse(
                status="failed",
                command=command,
                stdout="",
                stderr=str(e),
                exit_code=1,
                message="Subprocess execution failed."
            )


