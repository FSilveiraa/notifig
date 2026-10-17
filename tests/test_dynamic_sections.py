"""Dynamic sections: eager entry instantiation, the extras posture, re-typing,
and the unowned-block rules.

The rules pinned here are the ones the config-file scenarios depend on:
- A block becomes an entry the moment its data arrives (eager instantiation).
- A section whose entry type is strict refuses a block's surplus keys — a
  typo in a discovered plugin's config must be loud.
- A section may opt into `force_preserve_extra`: the entry type is copied with
  `extra="allow"`, so a block for a not-yet-typed entry keeps its settings
  until `set_type` re-validates them against the real model.
- `set_type` builds from what the entry already holds; a failing re-type
  leaves the entry and its data in place (no destruction on refusal).
- `remove_type` demotes the entry to unresolved data (kept for round-tripping,
  reported as preserved); `set_type` later adopts those values back.
- One batch is one notification; a failing entry in a batch leaves the earlier
  entries applied, like a `set` whose later path fails.
"""

import pytest
from pydantic import ValidationError

from notifig import ConfigDict
from notifig.exceptions import EntryRequiresData
from notifig.section.dynamic import DynamicSection

from .conftest import EntryConfig, TypedEntry, make_settings


class NeedsData(EntryConfig):
    """An entry model that demands a setting the block doesn't carry. It
    extends `EntryConfig` so `enabled` is a known field and the ONLY
    validation error is the missing `url` — `EntryRequiresData` fires when
    every error is a missing value; a mixed failure stays pydantic's."""

    url: str  # no default: an entry cannot be built without a value


class DynamicSectionWithFlag(DynamicSection[EntryConfig], force_preserve_extra=True):
    pass


class PermissiveEntry(EntryConfig):
    model_config = ConfigDict(extra="allow")


class PermissivePlugins(DynamicSection[PermissiveEntry]):
    pass


def test_a_block_becomes_an_entry_immediately():
    """A block arriving with the config data becomes an entry at once —
    it does not wait as written for its type."""
    settings = make_settings()
    settings.plugins["ghost"] = {"enabled": True}
    entry = settings.plugins["ghost"]
    assert entry.enabled is True
    assert entry.owner is None  # stamped by no one yet: an unowned block


def test_a_strict_section_refuses_a_blocks_surplus_keys():
    settings = make_settings()  # plain parametrized section: strict
    with pytest.raises(ValidationError):
        settings.plugins["ghost"] = {"enabled": True, "retries": 5}
    assert "ghost" not in dict(settings.plugins)


def test_force_preserve_extra_keeps_surplus_keys_until_the_type_arrives():
    settings = make_settings(plugins_section=DynamicSectionWithFlag)
    settings.plugins["ghost"] = {"enabled": False, "retries": 5}
    entry = settings.plugins["ghost"]
    # The placeholder is the copied type, not the declared one, and it holds
    # the user's setting that no declared field knows about.
    assert "_Preserving" in type(entry).__name__
    assert entry.enabled is False
    assert entry.retries == 5

    settings.plugins.set_type(ghost=TypedEntry)
    typed = settings.plugins["ghost"]
    assert type(typed) is TypedEntry
    assert typed.enabled is False and typed.retries == 5  # adopted, re-validated


def test_an_already_permissive_entry_type_is_used_as_itself():
    """A section whose parameter already allows extras needs no copy: the
    entry is the declared class itself, not a synthetic `_Preserving`."""
    settings = make_settings(plugins_section=PermissivePlugins)
    settings.plugins["ghost"] = {"enabled": False, "retries": 5}
    entry = settings.plugins["ghost"]
    assert type(entry) is PermissiveEntry
    assert entry.retries == 5


def test_set_type_retypes_an_unowned_block_and_stamps_the_classvar():
    settings = make_settings()
    settings.plugins["ghost"] = {"enabled": False}
    settings.plugins.set_type(ghost=TypedEntry)
    typed = settings.plugins["ghost"]
    assert type(typed) is TypedEntry
    assert typed.enabled is False  # the block's value, adopted


def test_an_entry_name_cannot_carry_a_dot():
    """`section["a.b"]` navigates to `b` inside `a`, so an entry named `a.b`
    could never be read back — by `[]`, as an attribute, or by a dotted path.
    Both doors refuse: `set_type` (the write path) and the config load
    (a block whose key carries a dot is a load error, not a kept object)."""
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
    """A subclassed entry (re-typed through `set_type`) round-trips its own
    fields: serializing the section must serialize the value by the class it
    actually IS, not the section's declared entry type. This is the behaviour
    `ConfigDict.polymorphic_serialization` was once thought to provide - the
    probe showed pydantic already does it for our shapes, so the flag was
    removed; this test is what guards against re-adding it (or a pydantic
    change) wrongly."""

    class RichEntry(EntryConfig):
        extra_field: str = "x"

    settings = make_settings()
    settings.plugins.set_type(rich=RichEntry)
    settings.plugins["rich"].extra_field = "kept"
    serialized = settings.model_dump(mode="json")["plugins"]["rich"]
    assert serialized == {"enabled": True, "extra_field": "kept"}


def test_a_failing_set_type_leaves_the_entry_in_place():
    """Refusal must not destroy the entry — the data is the user's, and the
    next (fixed) `set_type` has to find it."""
    settings = make_settings()
    settings.plugins["ghost"] = {"enabled": True}
    with pytest.raises(EntryRequiresData) as raised:
        settings.plugins.set_type(ghost=NeedsData)
    assert raised.value.missing_fields == ["url"]
    # Still an unowned block, still holding its value.
    entry = settings.plugins["ghost"]
    assert entry.enabled is True
    assert entry.owner is None


def test_remove_type_demotes_the_entry_to_kept_data(settings):
    settings.plugins["ghost"] = {"enabled": False}
    assert settings.plugins.remove_type("ghost") == []
    assert "ghost" not in dict(settings.plugins)
    assert "ghost" in settings.plugins.unresolved  # kept for round-tripping

    # Discovery later: the values come back from the unresolved data.
    settings.plugins.set_type(ghost=TypedEntry)
    assert settings.plugins["ghost"].enabled is False


def test_a_batch_is_one_notification_and_fails_partially():
    settings = make_settings(plugins_section=DynamicSectionWithFlag)
    settings.plugins["good"] = {"enabled": True}
    settings.plugins["needy"] = {"enabled": True}
    heard: list = []

    @settings.plugins.on_change()
    def hear(_section, changes):
        heard.append([change.path for change in changes])

    # Two types in one call: `good` applies, `needy` cannot (its model
    # requires data the block doesn't carry).
    #
    # NOTE: pinned behaviour — the notification rides the success path, so a
    #  failed batch notifies NOBODY even though the earlier entries applied.
    #  The exception names the failure; whether a failed batch should still
    #  notify what it did apply is an open design question, not settled here.
    with pytest.raises(EntryRequiresData):
        settings.plugins.set_type(good=TypedEntry, needy=NeedsData)
    assert heard == []  # the batch failed: no notification ran
    assert "good" in dict(settings.plugins)  # the earlier entry stays applied
    assert type(settings.plugins["good"]) is TypedEntry
