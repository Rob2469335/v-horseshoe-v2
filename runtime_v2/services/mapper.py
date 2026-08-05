import os
import ast
from pathlib import Path

def generate_repo_map(root_dir: str) -> str:
    """Generates a full markdown map of the codebase."""
    root_path = Path(root_dir).resolve()
    ignored_dirs = {".git", "node_modules", "venv", "__pycache__", ".swarm_brain", ".vscode", "dist", "build"}
    
    map_lines = []
    map_lines.append("# Codebase Map")
    map_lines.append("This is an architectural map of the project. It shows all directories, files, classes, and functions.\n")
    
    core_dirs = ["runtime_v2", "swarm_os", "organism_console", "config", "tests"]
    
    for core in core_dirs:
        core_path = root_path / core
        if not core_path.exists():
            continue
            
        for root, dirs, files in os.walk(core_path):
            # Filter out ignored directories
            dirs[:] = [d for d in dirs if d not in ignored_dirs and not d.startswith('.')]
        
            rel_dir = Path(root).relative_to(root_path)
            if str(rel_dir) == ".":
                depth = 0
                indent = ""
            else:
                depth = len(rel_dir.parts)
                indent = "  " * depth
                map_lines.append(f"{indent}📂 {rel_dir.name}/")
                
            for file in sorted(files):
                if file.endswith(".py"):
                    file_indent = "  " * (depth + 1)
                    map_lines.append(f"{file_indent}📄 {file}")
                    
                    # Parse python file for symbols
                    file_path = Path(root) / file
                    try:
                        content = file_path.read_text(encoding="utf-8")
                        tree = ast.parse(content)
                        
                        symbol_indent = "  " * (depth + 2)
                        for node in tree.body:
                            if isinstance(node, ast.ClassDef):
                                map_lines.append(f"{symbol_indent}class {node.name}:")
                                for class_node in node.body:
                                    if isinstance(class_node, ast.FunctionDef):
                                        map_lines.append(f"{symbol_indent}  def {class_node.name}()")
                            elif isinstance(node, ast.FunctionDef):
                                map_lines.append(f"{symbol_indent}def {node.name}()")
                    except Exception:
                        map_lines.append(f"{symbol_indent}  (Failed to parse)")
                elif file.endswith((".md", ".json", ".txt", ".yml", ".yaml", ".ps1")):
                    file_indent = "  " * (depth + 1)
                    map_lines.append(f"{file_indent}📄 {file}")
                
    return "\n".join(map_lines)

if __name__ == "__main__":
    print(generate_repo_map(os.getcwd()))
