"""Dynamic sections: entries that exist before their type is known.

The rules pinned here are the ones the config-file scenarios depend on:
- A block becomes an entry the moment its data arrives (eager instantiation).
- A strict entry type refuses a block's surplus keys — a typo in a discovered
  plugin's config must be loud.
- A section may opt into `force_preserve_extra`: the entry type is copied with
  `extra="allow"`, so a block for a not-yet-typed entry keeps its settings
  until `set_type` re-validates them against the real model.
- `set_type` builds from what the entry already holds; a failing re-type leaves
  the entry and its data in place.
- `remove_type` demotes an entry to unresolved data (kept for round-tripping);
  `set_type` later adopts those values back.
- One batch is one notification.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic import Field, ValidationError

from notifig import Config, ConfigDict, PathNotFound
from notifig.exceptions import CantDeleteRetypeDeclaredField, EntryRequiresData
from notifig.section.dynamic import DynamicSection

from .conftest import EntryConfig, PreservingPlugins, TypedEntry, make_settings


class NeedsData(EntryConfig):
    """An entry model that demands a value the block doesn't carry. It extends
    `EntryConfig` so `enabled` is known and the ONLY validation error is the
    missing `url` — `EntryRequiresData` fires only when every error is a
    missing value; a mixed failure stays pydantic's."""

    url: str  # no default: an entry cannot be built without a value


class PermissiveEntry(EntryConfig):
    model_config = ConfigDict(extra="allow")


class PermissivePlugins(DynamicSection[PermissiveEntry]):
    pass


def test_a_block_becomes_an_entry_immediately():
    settings = make_settings()
    settings.plugins["ghost"] = {"enabled": True}
    entry = settings.plugins["ghost"]
    assert entry.enabled is True
    assert entry.owner is None  # no discovery has claimed it


def test_a_strict_section_refuses_a_blocks_surplus_keys():
    settings = make_settings()
    with pytest.raises(ValidationError):
        settings.plugins["ghost"] = {"enabled": True, "retries": 5}
    assert "ghost" not in dict(settings.plugins)


def test_force_preserve_extra_keeps_surplus_keys_until_the_type_arrives():
    settings = make_settings(plugins_section=PreservingPlugins)
    settings.plugins["ghost"] = {"enabled": False, "retries": 5}
    entry = settings.plugins["ghost"]
    # The placeholder is a copy of the declared type, not the declared type.
    assert "_Preserving" in type(entry).__name__
    assert entry.enabled is False
    assert entry.retries == 5

    settings.plugins.set_type(ghost=TypedEntry)
    typed = settings.plugins["ghost"]
    assert type(typed) is TypedEntry
    assert typed.enabled is False and typed.retries == 5  # adopted, re-validated


def test_a_permissive_entry_type_is_used_as_itself():
    """A section whose entry type already allows extras needs no copy."""
    settings = make_settings(plugins_section=PermissivePlugins)
    settings.plugins["ghost"] = {"enabled": False, "retries": 5}
    entry = settings.plugins["ghost"]
    assert type(entry) is PermissiveEntry
    assert entry.retries == 5


def test_set_type_retypes_an_unowned_block():
    settings = make_settings()
    settings.plugins["ghost"] = {"enabled": False}
    settings.plugins.set_type(ghost=TypedEntry)
    typed = settings.plugins["ghost"]
    assert type(typed) is TypedEntry
    assert typed.enabled is False  # the block's value, adopted
    assert typed.retries == 2  # the model's own default


def test_set_type_takes_one_positional_pair_like_set():
    settings = make_settings()
    settings.plugins.set_type("ghost", TypedEntry)
    assert type(settings.plugins["ghost"]) is TypedEntry


