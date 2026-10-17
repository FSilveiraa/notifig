"""The smoke suite: the behaviours the README promises, exercised end to end.

This is deliberately small and real — no mocks, no fixtures beyond an empty
`Config` — because a config library's core claim ("declare a tree, mutate it,
be told what changed") is testable without any of it. Bigger suites grow
outward from here per area.
"""

import asyncio
from typing import ClassVar

import pytest

from notifig import Config, ConfigSection
from notifig.section.dynamic import DynamicSection


class EntryConfig(ConfigSection):
    """The entry type the smoke suite's dynamic section holds: the shared
    `enabled` flag, plus a `ClassVar` stamp slot like a tool's config class
    carries - an untyped entry has no owner, a typed one does."""

    enabled: bool = True
    owner: ClassVar[str | None] = None


def make_settings():
    class InnerConfig(ConfigSection):
        token: str = "default-token"

    class ApiConfig(ConfigSection):
        url: str = "https://default.test"
        model: str | None = None
        inner: InnerConfig = InnerConfig()

    class PluginsConfig(DynamicSection[EntryConfig]):
        pass

    class Settings(Config):
        api: ApiConfig
        plugins: PluginsConfig

    # `parse_cli=False`: the smoke tests must not read pytest's own argv.
    return Settings(
        api={"url": "https://example.test/v1", "inner": {"token": "t"}},
        plugins={},
        parse_cli=False,
    )


def test_write_and_read_by_name():
    settings = make_settings()
    settings.api["url"] = "https://other.test/v1"
    assert settings.api["url"] == "https://other.test/v1"
    assert settings.api.url == settings.api["url"]


def test_write_and_read_by_dotted_path():
    """`[]` navigates: a dotted key is the chained/dotted write and read."""
    settings = make_settings()
    settings["api.url"] = "https://other.test/v1"
    assert settings["api.url"] == "https://other.test/v1"
    assert settings.api.url == settings["api.url"]
    assert settings["api"]["url"] == settings["api.url"]  # chains agree
    # deep paths too
    settings["api.inner.token"] = "t2"
    assert settings["api.inner.token"] == "t2"


def test_dotted_access_refuses_missing_levels_with_a_keyerror():
    settings = make_settings()
    with pytest.raises(KeyError):
        settings["api.nope"]  # no such field
    with pytest.raises(KeyError):
        settings["api.inner.nope"]
    with pytest.raises(KeyError):
        settings["nope.url"]  # no such intermediate section
    with pytest.raises(KeyError):
        settings["api.url.x"]  # the leaf is a str, not a section
    # and nothing was created
    assert "nope" not in settings.api.model_fields_set


def test_dotted_path_walk_and_set():
    settings = make_settings()
    settings.set("api.model", "test-model")
    assert settings.get("api.model") == "test-model"


def test_sync_observer_hears_the_change_with_its_path():
    settings = make_settings()
    heard = []

    @settings.on_change("api.model")
    def hear(_section, changes):
        heard.extend(change.path for change in changes)

    settings.api.model = "test-model"
    assert heard == ["api.model"]


def test_plain_assignment_reaches_only_sync_observers():
    settings = make_settings()
    settings.api.url = "https://changed.test"
    assert settings.api["url"] == "https://changed.test"


def test_async_observer_is_refused_by_plain_assignment_and_runs_via_set_and_wait():
    settings = make_settings()
    heard = []

    @settings.on_change("api.model")
    async def hear(_section, changes):
        heard.extend(change.path for change in changes)

    import pytest

    with pytest.raises(Exception, match="hear"):
        settings.api.model = "unapplied"  # nothing was applied

    assert settings.api.model is None
    asyncio.run(settings.set_and_wait(**{"api.model": "applied"}))
    assert settings.api.model == "applied"
    assert heard == ["api.model"]


def test_dynamic_section_types_an_entry_after_the_data_arrived():

    class EntryConfig(ConfigSection):
        enabled: bool = True
        owner: ClassVar[str | None] = None

    class GhostConfig(EntryConfig):
        pass

    class Plugins(DynamicSection[EntryConfig]):
        pass

    settings = make_settings()
    settings.plugins["ghost"] = {"enabled": False}
    entry = settings.plugins["ghost"]
    assert entry.enabled is False
    assert entry.owner is None

    settings.plugins.set_type(ghost=GhostConfig)
    typed = settings.plugins["ghost"]
    assert isinstance(typed, GhostConfig)
    assert typed.enabled is False
    assert typed.owner is None


def test_unset_removes_an_entry_and_notifies():
    settings = make_settings()
    settings.plugins["ghost"] = {"enabled": True}
    heard = []

    @settings.plugins.on_change()
    def hear(_section, changes):
        heard.extend(type(change).__name__ for change in changes)

    settings.plugins.unset("ghost")
    assert "ghost" not in dict(settings.plugins)
    assert "Deleted" in heard
