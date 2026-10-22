"""End to end: one config used nearly every way notifig allows.

Files at two layers, env, CLI flags, sections, a dynamic section whose entries
get typed and un-typed, a `Choice` field, a `Link`, observers on the root and on
a nested section, a save and a reload. Each test drives one leg of that
lifecycle; together they cover the surface a real application touches.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Annotated, ClassVar

from pydantic import Field

from notifig import (
    Choice,
    CLIFlags,
    CommaSeparated,
    Config,
    ConfigDict,
    ConfigSection,
    Deleted,
    Link,
    Updated,
)
from notifig.section.dynamic import DynamicSection
from notifig.sources import save_config

from .conftest import (
    EntryConfig,
    TypedEntry,
    cli_list_values_need_ps_211,
    set_argv,
    write_files,
)


@dataclass(frozen=True)
class Backend:
    name: str


BACKENDS = {"memory": Backend("memory"), "redis": Backend("redis")}


class Api(ConfigSection):
    url: str = "https://default.test"
    port: Annotated[int, CLIFlags("p", "port")] = 8000
    backend: Annotated[Backend, Choice(BACKENDS)] = BACKENDS["memory"]
    model: str | None = None
    origins: CommaSeparated[str] = Field(default_factory=list)


class Webhooks(ConfigSection):
    """Reads the API's address from one place; never keeps a copy."""

    url: ClassVar[Link[str]] = Link("/api.url")
    enabled: bool = True


class Plugins(DynamicSection[EntryConfig]):
    pass


class Settings(Config):
    model_config = ConfigDict(
        env_prefix="APP_",
        env_nested_delimiter="__",
    )

    api: Api = Field(default_factory=Api)
    webhooks: Webhooks = Field(default_factory=Webhooks)
    plugins: Plugins = Field(default_factory=Plugins)


GLOBAL = """\
[api]
url = "https://global.test"
model = "from-global"

[plugins.alpha]
enabled = false
"""

LOCAL = """\
api:
  url: https://local.test
plugins:
  beta:
    enabled: true
"""


def build(tmp_path, monkeypatch, *argv: str) -> Settings:
    """Two files (global low, local high), env, then CLI on top."""
    global_cfg, local_cfg = write_files(
        tmp_path, **{"global.toml": GLOBAL, "local.yaml": LOCAL}
    )
    monkeypatch.setenv("APP_API__MODEL", "from-env")
    set_argv(monkeypatch, *argv)
    return Settings(config_files=[global_cfg, local_cfg])


def test_every_source_layers_in_its_documented_order(tmp_path, monkeypatch):
    settings = build(tmp_path, monkeypatch, "-p", "7100")

    # File layering: the later file wins, the earlier file's other keys survive.
    assert settings.api.url == "https://local.test"
    assert [str(path) for path in settings.config_files] == [
        str(tmp_path / "global.toml"),
        str(tmp_path / "local.yaml"),
    ]

    # env beats the files, CLI beats env and files.
    assert settings.api.model == "from-env"
    assert settings.api.port == 7100

    # Both plugin blocks survived the merge, from the two different files.
    assert set(dict(settings.plugins)) == {"alpha", "beta"}
    assert settings.plugins["alpha"].enabled is False
    assert settings.plugins["beta"].enabled is True


@cli_list_values_need_ps_211
def test_a_comma_separated_field_can_come_from_the_command_line(tmp_path, monkeypatch):
    settings = build(
        tmp_path,
        monkeypatch,
        "--api.origins",
        "https://a.test,https://b.test",
    )
    assert settings.api.origins == ["https://a.test", "https://b.test"]


def test_entries_are_untyped_then_typed_retyped_and_removed(tmp_path, monkeypatch):
    settings = build(tmp_path, monkeypatch)

    # A block is a live entry the moment it arrives, owned by nobody.
    assert type(settings.plugins["alpha"]) is EntryConfig
    assert settings.plugins["alpha"].owner is None

    heard: list = []

    @settings.plugins.on_change()
    def hear(_section, changes):
        heard.extend(changes)

    settings.plugins.set_type(alpha=TypedEntry)
    assert type(settings.plugins["alpha"]) is TypedEntry
    assert settings.plugins["alpha"].enabled is False  # adopted from the file
    assert isinstance(heard[-1], Updated)

    # Demote to raw data, then adopt it back: the value never gets lost.
    assert settings.plugins.remove_type("beta") == []
    assert "beta" in settings.plugins.unresolved
    settings.plugins.set_type(beta=TypedEntry)
    assert settings.plugins["beta"].enabled is True

    del settings.plugins.alpha
    assert "alpha" not in dict(settings.plugins)
    assert isinstance(heard[-1], Deleted)


def test_choice_and_link_see_the_same_live_values(tmp_path, monkeypatch):
    settings = build(tmp_path, monkeypatch)

    # Choice: the file says a name, the field holds the member.
    assert settings.api.backend is BACKENDS["memory"]
    settings.api.backend = "redis"
    assert settings.api.backend is BACKENDS["redis"]

    # Link: one value, two readers, following every write.
    assert settings.webhooks.url == "https://local.test"
    settings.api.url = "https://changed.test"
    assert settings.webhooks.url == "https://changed.test"


def test_observers_fire_for_sync_and_async_readers(tmp_path, monkeypatch):
    settings = build(tmp_path, monkeypatch)
    sync_heard: list = []
    async_heard: list = []

    @settings.on_change("api.port")
    def hear_sync(_section, changes):
        sync_heard.extend(change.path for change in changes)

    @settings.api.on_change("port")
    async def hear_async(_section, changes):
        async_heard.extend(change.path for change in changes)

    pending = settings.set("api.port", 9100)
    assert settings.api.port == 9100
    assert sync_heard == ["api.port"]  # sync ran on the write
    assert async_heard == []  # async is the caller's to await
    asyncio.run(_await_all(pending))
    assert async_heard == ["port"]  # relative to the section that heard it

    asyncio.run(settings.set_and_wait("api.port", 9200))
    assert sync_heard == ["api.port", "api.port"]
    assert async_heard == ["port", "port"]


async def _await_all(pending):
    for awaiting in pending:
        await awaiting


def test_the_config_saves_and_reloads_identically(tmp_path, monkeypatch):
    settings = build(tmp_path, monkeypatch)
    settings.plugins.set_type(alpha=TypedEntry)
    settings.api.backend = "redis"
    settings.api.origins = ["https://a.test"]

    saved = tmp_path / "saved.toml"
    save_config(settings.model_dump(mode="json", exclude_unset=True), saved)

    reloaded = Settings(config_files=[str(saved)], parse_cli=False)

    assert reloaded.api.url == settings.api.url
    assert reloaded.api.port == settings.api.port
    assert reloaded.api.backend is BACKENDS["redis"]  # the name came back
    assert reloaded.api.origins == ["https://a.test"]
    assert type(reloaded.plugins["alpha"]) is EntryConfig
    assert reloaded.plugins["alpha"].enabled is False
    # Internal switches are never written.
    assert reloaded.config_files == [saved.resolve()]