def test_set_type_keeps_an_entry_already_of_its_type():
    """Re-typing to the same class must not replace the object: the entry's own
    observers live on the instance, and replacing it would silently drop them."""
    settings = make_settings()
    settings.plugins["ghost"] = {"enabled": True}
    settings.plugins.set_type(ghost=TypedEntry)
    before = settings.plugins["ghost"]
    settings.plugins.set_type(ghost=TypedEntry)
    assert settings.plugins["ghost"] is before


def test_set_type_with_no_data_builds_from_defaults_and_is_not_user_set():
    """`set_type(name, Model)` needs no prior data: a section entry is a
    BaseModel, so it can always be built from its own defaults, and a default is
    not a value the user chose so `exclude_unset=True` omits it."""
    settings = make_settings()
    settings.plugins.set_type(fresh=TypedEntry)
    assert type(settings.plugins["fresh"]) is TypedEntry
    assert settings.plugins["fresh"].enabled is True
    assert "fresh" not in settings.plugins.__pydantic_fields_set__


def test_an_entry_name_cannot_carry_a_dot():
    """`section["a.b"]` navigates to `b` inside `a`, so an entry named `a.b`
    could never be read back: `set_type("a.b", ...)` walks `a` as a path and
    raises `PathNotFound`, and config load refuses `a.b` at the door."""
    settings = make_settings()
    with pytest.raises(PathNotFound):
        settings.plugins.set_type("a.b", TypedEntry)
    assert "a.b" not in dict(settings.plugins)
    assert "a" not in dict(settings.plugins)  # nothing half-created

    with pytest.raises(ValidationError, match="cannot be an entry name"):
        type(settings)(
            api={"url": "https://example.test/v1"},
            plugins={"a.b": {"enabled": True}},
            parse_cli=False,
        )


def test_an_entry_serializes_by_its_runtime_class():
    """A re-typed entry round-trips its own fields: serializing the section
    must use the class the value actually IS, not the declared entry type."""

    class RichEntry(EntryConfig):
        extra_field: str = "x"

    settings = make_settings()
    settings.plugins.set_type(rich=RichEntry)
    settings.plugins["rich"].extra_field = "kept"
    assert settings.model_dump(mode="json")["plugins"]["rich"] == {
        "enabled": True,
        "extra_field": "kept",
    }


def test_a_failing_set_type_leaves_the_entry_in_place():
    settings = make_settings()
    settings.plugins["ghost"] = {"enabled": True}
    with pytest.raises(EntryRequiresData) as raised:
        settings.plugins.set_type(ghost=NeedsData)
    assert raised.value.missing_fields == ["url"]
    assert raised.value.name == "ghost"
    # Still an unowned block, still holding its value.
    entry = settings.plugins["ghost"]
    assert entry.enabled is True
    assert entry.owner is None


def test_remove_type_demotes_the_entry_to_kept_data():
    settings = make_settings()
    settings.plugins["ghost"] = {"enabled": False}
    assert settings.plugins.remove_type("ghost") == []
    assert "ghost" not in dict(settings.plugins)
    assert "ghost" in settings.plugins.unresolved  # kept for round-tripping

    # Discovery later: the values come back from the unresolved data.
    settings.plugins.set_type(ghost=TypedEntry)
    assert settings.plugins["ghost"].enabled is False


def test_remove_type_for_an_unknown_name_raises():
    settings = make_settings()
    with pytest.raises(PathNotFound):
        settings.plugins.remove_type("ghost")


def test_a_failing_set_type_batch_applies_nothing_and_notifies_nobody():
    settings = make_settings(plugins_section=PreservingPlugins)
    settings.plugins["good"] = {"enabled": True}
    settings.plugins["needy"] = {"enabled": True}
    good_before = settings.plugins["good"]
    heard: list = []

    @settings.plugins.on_change()
    def hear(_section, changes):
        heard.append([change.path for change in changes])

    with pytest.raises(EntryRequiresData):
        settings.plugins.set_type(good=TypedEntry, needy=NeedsData)
    assert heard == []
    assert settings.plugins["good"] is good_before


