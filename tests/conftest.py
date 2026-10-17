"""Shared fixtures for the suite.

The tree used across tests mirrors the one the notification-reach measurements
were taken on: a root with an `api` section, a nested `inner` section under it,
and a dynamic `plugins` section holding `EntryConfig` entries. Fixtures build
it fresh per test; nothing here is shared mutable state.
"""

from typing import ClassVar

import pytest

from notifig import Config, ConfigSection
from notifig.section.dynamic import DynamicSection


class EntryConfig(ConfigSection):
    """The entry type the dynamic sections hold: the shared `enabled` flag,
    plus a `ClassVar` stamp slot — an untyped entry has no owner, a typed one
    does."""

    enabled: bool = True
    owner: ClassVar[str | None] = None


class TypedEntry(EntryConfig):
    """What a plugin would declare after discovery: one extra setting."""

    retries: int = 2


def make_settings(*, plugins_section: type[DynamicSection] | None = None):
    """A fresh three-level tree: root → api → inner, plus a dynamic section.

    `plugins_section` lets a test opt into a differently-postured dynamic
    section (e.g. `force_preserve_extra=True`); the default is the plain
    parametrized one."""

    class InnerConfig(ConfigSection):
        token: str = "default-token"

    class ApiConfig(ConfigSection):
        url: str = "https://default.test"
        model: str | None = None
        inner: InnerConfig = InnerConfig()

    base = plugins_section or DynamicSection[EntryConfig]

    class PluginsConfig(base):  # type: ignore[misc, valid-type]
        pass

    class Settings(Config):
        api: ApiConfig
        plugins: PluginsConfig

    # `parse_cli=False`: tests must not read pytest's own argv.
    return Settings(
        api={"url": "https://example.test/v1", "inner": {"token": "t"}},
        plugins={},
        parse_cli=False,
    )


@pytest.fixture
def settings():
    return make_settings()
