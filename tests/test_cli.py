"""CLI flags: the automatic names, the declared shortcuts, and the collisions.

`config.py` builds pydantic-settings' `cli_shortcuts` from each field's
`CLIFlags`, so this file pins the flag spelling a user actually gets: kebab-case
for a field, dotted for a nested one, one dash for a one-character shortcut.
"""

from __future__ import annotations

from typing import Annotated

import pytest
from pydantic import Field

from notifig import CLIFlags, CommaSeparated, Config, ConfigSection

from .conftest import cli_list_values_need_ps_211, set_argv


class Cli(Config):
    port: Annotated[int, CLIFlags("p", "port")] = 8000
    name: str = "unnamed"
    enabled: bool = False
    tags: CommaSeparated[str] = []


def test_a_declared_shortcut_is_one_dash_or_two(monkeypatch):
    set_argv(monkeypatch, "-p", "9000")
    assert Cli().port == 9000
    set_argv(monkeypatch, "--port", "9001")
    assert Cli().port == 9001


def test_a_field_is_reachable_by_its_own_name(monkeypatch):
    set_argv(monkeypatch, "--name", "from-cli")
    assert Cli().name == "from-cli"


def test_booleans_are_implicit_flags(monkeypatch):
    set_argv(monkeypatch, "--enabled")
    assert Cli().enabled is True
    set_argv(monkeypatch, "--no-enabled")
    assert Cli().enabled is False


@cli_list_values_need_ps_211
def test_a_comma_separated_field_takes_one_flag(monkeypatch):
    set_argv(monkeypatch, "--tags", "a,b")
    assert Cli().tags == ["a", "b"]


def test_two_fields_claiming_one_flag_is_an_error():
    class Clashing(Config):
        first: Annotated[int, CLIFlags("d")] = 0
        second: Annotated[int, CLIFlags("d")] = 0

    with pytest.raises(ValueError, match="claimed by both"):
        Clashing(parse_cli=False)


def test_parse_cli_false_ignores_the_arguments(monkeypatch):
    set_argv(monkeypatch, "-p", "9000")
    assert Cli(parse_cli=False).port == 8000


def test_a_reserved_shortcut_is_refused_when_declared():
    """argparse owns `-h`/`--help` on every parser pydantic-settings builds, so
    the refusal has to happen here — at the annotation — or it surfaces later as
    "conflicting option string" on a config class the author never touched."""
    for name in ("h", "help"):
        with pytest.raises(ValueError, match="reserved by the CLI parser"):
            CLIFlags(name)
    with pytest.raises(ValueError, match="reserved by the CLI parser"):
        CLIFlags(["good", "help"])


# --- nested fields ------------------------------------------------------------


class Inner(ConfigSection):
    token: str = "t"


class Api(ConfigSection):
    url: str = "https://default.test"
    inner: Inner = Field(default_factory=Inner)


class Nested(Config):
    api: Api = Field(default_factory=Api)


def test_a_nested_field_is_addressed_by_its_dotted_path(monkeypatch):
    set_argv(monkeypatch, "--api.url", "https://cli.test")
    assert Nested().api.url == "https://cli.test"


def test_a_deeply_nested_field_carries_the_whole_path(monkeypatch):
    set_argv(monkeypatch, "--api.inner.token", "from-cli")
    assert Nested().api.inner.token == "from-cli"


def test_a_shortcut_on_a_nested_field_can_shorten_the_path(monkeypatch):
    """The shortcut lives on the field, wherever the field lives — so a section
    can give `-u` to its `url` and the root needs to know nothing about it."""

    class ApiWithShortcut(ConfigSection):
        url: Annotated[str, CLIFlags("u")] = "https://default.test"

    class ShortNested(Config):
        api: ApiWithShortcut = Field(default_factory=ApiWithShortcut)

    set_argv(monkeypatch, "-u", "https://short.test")
    assert ShortNested().api.url == "https://short.test"