def test_a_typed_entrys_own_observers_hear_its_writes(settings):
    """An entry is a section: it has its own observers, addressed relatively."""
    settings.plugins["ghost"] = {"enabled": True}
    heard: list = []

    @settings.plugins["ghost"].on_change()
    def hear(_section, changes):
        heard.extend(change.path for change in changes)

    settings.plugins["ghost"].enabled = False
    assert heard == ["enabled"]


def test_an_untyped_dynamic_section_keeps_entries_in_unresolved_until_typed():
    """When `T` is not a model (`DynamicSection[Any]`), blocks wait in
    `unresolved` at load time and on assignment, round-trip on dump, and can be
    updated, typed, or deleted directly from `unresolved`."""

    class RawPlugins(DynamicSection[Any]):
        declared: str = "fixed"

    class RawSettings(Config):
        plugins: RawPlugins = Field(default_factory=RawPlugins)

    settings = RawSettings(
        plugins={"declared": "custom", "alpha": {"enabled": False}},
        parse_cli=False,
    )
    assert settings.plugins.declared == "custom"
    assert settings.plugins.unresolved == ["alpha"]

    # Runtime write to an untyped entry updates `unresolved` in place.
    settings.plugins["alpha"] = {"enabled": True}
    settings.plugins["beta"] = {"enabled": False}
    assert settings.plugins.unresolved == ["alpha", "beta"]
    assert settings.plugins.model_dump(mode="json") == {
        "declared": "custom",
        "alpha": {"enabled": True},
        "beta": {"enabled": False},
    }

    # Deleting an unresolved entry removes its data.
    settings.plugins.delete("beta")
    assert settings.plugins.unresolved == ["alpha"]

    # `set_type` adopts the unresolved data into a live typed entry.
    settings.plugins.set_type(alpha=TypedEntry)
    assert settings.plugins.unresolved == []
    assert settings.plugins["alpha"].enabled is True


def test_declared_fields_on_a_dynamic_section_refuse_delete_set_type_and_remove_type():
    class MixedPlugins(DynamicSection[EntryConfig]):
        declared: str = "fixed"

    class MixedSettings(Config):
        plugins: MixedPlugins = Field(default_factory=MixedPlugins)

    settings = MixedSettings(parse_cli=False)
    with pytest.raises(CantDeleteRetypeDeclaredField):
        settings.plugins.delete("declared")
    with pytest.raises(CantDeleteRetypeDeclaredField):
        settings.plugins.set_type(declared=TypedEntry)
    with pytest.raises(CantDeleteRetypeDeclaredField):
        settings.plugins.remove_type("declared")


def test_set_type_reraises_validation_error_when_data_is_wrong_rather_than_missing():
    settings = make_settings()
    settings.plugins["ghost"] = {"enabled": True}
    with pytest.raises(ValidationError):
        settings.plugins.set_type(ghost=int)


def test_set_type_with_a_non_model_for_an_unknown_entry_raises_path_not_found():
    settings = make_settings()
    with pytest.raises(PathNotFound):
        settings.plugins.set_type(unknown=int)


def test_set_type_refuses_a_single_positional_argument():
    settings = make_settings()
    with pytest.raises(ValueError, match="exactly 2 or no positional"):
        settings.plugins.set_type("ghost")


def test_set_type_and_wait_and_remove_type_and_wait_run_async_observers():
    settings = make_settings()
    settings.plugins["ghost"] = {"enabled": False}
    heard: list[str] = []

    @settings.plugins.on_change()
    async def hear(_section, changes):
        heard.extend(type(change).__name__ for change in changes)

    asyncio.run(settings.plugins.set_type_and_wait(ghost=TypedEntry))
    assert type(settings.plugins["ghost"]) is TypedEntry
    assert heard == ["Updated"]

    asyncio.run(settings.plugins.remove_type_and_wait("ghost"))
    assert "ghost" in settings.plugins.unresolved
    assert heard == ["Updated", "Deleted"]
