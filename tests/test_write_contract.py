"""The write contract.

A sync writer applies, notifies, and hands the async observers back; plain
assignment refuses BEFORE applying when an async observer is on the path, so a
forgotten `await` can never lose the write silently. A write that changes
nothing notifies nobody and therefore never refuses.
"""

from __future__ import annotations

import asyncio

import pytest

from notifig import PathNotFound
from notifig.exceptions import AsyncObserversNotCalled, CantDeleteRetypeDeclaredField

from .conftest import make_settings


def test_a_sync_write_applies_and_returns_nothing_pending():
    settings = make_settings()
    assert settings.set(**{"api.model": "m"}) == []
    assert settings.api.model == "m"


def test_set_takes_one_positional_pair_or_keywords_but_not_a_bare_path():
    settings = make_settings()
    settings.set("api.model", "m")
    assert settings.api.model == "m"
    settings.set(**{"api.model": "m2", "api.url": "https://other.test"})
    assert (settings.api.model, settings.api.url) == ("m2", "https://other.test")
    with pytest.raises(ValueError, match="exactly 2 or no positional"):
        settings.set("api.model")


def test_plain_assignment_refuses_before_applying_when_async_observers_reach():
    settings = make_settings()
    heard: list = []

    @settings.on_change("api.model")
    async def hear(_section, changes):
        heard.extend(change.path for change in changes)

    with pytest.raises(AsyncObserversNotCalled) as refused:
        settings.api.model = "unapplied"

    assert settings.api.model is None
    assert heard == []
    assert "hear" in str(refused.value)
    assert "Nothing was applied" in str(refused.value)


def test_the_refusal_applies_to_the_dict_door_too():
    settings = make_settings()
    heard: list = []

    @settings.on_change("api.model")
    async def hear(_section, changes):
        heard.extend(change.path for change in changes)

    with pytest.raises(AsyncObserversNotCalled):
        settings["api.model"] = "unapplied"
    assert settings.api.model is None
    assert heard == []
    # The sync case lands where plain assignment puts it.
    settings["api.url"] = "https://other.test"
    assert settings.api.url == "https://other.test"


def test_a_no_op_write_does_not_refuse_even_under_async_observers():
    settings = make_settings()
    heard: list = []

    @settings.on_change("api.url")
    async def hear(_section, changes):
        heard.extend(change.path for change in changes)

    settings.api.url = "https://example.test/v1"  # already the value
    assert settings.api.url == "https://example.test/v1"
    assert heard == []


def test_a_no_op_write_of_raw_data_over_an_equal_entry_keeps_the_entry():
    """The raw data builds a new entry, which holds the same data as the live
    one but none of its private bookkeeping. The two must still compare equal,
    or the write would refuse, notify, and replace the entry with its
    observers."""
    settings = make_settings()
    settings.plugins["ghost"] = {"enabled": True}
    entry = settings.plugins["ghost"]
    heard: list = []

    @settings.plugins.on_change()
    async def hear(_section, changes):
        heard.extend(change.path for change in changes)

    settings.plugins["ghost"] = {"enabled": True}  # already the value
    assert settings.plugins["ghost"] is entry
    assert heard == []


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
        for awaiting in pending:
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


def test_a_raising_sync_observer_does_not_undo_the_write():
    settings = make_settings()

    @settings.on_change("api.url")
    def broken(_section, changes):
        raise RuntimeError("observer failed")

    with pytest.raises(ExceptionGroup) as raised:
        settings.api.url = "https://other.test"
    assert isinstance(raised.value.exceptions[0], RuntimeError)
    assert "observer failed" in str(raised.value.exceptions[0])
    assert settings.api.url == "https://other.test"  # applied


def test_observers_are_isolated_from_each_others_failures():
    settings = make_settings()
    heard: list = []

    @settings.on_change("api.url")
    def broken(_section, changes):
        raise RuntimeError("first")

    @settings.on_change("api.url")
    def second(_section, changes):
        heard.extend(change.path for change in changes)

    with pytest.raises(ExceptionGroup):
        settings.api.url = "https://other.test"
    assert heard == ["api.url"]


def test_a_raising_async_observer_surfaces_from_set_and_wait():
    settings = make_settings()

    @settings.on_change("api.model")
    async def broken(_section, changes):
        raise RuntimeError("async failed")

    with pytest.raises(ExceptionGroup) as raised:
        asyncio.run(settings.set_and_wait(**{"api.model": "m"}))
    assert "async failed" in str(raised.value.exceptions[0])
    assert settings.api.model == "m"  # applied; the observer's failure is its own


def test_a_declared_field_is_not_deletable(settings):
    with pytest.raises(CantDeleteRetypeDeclaredField, match="declared field"):
        del settings.api.url
    assert settings.api.url == "https://example.test/v1"
    with pytest.raises(PathNotFound):
        settings.api.delete("nope")


def test_delete_removes_an_entry_with_a_deleted_notification(settings):
    settings.plugins["ghost"] = {"enabled": True}
    heard: list = []

    @settings.plugins.on_change()
    def hear(_section, changes):
        heard.extend(type(change).__name__ for change in changes)

    settings.plugins.delete("ghost")
    assert "ghost" not in dict(settings.plugins)
    assert heard == ["Deleted"]


def test_delitem_and_delete_and_wait_remove_dynamic_entries(settings):
    settings.plugins["alpha"] = {"enabled": True}
    settings.plugins["beta"] = {"enabled": True}
    del settings["plugins.alpha"]
    assert "alpha" not in dict(settings.plugins)

    heard: list[str] = []

    @settings.plugins.on_change()
    async def hear(_section, changes):
        heard.extend(change.path for change in changes)

    asyncio.run(settings.plugins.delete_and_wait("beta"))
    assert "beta" not in dict(settings.plugins)
    assert heard == ["beta"]


def test_del_and_delitem_refuse_while_an_async_observer_reaches_the_deletion(
    settings,
):
    settings.plugins["ghost"] = {"enabled": True}

    @settings.plugins.on_change()
    async def hear(_section, changes): ...

    with pytest.raises(AsyncObserversNotCalled):
        del settings.plugins.ghost
    with pytest.raises(AsyncObserversNotCalled):
        del settings.plugins["ghost"]
    assert settings.plugins["ghost"].enabled is True  # nothing was removed
