"""Tests for the SOTA CLI upgrade: edit-personalized AST symbol context.

Covers the dependency-free repo-map slice (aider-style, stdlib ast):
  - extract_symbol_map: pure signature extraction (classes/funcs/async/args)
  - module_name_for_path: dotted module name resolution
  - find_direct_importers: reverse-import lookup via the cached KnowledgeGraph
  - symbol_context_for_read: additive payload (.py only, degrades to {})
  - filesystem read integration: reading a .py file carries symbol_map
"""

from pathlib import Path

from swarm_os.lib.symbol_context import (
    extract_symbol_map,
    find_direct_importers,
    module_name_for_path,
    symbol_context_for_read,
)


class TestExtractSymbolMap:
    def test_class_and_methods(self):
        src = (
            "class Foo:\n"
            "    def bar(self, x):\n"
            "        return x\n"
            "    async def baz(self):\n"
            "        pass\n"
        )
        out = extract_symbol_map(src)
        assert "class Foo" in out
        assert "def bar(self, x)" in out
        assert "async def baz(self)" in out

    def test_top_level_function_with_varargs(self):
        src = "def f(a, *args, **kw):\n    pass\n"
        out = extract_symbol_map(src)
        assert "def f(a, *args, **kw)" in out

    def test_indentation_reflects_nesting(self):
        src = "class C:\n    def m(self):\n        pass\n"
        out = extract_symbol_map(src)
        lines = out.splitlines()
        assert lines[0] == "class C"
        assert lines[1].startswith("  ")

    def test_syntax_error_returns_empty(self):
        assert extract_symbol_map("def broken(:\n") == ""

    def test_empty_source_returns_empty(self):
        assert extract_symbol_map("") == ""

    def test_max_symbols_cap(self):
        src = "\n".join(f"def f{i}():\n    pass" for i in range(20))
        out = extract_symbol_map(src, max_symbols=5)
        assert len(out.splitlines()) == 5

    def test_no_bodies_leaked(self):
        src = "def f():\n    secret_body_line = 42\n    return secret_body_line\n"
        out = extract_symbol_map(src)
        assert "secret_body_line" not in out
        assert "def f()" in out


class TestModuleNameForPath:
    def test_nested_module(self, tmp_path: Path):
        p = tmp_path / "pkg" / "sub" / "mod.py"
        assert module_name_for_path(p, tmp_path) == "pkg.sub.mod"

    def test_outside_root_returns_empty(self, tmp_path: Path):
        other = tmp_path / "elsewhere"
        other.mkdir()
        p = other / "x.py"
        assert module_name_for_path(p, tmp_path / "root") == ""


class TestFindDirectImporters:
    def test_importer_found(self, tmp_path: Path):
        (tmp_path / "target.py").write_text("VALUE = 1\n", encoding="utf-8")
        (tmp_path / "user.py").write_text("import target\n", encoding="utf-8")
        (tmp_path / "unrelated.py").write_text("x = 1\n", encoding="utf-8")
        importers = find_direct_importers(tmp_path / "target.py", tmp_path)
        assert "user" in importers
        assert "unrelated" not in importers

    def test_from_import_found(self, tmp_path: Path):
        (tmp_path / "target.py").write_text("VALUE = 1\n", encoding="utf-8")
        (tmp_path / "user.py").write_text("from target import VALUE\n", encoding="utf-8")
        importers = find_direct_importers(tmp_path / "target.py", tmp_path)
        assert "user" in importers

    def test_no_importers_empty(self, tmp_path: Path):
        (tmp_path / "lonely.py").write_text("x = 1\n", encoding="utf-8")
        assert find_direct_importers(tmp_path / "lonely.py", tmp_path) == []


class TestSymbolContextForRead:
    def test_py_file_returns_symbol_map(self, tmp_path: Path):
        p = tmp_path / "m.py"
        content = "def hello(name):\n    return name\n"
        p.write_text(content, encoding="utf-8")
        ctx = symbol_context_for_read(p, content, tmp_path)
        assert "def hello(name)" in ctx.get("symbol_map", "")

    def test_non_py_returns_empty(self, tmp_path: Path):
        p = tmp_path / "notes.txt"
        assert symbol_context_for_read(p, "some text", tmp_path) == {}

    def test_importers_included(self, tmp_path: Path):
        (tmp_path / "target.py").write_text("def g():\n    pass\n", encoding="utf-8")
        (tmp_path / "user.py").write_text("import target\n", encoding="utf-8")
        content = (tmp_path / "target.py").read_text(encoding="utf-8")
        ctx = symbol_context_for_read(tmp_path / "target.py", content, tmp_path)
        assert "user" in ctx.get("importers", [])


class TestFilesystemReadIntegration:
    def test_read_py_carries_symbol_map(self, tmp_path: Path):
        from swarm_os.lib.mcp.filesystem import filesystem_handler

        f = tmp_path / "sample.py"
        f.write_text("class Widget:\n    def render(self):\n        pass\n", encoding="utf-8")
        res = filesystem_handler(
            {"operation": "read", "path": "sample.py"}, root=tmp_path
        )
        assert res["ok"] is True
        assert "class Widget" in res.get("symbol_map", "")
        assert "def render(self)" in res.get("symbol_map", "")
        # original content still present and unchanged
        assert "class Widget" in res["content"]

    def test_read_non_py_has_no_symbol_map(self, tmp_path: Path):
        from swarm_os.lib.mcp.filesystem import filesystem_handler

        f = tmp_path / "data.txt"
        f.write_text("hello world", encoding="utf-8")
        res = filesystem_handler({"operation": "read", "path": "data.txt"}, root=tmp_path)
        assert res["ok"] is True
        assert "symbol_map" not in res
