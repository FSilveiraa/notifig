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

import pytest
from pydantic import ValidationError

from notifig import ConfigDict, PathNotFound
from notifig.exceptions import EntryRequiresData
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
    settings = make_settings()
    settings.plugins.set_type(fresh=TypedEntry)
    assert settings.plugins["fresh"].enabled is True
    # A default is not a value the user chose, so a save with
    # `exclude_unset=True` won't write it out.
    assert "fresh" not in settings.plugins.__pydantic_fields_set__


def test_set_type_for_an_unknown_name_builds_it_from_defaults():
    """`set_type(name, Model)` needs no prior data: a section entry is a
    BaseModel, so it can always be built from its own defaults."""
    settings = make_settings()
    settings.plugins.set_type(ghost=TypedEntry)
    assert type(settings.plugins["ghost"]) is TypedEntry
    assert settings.plugins["ghost"].enabled is True


def test_an_entry_name_cannot_carry_a_dot():
    """`section["a.b"]` navigates to `b` inside `a`, so an entry named `a.b`
    could never be read back. Both doors refuse: `set_type` and the config
    load."""
    settings = make_settings()
    with pytest.raises(ValueError, match="cannot be an entry name"):
        settings.plugins.set_type("a.b", TypedEntry)
    assert "a.b" not in dict(settings.plugins)
    assert "a" not in dict(settings.plugins)  # nothing half-created

    with pytest.raises(ValidationError):
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


def test_a_batch_is_one_notification_and_fails_partially():
    settings = make_settings(plugins_section=PreservingPlugins)
    settings.plugins["good"] = {"enabled": True}
    settings.plugins["needy"] = {"enabled": True}
    heard: list = []

    @settings.plugins.on_change()
    def hear(_section, changes):
        heard.append([change.path for change in changes])

    # NOTE: pinned behaviour — the notification rides the success path, so a
    #  failed batch notifies NOBODY even though the earlier entries applied.
    #  Whether a failed batch should notify what it did apply is an open design
    #  question, not settled here.
    with pytest.raises(EntryRequiresData):
        settings.plugins.set_type(good=TypedEntry, needy=NeedsData)
    assert heard == []
    assert type(settings.plugins["good"]) is TypedEntry


def test_a_typed_entrys_own_observers_hear_its_writes(settings):
    """An entry is a section: it has its own observers, addressed relatively."""
    settings.plugins["ghost"] = {"enabled": True}
    heard: list = []

    @settings.plugins["ghost"].on_change()
    def hear(_section, changes):
        heard.extend(change.path for change in changes)

    settings.plugins["ghost"].enabled = False
    assert heard == ["enabled"]
