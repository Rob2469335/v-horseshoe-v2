"""Tests for the gated internet-driven self-improvement flow (`/upgrade`).

The SOTA research (Gödel Agent / propose-don't-apply, verifier-gated,
human-approved) says an agent should research and PROPOSE, and only modify the
working tree after an explicit human approval — never auto-apply.

These tests pin the gate contract:
  - the research phase runs READ-ONLY (force_readonly=True, no write intent);
  - declining the proposal applies ZERO changes (no write loop, no skill-memory
    execution);
  - approving the proposal embeds it in the apply objective for the verified
    write loop.
"""

from unittest.mock import MagicMock, patch

from organism_console import _commands_ai
import organism_console.loops.autonomous as _au_mod
import organism_console.core.self_improvement_agent as _sia_mod


def _make_ctx():
    state = MagicMock()
    state.entry_agent = "coordinator"
    state.active_agent = "coordinator"
    state.delegation_chain = []
    state.history = []
    state.save = MagicMock()

    ctx = MagicMock()
    ctx.state = state
    ctx.console = MagicMock()
    ctx.run_goal_loop = MagicMock()
    return ctx


def _fake_goal_loop(proposal, error=None):
    def _fake(goal, cmd_ctx, *, force_readonly=False):
        if error:
            raise error
        return proposal

    return _fake


class TestUpgradeResearchGate:
    def test_research_phase_is_forced_readonly(self):
        """The research phase must run through run_autonomous_goal_loop with
        force_readonly=True so proposal text can never be misclassified as a
        write goal (which would enter the file-mutating fix loop)."""
        ctx = _make_ctx()
        seen = {}

        def _fake(goal, cmd_ctx, *, force_readonly=False):
            seen["force_readonly"] = force_readonly
            seen["goal"] = goal
            return "proposal: add caching to orchestrator.py\nSource: arxiv 2604.14717"

        with (
            patch.object(_au_mod, "run_autonomous_goal_loop", _fake),
            patch("rich.prompt.Confirm") as m_confirm,
        ):
            m_confirm.ask.return_value = False
            _commands_ai.cmd_upgrade(ctx, [])

        assert seen.get("force_readonly") is True, (
            "research phase must force the read-only branch — otherwise the "
            "proposal objective could be misclassified as a write goal"
        )
        assert "research" in (seen.get("goal") or "").lower()

    def test_declined_proposal_applies_zero_changes(self):
        """Declining the proposal must not run the apply loop, must not run the
        skill-memory execution, and must not touch any files."""
        ctx = _make_ctx()
        with (
            patch.object(
                _au_mod, "run_autonomous_goal_loop", _fake_goal_loop("proposal: X")
            ),
            patch("rich.prompt.Confirm") as m_confirm,
            patch.object(_sia_mod, "SelfImprovementAgent") as m_sia,
        ):
            m_confirm.ask.return_value = False
            _commands_ai.cmd_upgrade(ctx, [])

        ctx.run_goal_loop.assert_not_called()
        m_sia.assert_not_called()
        m_confirm.ask.assert_called_once()

    def test_approved_proposal_embeds_in_apply_objective(self):
        """Approving the proposal must run the write goal loop with the approved
        proposal embedded in the apply objective."""
        ctx = _make_ctx()
        proposal = "proposal: add caching to orchestrator.py"
        with (
            patch.object(
                _au_mod, "run_autonomous_goal_loop", _fake_goal_loop(proposal)
            ),
            patch("rich.prompt.Confirm") as m_confirm,
            patch.object(_sia_mod, "SelfImprovementAgent") as m_sia,
        ):
            m_confirm.ask.return_value = True
            _commands_ai.cmd_upgrade(ctx, [])

        ctx.run_goal_loop.assert_called_once()
        apply_obj = ctx.run_goal_loop.call_args[0][0]
        assert "APPROVED PROPOSAL" in apply_obj
        assert proposal in apply_obj
        m_sia.assert_called_once()

    def test_empty_proposal_aborts_without_asking(self):
        """A research run that returns no proposal must abort before the gate —
        never ask, never apply."""
        ctx = _make_ctx()
        with (
            patch.object(_au_mod, "run_autonomous_goal_loop", _fake_goal_loop("  ")),
            patch("rich.prompt.Confirm") as m_confirm,
        ):
            _commands_ai.cmd_upgrade(ctx, [])

        m_confirm.ask.assert_not_called()
        ctx.run_goal_loop.assert_not_called()

    def test_research_failure_aborts_cleanly(self):
        """If the research run raises, cmd_upgrade must surface the error and
        stop — never fall through to a silent apply."""
        ctx = _make_ctx()
        with (
            patch.object(
                _au_mod,
                "run_autonomous_goal_loop",
                _fake_goal_loop(None, error=RuntimeError("boom")),
            ),
            patch("rich.prompt.Confirm") as m_confirm,
        ):
            _commands_ai.cmd_upgrade(ctx, [])

        m_confirm.ask.assert_not_called()
        ctx.run_goal_loop.assert_not_called()
        assert any(
            "Upgrade research failed" in str(c.args[0]) if c.args else False
            for c in ctx.console.print.call_args_list
        ), "research failure must be surfaced to the user"
