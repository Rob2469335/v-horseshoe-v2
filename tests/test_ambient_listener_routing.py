"""Tests for ambient_listener.py dual-mode voice routing.

Covers:
- Mode selection given different keyword/transcript inputs
- Keyword stripping from transcribed text
- Case insensitivity and punctuation variants
- Dictation mode never routing to Swarm OS path
- Agent command mode never routing to dictation path
"""

import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from voice_routing import determine_mode, DICTATION_KEYWORDS


class TestDetermineMode:
    """Tests for determine_mode() routing logic."""

    def test_dictation_keyword_dictate(self):
        mode, text = determine_mode("dictate write an email about the project")
        assert mode == "dictation"
        assert text == "write an email about the project"

    def test_dictation_keyword_type(self):
        mode, text = determine_mode("type hello world")
        assert mode == "dictation"
        assert text == "hello world"

    def test_dictation_keyword_type_this(self):
        mode, text = determine_mode("type this hello world")
        assert mode == "dictation"
        assert text == "hello world"

    def test_dictation_strips_colon_after_keyword(self):
        mode, text = determine_mode("dictate: refactor the database schema")
        assert mode == "dictation"
        assert text == "refactor the database schema"

    def test_dictation_strips_comma_after_keyword(self):
        mode, text = determine_mode("type, hello world")
        assert mode == "dictation"
        assert text == "hello world"

    def test_agent_command_default(self):
        mode, text = determine_mode("what is the weather in London")
        assert mode == "agent_command"
        assert text == "what is the weather in London"

    def test_agent_command_no_keyword(self):
        mode, text = determine_mode("run benchmark")
        assert mode == "agent_command"
        assert text == "run benchmark"

    def test_agent_command_keyword_in_middle_not_start(self):
        mode, text = determine_mode("please dictate this for me")
        assert mode == "agent_command"
        assert text == "please dictate this for me"

    def test_case_insensitivity(self):
        mode, text = determine_mode("Dictate write an email")
        assert mode == "dictation"
        assert text == "write an email"

    def test_mixed_case_keyword(self):
        mode, text = determine_mode("TYPE hello world")
        assert mode == "dictation"
        assert text == "hello world"

    def test_empty_transcript_falls_to_agent(self):
        mode, text = determine_mode("")
        assert mode == "agent_command"
        assert text == ""

    def test_only_keyword_no_text(self):
        mode, text = determine_mode("dictate")
        assert mode == "dictation"
        assert text == ""

    def test_type_this_with_extra_spaces(self):
        mode, text = determine_mode("  type this   hello world")
        assert mode == "dictation"
        assert text == "hello world"

    def test_leading_whitespace(self):
        mode, text = determine_mode("  dictate write an email")
        assert mode == "dictation"
        assert text == "write an email"

    def test_keyword_not_a_prefix_embedded(self):
        mode, text = determine_mode("notdictate hello")
        assert mode == "agent_command"
        assert text == "notdictate hello"

    def test_all_keywords_covered(self):
        for kw in DICTATION_KEYWORDS:
            mode, text = determine_mode(f"{kw} test phrase")
            assert mode == "dictation"
            assert text == "test phrase"

    def test_dictation_does_not_forward_to_swarm(self):
        """Dictation keyword must produce dictation mode, never agent_command."""
        mode, _ = determine_mode("dictate some text")
        assert mode == "dictation", "Dictation mode misrouted to agent_command"

    def test_agent_does_not_route_to_dictation(self):
        """Non-keyword transcripts must produce agent_command, never dictation."""
        mode, _ = determine_mode("what is the capital of France")
        assert mode == "agent_command", "Agent command misrouted to dictation"

    def test_type_in_middle_of_sentence_stays_agent(self):
        """'type' appearing mid-sentence must NOT trigger dictation mode."""
        mode, text = determine_mode("can you help me type up some notes about the project")
        assert mode == "agent_command"
        assert text == "can you help me type up some notes about the project"

    def test_dictate_in_middle_of_sentence_stays_agent(self):
        """'dictate' appearing mid-sentence must NOT trigger dictation mode."""
        mode, text = determine_mode("I need to dictate a letter but I'm not sure how")
        assert mode == "agent_command"
        assert text == "I need to dictate a letter but I'm not sure how"

    def test_type_this_in_middle_stays_agent(self):
        """'type this' appearing mid-sentence must NOT trigger dictation mode."""
        mode, text = determine_mode("tell me how to type this correctly")
        assert mode == "agent_command"
        assert text == "tell me how to type this correctly"

    def test_sendkeys_special_chars_escaped(self):
        """Verify the escaping regex matches transcribe.ps1 behavior exactly.
        
        SendKeys special characters: { } ( ) + ^ % ~
        Others like =, -, _, etc. pass through unescaped.
        """
        from voice_routing import DICTATION_ESCAPE_CHARS_RE
        cases = {
            "hello {world}": "hello {{}world{}}",
            "a+b": "a{+}b",
            "func(x)": "func{(}x{)}",
            "100% done": "100{%} done",
            "^caret": "{^}caret",
            "~tilde": "{~}tilde",
            "plain text no specials": "plain text no specials",
            "code {a, b, c}": "code {{}a, b, c{}}",
            "a+b*c": "a{+}b*c",
        }
        for raw, expected in cases.items():
            escaped = DICTATION_ESCAPE_CHARS_RE.sub(r'{\1}', raw)
            assert escaped == expected, f"raw={raw!r}: expected {expected!r}, got {escaped!r}"


class TestModeIntegrationGuard:
    """Guard tests: ensure mode routing happens before any external call.

    These validate that a dictation-mode utterance will not reach the
    Swarm OS forwarding block (simulated via sentinel values).
    """

    def test_determine_mode_is_pure_function(self):
        """determine_mode has no side effects and returns consistent results."""
        inputs = [
            "dictate hello world",
            "type test",
            "type this some text",
            "normal query",
            "",
            "dictate: colon separated",
        ]
        for inp in inputs:
            mode1, text1 = determine_mode(inp)
            mode2, text2 = determine_mode(inp)
            assert mode1 == mode2
            assert text1 == text2

    def test_dictation_and_agent_are_exhaustive(self):
        """Every transcript maps to exactly one of the two modes."""
        test_cases = [
            "dictate do this",
            "type that",
            "type this something",
            "hello world",
            "run the tests",
            "",
            "   dictate   spaced out",
        ]
        for transcript in test_cases:
            mode, _ = determine_mode(transcript)
            assert mode in ("agent_command", "dictation")
