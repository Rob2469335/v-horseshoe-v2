from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)


def filesystem_handler(
    params: Dict[str, Any], root: Path, trace_hook=None
) -> Dict[str, Any]:
    """
    Handles filesystem operations within a sandboxed root. SYNCHRONOUS: it does
    blocking file I/O (reads, writes, recursive walks) and must be called via
    asyncio.to_thread from async callers so it never blocks the event loop.
    """
    op_raw = str(params.get("operation", "read")).lower().strip()
    if op_raw in (
        "read",
        "read_file",
        "read_all",
        "read_files",
        "read_multiple",
        "view",
        "view_file",
        "cat",
        "get",
        "get_file",
    ):
        operation = "read"
    elif op_raw in ("write", "write_file", "create", "create_file", "save", "put"):
        operation = "write"
    elif op_raw in (
        "patch",
        "edit",
        "update",
        "modify",
        "replace",
        "replace_file_content",
        "edit_file",
    ):
        operation = "patch"
    elif op_raw in (
        "list",
        "list_files",
        "list_dir",
        "ls",
        "dir",
        "directory",
        "list_directory",
        "scandir",
        "scan_dir",
        "walk",
    ):
        operation = "list"
    elif op_raw in ("search", "grep", "find", "grep_search", "search_files"):
        operation = "grep"
    elif op_raw in ("glob", "wildcard", "match", "pattern"):
        operation = "glob"
    else:
        operation = op_raw

    path_param = params.get("path", params.get("paths", ""))
    if isinstance(path_param, list):
        if len(path_param) == 1:
            requested = str(path_param[0])
        else:
            requested = path_param
    else:
        requested = str(path_param)

    # Resolve and force to be absolute paths
    root = root.resolve()

    def resolve_in_sandbox(requested_path_str: str) -> Path:
        # LLMs often assume they are in a linux root directory
        if requested_path_str in ("/", "\\"):
            requested_path_str = "."
        elif (
            requested_path_str.startswith("/") or requested_path_str.startswith("\\")
        ) and ":" not in requested_path_str:
            requested_path_str = requested_path_str.lstrip("/\\") or "."

        try:
            req_path = Path(requested_path_str or ".")
            if req_path.is_absolute():
                target_path = req_path.resolve()
            else:
                target_path = (root / req_path).resolve()
            target_path.relative_to(root)
            return target_path
        except ValueError as e:
            raise ValueError(f"Path is outside sandbox: {requested_path_str}") from e

    try:
        target_path = resolve_in_sandbox(
            requested[0] if isinstance(requested, list) else requested
        )

        if operation == "read":

            def _read_file_capped(file_path: Path, max_chars: int = 50000) -> str:
                try:
                    raw_bytes = file_path.read_bytes()
                    text = raw_bytes.decode("utf-8", errors="replace")
                    if len(text) > max_chars:
                        text = (
                            text[:max_chars]
                            + f"\n... [TRUNCATED to {max_chars} chars] ..."
                        )
                    return text
                except Exception as ex:
                    return f"(ERROR reading {file_path}: {ex})"

            def _read_path_entry(
                p_str: str, max_chars: int = 50000
            ) -> tuple[list[str], list[str]]:
                try:
                    t_path = resolve_in_sandbox(p_str)
                    if t_path.exists() and t_path.is_file():
                        return (
                            [
                                f"=== FILE: {p_str} ===\n{_read_file_capped(t_path, max_chars)}"
                            ],
                            [str(t_path)],
                        )
                    elif t_path.exists() and t_path.is_dir():
                        res_c = []
                        paths_r = []
                        count = 0
                        for fp in sorted(t_path.rglob("*")):
                            if count >= 15:
                                res_c.append(
                                    f"=== DIRECTORY {p_str} TRUNCATED AT 15 FILES ==="
                                )
                                break
                            if (
                                fp.is_file()
                                and not any(
                                    x in fp.parts
                                    for x in (
                                        ".venv",
                                        ".git",
                                        "__pycache__",
                                        "node_modules",
                                        ".ruff_cache",
                                    )
                                )
                                and fp.suffix.lower()
                                not in (
                                    ".gguf",
                                    ".wav",
                                    ".pkl",
                                    ".zip",
                                    ".pyc",
                                    ".png",
                                    ".jpg",
                                    ".ico",
                                )
                            ):
                                res_c.append(
                                    f"=== FILE: {fp.relative_to(root).as_posix()} ===\n{_read_file_capped(fp, 25000)}"
                                )
                                paths_r.append(str(fp))
                                count += 1
                        return (res_c, paths_r)
                    else:
                        return ([f"=== FILE: {p_str} (NOT FOUND) ==="], [])
                except Exception as ex:
                    return ([f"=== FILE: {p_str} (ERROR: {ex}) ==="], [])

            if isinstance(requested, list):
                results_content = []
                paths_read = []
                for p in requested:
                    c_list, p_list = _read_path_entry(str(p))
                    results_content.extend(c_list)
                    paths_read.extend(p_list)
                combined = "\n\n".join(results_content)
                if trace_hook:
                    trace_hook("filesystem_read", {"ok": True, "paths": paths_read})
                return {"ok": True, "content": combined, "paths": paths_read}

            if not target_path.exists():
                # Fallback: if bare filename was passed, search for it across the project root
                req_str = str(requested)
                if "/" not in req_str and "\\" not in req_str:
                    matches = [
                        fp
                        for fp in root.rglob(req_str)
                        if fp.is_file()
                        and not any(
                            part in (".venv", ".git", "__pycache__", "node_modules")
                            for part in fp.parts
                        )
                    ]
                    if matches:
                        target_path = matches[0]
                if not target_path.exists():
                    return {
                        "ok": False,
                        "error": f"File not found: {requested}",
                        "path": str(target_path),
                    }

            if target_path.is_dir():
                c_list, p_list = _read_path_entry(str(requested))
                combined = "\n\n".join(c_list)
                if trace_hook:
                    trace_hook("filesystem_read", {"ok": True, "paths": p_list})
                return {"ok": True, "content": combined, "paths": p_list}

            content = _read_file_capped(target_path, 50000)
            if trace_hook:
                trace_hook("filesystem_read", {"ok": True, "path": str(target_path)})
            return {"ok": True, "content": content, "path": str(target_path)}

        elif operation == "write":
            content = str(params.get("content", ""))
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(content, encoding="utf-8")
            if trace_hook:
                trace_hook("filesystem_write", {"ok": True, "path": str(target_path)})
            return {"ok": True, "path": str(target_path)}

        elif operation == "patch":
            if not target_path.exists():
                return {
                    "ok": False,
                    "error": f"File not found for patching: {requested}",
                }

            old_str = str(params.get("old", params.get("old_string", "")))
            new_str = str(params.get("new", params.get("new_string", "")))

            content = target_path.read_text(encoding="utf-8")
            if old_str not in content:
                return {
                    "ok": False,
                    "error": "Surgical Error: 'old' not found in file content.",
                }

            # Precise single-occurrence check for safety
            if content.count(old_str) > 1 and not params.get("allow_multiple", False):
                return {
                    "ok": False,
                    "error": "Surgical Ambiguity: 'old' occurs multiple times. Provide more context.",
                }

            updated_content = content.replace(old_str, new_str)
            target_path.write_text(updated_content, encoding="utf-8")

            return {
                "ok": True,
                "replaced": 1
                if not params.get("allow_multiple")
                else content.count(old_str),
            }

        elif operation == "list":
            if not target_path.exists():
                return {
                    "ok": False,
                    "error": f"Directory not found: {requested}",
                    "path": str(target_path),
                }
            if not target_path.is_dir():
                return {
                    "ok": False,
                    "error": f"Not a directory: {requested}",
                    "path": str(target_path),
                }

            recursive = bool(params.get("recursive", False))
            entries = []
            try:
                iter_path = (
                    target_path.rglob("*") if recursive else target_path.iterdir()
                )
                for item in iter_path:
                    # BEGIN NEW FILTERS
                    # Exclude specific directories
                    excluded_dirs = {
                        ".venv",
                        ".git",
                        "__pycache__",
                        ".ruff_cache",
                        ".swarm_brain",
                        ".swarm_2027_backup",
                        "node_modules",
                    }
                    if recursive and any(
                        part in excluded_dirs
                        for part in item.relative_to(target_path).parts
                    ):
                        continue

                    # Exclude specific file extensions
                    excluded_extensions = {".gguf", ".wav", ".pkl", ".zip"}
                    if item.is_file() and item.suffix.lower() in excluded_extensions:
                        continue

                    # Exclude files larger than 5MB
                    max_file_size = 5 * 1024 * 1024  # 5MB
                    if item.is_file() and item.stat().st_size > max_file_size:
                        continue
                    # END NEW FILTERS
                    name = str(item.relative_to(root).as_posix())
                    if item.is_dir():
                        name += "/"
                    entries.append(name)
            except Exception as e:
                return {
                    "ok": False,
                    "error": f"Error listing directory: {e}",
                    "path": str(target_path),
                }
            return {"ok": True, "entries": entries}

        elif operation == "grep":
            pattern = str(params.get("pattern", ""))
            recursive = bool(params.get("recursive", True))
            matches = []

            def search_file(file_path: Path):
                try:
                    content = file_path.read_text(encoding="utf-8", errors="ignore")
                    for i, line in enumerate(content.splitlines(), 1):
                        if pattern in line:
                            rel_file = str(file_path.relative_to(root)).replace(
                                "\\", "/"
                            )
                            matches.append({"file": rel_file, "line": i, "text": line})
                except UnicodeDecodeError:
                    pass
                except Exception as e:
                    if not recursive:
                        raise e

            if target_path.is_file():
                search_file(target_path)
            elif target_path.is_dir():
                if recursive:
                    for p in target_path.rglob("*"):
                        if p.is_file() and not any(
                            part.startswith(".") or part in ("node_modules", ".venv")
                            for part in p.parts
                        ):
                            search_file(p)
                else:
                    for p in target_path.glob("*"):
                        if p.is_file():
                            search_file(p)

            return {"ok": True, "matches": matches}

        elif operation == "glob":
            pattern = str(params.get("pattern", "**/*")).replace("\\", "/").lstrip("/")
            recursive = bool(params.get("recursive", True))
            matches = []
            excluded_dirs = {
                ".venv",
                ".git",
                "__pycache__",
                ".ruff_cache",
                ".swarm_brain",
                ".swarm_2027_backup",
                "node_modules",
                "models",
                "data",
                "logs",
                ".venv",
            }
            excluded_extensions = {
                ".gguf",
                ".wav",
                ".pkl",
                ".zip",
                ".pyc",
                ".png",
                ".jpg",
                ".ico",
            }
            try:
                base = target_path if target_path.is_dir() else target_path.parent
                import fnmatch

                if recursive:
                    iterator = base.rglob("*")
                else:
                    iterator = base.glob("*")
                for p in iterator:
                    if p.is_dir():
                        continue
                    if any(part in excluded_dirs for part in p.parts):
                        continue
                    if p.suffix.lower() in excluded_extensions:
                        continue
                    try:
                        if p.stat().st_size > 5 * 1024 * 1024:
                            continue
                    except Exception:
                        continue
                    rel = p.relative_to(base).as_posix()
                    if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(
                        rel, pattern.lstrip("./")
                    ):
                        matches.append(str(p.relative_to(root).as_posix()))
            except Exception as e:
                return {
                    "ok": False,
                    "error": f"Error in glob: {e}",
                    "path": str(target_path),
                }
            matches.sort()
            if len(matches) > 200:
                matches = matches[:200]
            return {
                "ok": True,
                "matches": matches,
                "count": len(matches),
                "base": str(base.relative_to(root).as_posix()),
            }

        elif operation in ("scan_dir", "scandir", "list_dir", "walk"):
            # Alias: agents sometimes hallucinate this name; treat as list
            operation = "list"
            if not target_path.exists():
                return {
                    "ok": False,
                    "error": f"Directory not found: {requested}",
                    "path": str(target_path),
                }
            if not target_path.is_dir():
                return {
                    "ok": False,
                    "error": f"Not a directory: {requested}",
                    "path": str(target_path),
                }
            entries = []
            for p in target_path.iterdir():
                try:
                    entries.append(
                        {
                            "name": p.name,
                            "type": "dir" if p.is_dir() else "file",
                            "size": p.stat().st_size if p.is_file() else 0,
                        }
                    )
                except Exception:
                    continue
            return {"ok": True, "entries": entries, "note": "scan_dir aliased to list"}

        return {"ok": False, "error": f"Unknown operation: {operation}"}

    except Exception as e:
        logger.exception("Filesystem tool error")
        return {"ok": False, "error": str(e), "path": requested}
