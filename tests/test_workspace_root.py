import pytest
from swarm_os.lib.paths import project_root, agent_workspace_root
from swarm_os.lib.mcp.filesystem import filesystem_handler

def test_workspace_root_unset(monkeypatch):
    monkeypatch.delenv("SWARM_WORKSPACE_ROOT", raising=False)
    assert agent_workspace_root() == project_root()

def test_workspace_root_valid_temp(monkeypatch, tmp_path):
    monkeypatch.delenv("SWARM_WRITE_ROOT", raising=False)
    monkeypatch.setenv("SWARM_WORKSPACE_ROOT", str(tmp_path))
    assert agent_workspace_root() == tmp_path.resolve()
    
    # Verify file inside is readable/writable through the handler
    target = tmp_path / "test.txt"
    # Write
    res = filesystem_handler({"operation": "write", "path": str(target), "content": "hello"}, root=project_root())
    assert res.get("ok") is True
    assert target.read_text() == "hello"
    
    # Read
    res2 = filesystem_handler({"operation": "read", "path": str(target)}, root=project_root())
    assert res2.get("ok") is True
    assert res2.get("content") == "hello"

def test_workspace_root_refuses_outside(monkeypatch, tmp_path):
    monkeypatch.setenv("SWARM_WORKSPACE_ROOT", str(tmp_path))
    
    # Attempt to read a file outside the workspace root
    outside_file = project_root() / "pytest.ini"
    res = filesystem_handler({"operation": "read", "path": str(outside_file)}, root=project_root())
    assert res.get("ok") is False
    assert "Path is outside sandbox" in res.get("error", "")

def test_workspace_root_invalid(monkeypatch, tmp_path):
    # Relative path
    monkeypatch.setenv("SWARM_WORKSPACE_ROOT", "relative/path")
    with pytest.raises(ValueError, match="must be absolute"):
        agent_workspace_root()
        
    # Non-existent path
    non_existent = tmp_path / "does_not_exist"
    monkeypatch.setenv("SWARM_WORKSPACE_ROOT", str(non_existent))
    with pytest.raises(ValueError, match="must be an existing directory"):
        agent_workspace_root()
        
    # Not a directory
    test_file = tmp_path / "file.txt"
    test_file.touch()
    monkeypatch.setenv("SWARM_WORKSPACE_ROOT", str(test_file))
    with pytest.raises(ValueError, match="must be an existing directory"):
        agent_workspace_root()

def test_workspace_root_with_write_root(monkeypatch, tmp_path):
    monkeypatch.setenv("SWARM_WORKSPACE_ROOT", str(tmp_path))
    write_sub = tmp_path / "allowed_write"
    write_sub.mkdir()
    monkeypatch.setenv("SWARM_WRITE_ROOT", str(write_sub))
    
    # Can write inside SWARM_WRITE_ROOT
    inside_write = write_sub / "test.txt"
    res = filesystem_handler({"operation": "write", "path": str(inside_write), "content": "ok"}, root=project_root())
    assert res.get("ok") is True
    
    # CANNOT write outside SWARM_WRITE_ROOT but inside SWARM_WORKSPACE_ROOT
    outside_write = tmp_path / "test.txt"
    res2 = filesystem_handler({"operation": "write", "path": str(outside_write), "content": "no"}, root=project_root())
    assert res2.get("ok") is False
    assert "outside SWARM_WRITE_ROOT" in res2.get("error", "")

