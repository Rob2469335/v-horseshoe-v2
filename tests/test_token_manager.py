"""Tests for TokenManager.reset_usage() and set_budget() (2026-09-07 fix:
without a reset, a hit budget permanently locks the backend until restart)."""
import pytest
from swarm_os.services.token_manager import TokenManager


@pytest.mark.asyncio
async def test_reset_usage_clears_total_and_returns_prior_value():
    tm = TokenManager(budget=20)
    await tm.add_usage("x" * 84)  # ~21 estimated tokens -> over budget
    assert await tm.is_exhausted() is True

    prev = await tm.reset_usage()

    assert prev == 22
    assert await tm.get_usage() == 0
    assert await tm.is_exhausted() is False


@pytest.mark.asyncio
async def test_reset_usage_on_fresh_manager_returns_zero():
    tm = TokenManager(budget=500_000)
    prev = await tm.reset_usage()
    assert prev == 0
    assert await tm.get_usage() == 0


@pytest.mark.asyncio
async def test_set_budget_raises_ceiling_and_clears_exhaustion():
    tm = TokenManager(budget=20)
    await tm.add_usage("x" * 84)  # over budget
    assert await tm.is_exhausted() is True

    await tm.set_budget(1000)

    assert await tm.get_budget() == 1000
    assert await tm.is_exhausted() is False


@pytest.mark.asyncio
async def test_set_budget_floors_negative_input_at_zero():
    tm = TokenManager(budget=100)
    await tm.set_budget(-50)
    assert await tm.get_budget() == 0


@pytest.mark.asyncio
async def test_set_budget_zero_means_immediately_exhausted():
    tm = TokenManager(budget=100)
    await tm.set_budget(0)
    # is_exhausted checks _total_used >= _budget; 0 >= 0 is True
    assert await tm.is_exhausted() is True