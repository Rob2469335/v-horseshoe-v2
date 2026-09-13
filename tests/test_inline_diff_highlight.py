"""Word-level (delta-style) highlighting in the inline diff.

`format_inline_diff(..., highlight=True)` bolds the changed spans of replaced
line pairs. The default (highlight=False) output is unchanged, so the existing
TestInlineDiff contract still holds.
"""

from __future__ import annotations

from organism_console.ui.live_stream import format_inline_diff


def test_default_format_unchanged():
    lines = format_inline_diff("x = 1\ny = 2\n", "x = 1\ny = 3\n")
    assert "[red]- y = 2[/red]" in lines
    assert "[green]+ y = 3[/green]" in lines


def test_highlight_bolds_changed_span():
    lines = format_inline_diff("y = 2\n", "y = 3\n", highlight=True)
    joined = "\n".join(lines)
    assert "[red]- y = [bold]2[/bold][/red]" in joined
    assert "[green]+ y = [bold]3[/bold][/green]" in joined


def test_highlight_escapes_markup():
    lines = format_inline_diff("[bold]x[/bold]\n", "[bold]y[/bold]\n", highlight=True)
    assert "\\[bold]" in "\n".join(lines)


def test_highlight_unequal_replace_falls_back_to_whole_line():
    lines = format_inline_diff("a\n", "b\nc\n", highlight=True)
    assert any(ln.startswith("[red]- a") for ln in lines)
    assert not any("[bold]" in ln for ln in lines)


def test_highlight_insert_and_delete_stay_plain():
    assert not any(
        "[bold]" in ln for ln in format_inline_diff("", "new\n", highlight=True)
    )
    assert not any(
        "[bold]" in ln for ln in format_inline_diff("gone\n", "", highlight=True)
    )
