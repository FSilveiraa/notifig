"""The pydantic internals notifig is built on, asserted as invariants.

Notifig does not merely USE pydantic - it stands on undocumented shapes:
generic metadata, the extra-values mechanism, ``validate_assignment``. A
pydantic bump can change any of these without our code changing at all. This
file is the upgrade gate from solveig's dependency policy, carried here: run
it on any dependency bump; the tier in each test's docstring says how to react.

Tiers:
- T1: breakage means a pydantic bug or a major redesign - gate the bump until
  a release of notifig adapts. The version ceiling exists for these.
- T2: semantic, likely to change shape before it breaks - watch the diff, fix
  quietly if the meaning survives.
"""

from typing import ClassVar

import pytest
from pydantic import TypeAdapter, ValidationError

from notifig import Config, ConfigSection
from notifig.section.dynamic import DynamicSection
from notifig.section.static import ConfigSection as BaseSection


def test_t1_generic_metadata_carries_the_subscript_argument():
    """T1. The declarer-binding design (`MyConfig[MyTool]` stamping
    `tool_class`, `bound_to` guarding double binding) reads
    `cls.__pydantic_generic_metadata__["args"]` from
    `__pydantic_init_subclass__`. If pydantic reshapes that metadata, binding
    fails loudly - but the shape must be known before a bump is accepted."""

    class Base[T: str = str](BaseSection):
        declarer: ClassVar[str | None] = None

        @classmethod
        def __pydantic_init_subclass__(cls, **kwargs):
            super().__pydantic_init_subclass__(**kwargs)
            metadata = cls.__pydantic_generic_metadata__
            if metadata["origin"] is not None:  # this is Base[X] itself
                cls.declarer = metadata["args"][0]

    class Bound(Base["the-declarer"]):
        pass

    assert Bound.declarer == "the-declarer"


def test_t1_the_write_path_instantiates_entries_eagerly():
    """T1. A block written onto a dynamic section instantiates as the entry
    type immediately (or as the section's copied type under
    `force_preserve_extra`) - this is pydantic's extra-validation running
    through `DynamicSection._set`, and it is what makes an unowned block a
    live, editable object rather than a raw dict."""

    class Entry(ConfigSection):
        enabled: bool = True

    class Plugins(DynamicSection[Entry]):
        pass

    class Settings(Config):
        plugins: Plugins

    settings = Settings(plugins={}, parse_cli=False)
    settings.plugins["ghost"] = {"enabled": False}
    entry = settings.plugins["ghost"]
    assert isinstance(entry, Entry)  # instantiated, not a raw dict
    assert entry.enabled is False


def test_t1_constructor_data_instantiates_entries_eagerly():
    """T1. A block arriving with the CONSTRUCTOR - a config file at parse
    time - instantiates as the entry type exactly like a written one: the
    wrap validator (`DynamicSection._instantiate_entries`) is the one
    entrypoint a block crosses, so a malformed block is a load-time error,
    not a latent one. The mechanism is the same `TypeAdapter` build the
    write path uses."""

    class Entry(ConfigSection):
        enabled: bool = True

    class Plugins(DynamicSection[Entry]):
        pass

    class Settings(Config):
        plugins: Plugins

    settings = Settings(plugins={"ghost": {"enabled": False}}, parse_cli=False)
    entry = settings.plugins["ghost"]
    assert isinstance(entry, Entry)  # instantiated, not a raw dict
    assert entry.enabled is False


def test_t1_a_malformed_block_is_a_load_error():
    """T1. The one-entrypoint rule's payoff: a config file block the entry
    type refuses (surplus keys on a strict model) fails at LOAD, loudly -
    not as a latent raw dict discovered by whichever reader trips on it."""

    class StrictEntry(ConfigSection):
        enabled: bool = True

    class Plugins(DynamicSection[StrictEntry]):
        pass

    class Settings(Config):
        plugins: Plugins

    with pytest.raises(ValidationError):
        Settings(plugins={"ghost": {"enabled": True, "surplus": 1}}, parse_cli=False)


def test_t1_validate_assignment_runs_on_base_model_setattr():
    """T1. `set_type` stores entries with `BaseModel.__setattr__` ON PURPOSE:
    the re-validation it triggers is what stamps the entry's `_name` through
    `_bind` and records the name in `__pydantic_fields_set__`, so `/config
    save` round-trips the entry. Losing that would silently break persistence,
    not crash."""

    class Entry(ConfigSection):
        enabled: bool = True

    class Plugins(DynamicSection[Entry]):
        pass

    class Settings(Config):
        plugins: Plugins

    settings = Settings(plugins={}, parse_cli=False)
    plugins = settings.plugins
    plugins["ghost"] = {"enabled": True}
    assert "ghost" in plugins.__pydantic_fields_set__
    entry = plugins["ghost"]
    assert entry._name == "ghost"  # stamped by the re-validation


def test_t2_type_adapter_validates_without_registering_anything():
    """T2. `set_type` and `remove_type` validate data against an arbitrary
    entry model through an isolated `TypeAdapter`. If adapters gained global
    registration side effects, every re-type would leak; today they do not."""

    class Entry(ConfigSection):
        enabled: bool = True

    first = TypeAdapter(Entry)
    second = TypeAdapter(Entry)
    assert first.validate_python({"enabled": False}) is not second.validate_python(
        {"enabled": False}
    )


def test_t2_private_and_extra_storage_are_the_same_namespace():
    """T2. Reading an untyped entry name goes through pydantic's `__getattr__`
    into `__pydantic_extra__`; `__contains__` reads `model_extra` directly.
    These must stay the same storage or access splits in two."""

    class Entry(ConfigSection):
        enabled: bool = True

    class Plugins(DynamicSection[Entry]):
        pass

    class Settings(Config):
        plugins: Plugins

    settings = Settings(plugins={}, parse_cli=False)
    settings.plugins["ghost"] = {"enabled": True}
    assert "ghost" in settings.plugins.model_extra
    assert "ghost" in settings.plugins  # __contains__ sees it
    assert settings.plugins["ghost"].enabled is True  # and so does __getattr__


def test_t2_set_type_survives_validate_assignment_rejecting_a_write():
    """T2. `set_type` stores through `BaseModel.__setattr__`, and pydantic's
    `validate_assignment` re-validation runs AFTER the value has landed - a
    rejection there does not roll the write back. That is exactly why
    `set_type` refuses a dotted entry name BEFORE building: the guard must
    precede the store, or a refused name would leave a half-stored entry."""

    class Entry(ConfigSection):
        enabled: bool = True

    class Plugins(DynamicSection[Entry]):
        pass

    class Settings(Config):
        plugins: Plugins

    settings = Settings(plugins={}, parse_cli=False)
    with pytest.raises(ValueError):
        settings.plugins.set_type("dotted.name", Entry)
    assert "dotted.name" not in dict(settings.plugins)
