"""Shared builders for the suite.

Every test builds its own tree; nothing here is shared mutable state. The tree
mirrors the shapes the library exists for: a root `Config`, a section holding a
nested section, and a dynamic section whose entries can be typed later.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import ClassVar

import pytest

from notifig import Config, ConfigSection
from notifig.section.dynamic import DynamicSection


class EntryConfig(ConfigSection):
    """What an entry looks like before its owning class is known: the shared
    `enabled` flag and an unset owner stamp."""

    enabled: bool = True
    owner: ClassVar[str | None] = None


class TypedEntry(EntryConfig):
    """What a plugin declares once discovery has named it."""

    retries: int = 2


class InnerConfig(ConfigSection):
    token: str = "default-token"


class ApiConfig(ConfigSection):
    """A plain section: one nested section, one optional field."""

    url: str = "https://default.test"
    model: str | None = None
    inner: InnerConfig = InnerConfig()


class PreservingPlugins(DynamicSection[EntryConfig], force_preserve_extra=True):
    """A dynamic section that keeps a block's surplus keys until the type arrives."""


def make_settings(*, plugins_section=None, **values):
    """Build `Settings(root) -> api -> inner` plus a dynamic `plugins`.

    `plugins_section` opts into a differently-postured dynamic section (e.g.
    `PreservingPlugins`); `values` overrides any top-level block.
    """
    base = plugins_section or DynamicSection[EntryConfig]

    class PluginsConfig(base):  # type: ignore[misc, valid-type]
        pass

    class Settings(Config):
        api: ApiConfig
        plugins: PluginsConfig

    data = {
        "api": {"url": "https://example.test/v1", "inner": {"token": "t"}},
        "plugins": {},
    }
    data.update(values)
    # `parse_cli=False`: no test may read pytest's own argv by accident.
    return Settings(**data, parse_cli=False)


def write_files(directory: Path, **files: str) -> list[str]:
    """Write `{name: content}` into `directory`, returning paths low -> high."""
    paths: list[str] = []
    for name, content in files.items():
        path = directory / name
        path.write_text(content)
        paths.append(str(path))
    return paths


def set_argv(monkeypatch: pytest.MonkeyPatch, *flags: str) -> None:
    """Make the next `Config` read these CLI flags."""
    monkeypatch.setattr(sys, "argv", ["prog", *flags])


@pytest.fixture
def settings():
    return make_settings()
