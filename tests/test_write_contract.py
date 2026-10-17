"""The write contract: sync writers apply, notify, and hand back; plain
assignment refuses atomically when an async observer is on the path.

The before/after that motivated the contract: a forgotten `await` used to lose
the WRITE silently (a coroutine-never-awaited warning and nothing written).
Now the value always lands when a sync writer is used, the dunders refuse
BEFORE applying anything, and a no-op write never refuses — knowing what
you're doing (segmenting async and sync parts of the tree) costs nothing.
"""

import asyncio

import pytest

from notifig.exceptions import AsyncObserversNotCalled

from .conftest import make_settings


def test_a_sync_write_applies_and_returns_nothing_pending():
    settings = make_settings()
    assert settings.set(**{"api.model": "m"}) == []
    assert settings.api.model == "m"


def test_plain_assignment_refuses_before_applying_when_async_observers_reach():
    settings = make_settings()
    heard: list = []

    @settings.on_change("api.model")
    async def hear(_section, changes):
        heard.extend(change.path for change in changes)

    with pytest.raises(AsyncObserversNotCalled) as refused:
        settings.api.model = "unapplied"

    # Nothing was applied, nobody was notified, and the exception says both.
    assert settings.api.model is None
    assert heard == []
    assert "hear" in str(refused.value)
    assert "Nothing was applied" in str(refused.value)


def test_a_no_op_write_does_not_refuse_even_under_async_observers():
    settings = make_settings()
    settings.api.url = "https://example.test/v1"  # already the value
    # An async observer is on the path, but the write changes nothing:
    assert settings.api.url == "https://example.test/v1"


def test_set_returns_the_async_coroutines_for_the_caller_to_run():
    settings = make_settings()
    heard: list = []

    @settings.on_change("api.model")
    async def hear(_section, changes):
        heard.extend(change.path for change in changes)

    pending = settings.set(**{"api.model": "m"})
    assert settings.api.model == "m"  # the write itself is done
    assert heard == []  # the async observer has not run

    async def run_pending():
        for awaiting in pending:  # coroutines or futures: await works for both
            await awaiting

    asyncio.run(run_pending())
    assert heard == ["api.model"]


def test_set_and_wait_writes_and_runs_the_async_observer():
    settings = make_settings()
    heard: list = []

    @settings.on_change("api.model")
    async def hear(_section, changes):
        heard.extend(change.path for change in changes)

    asyncio.run(settings.set_and_wait(**{"api.model": "m"}))
    assert settings.api.model == "m"
    assert heard == ["api.model"]


def test_a_sync_observer_raising_does_not_undo_the_write():
    settings = make_settings()

    @settings.on_change("api.url")
    def broken(_section, changes):
        raise RuntimeError("observer failed")

    with pytest.raises(ExceptionGroup) as raised:
        settings.api.url = "https://other.test"
    assert isinstance(raised.value.exceptions[0], RuntimeError)
    assert "observer failed" in str(raised.value.exceptions[0])
    assert settings.api.url == "https://other.test"  # applied


def test_unset_removes_an_entry_with_a_deleted_notification(settings):
    settings.plugins["ghost"] = {"enabled": True}
    heard: list = []

    @settings.plugins.on_change()
    def hear(_section, changes):
        heard.extend(type(change).__name__ for change in changes)

    settings.plugins.unset("ghost")
    assert "ghost" not in dict(settings.plugins)
    assert "Deleted" in heard
