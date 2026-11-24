"""The pydantic internals notifig is built on, asserted as invariants.

Notifig does not merely USE pydantic - it stands on undocumented shapes:
generic metadata, the extra-values mechanism, ``validate_assignment``. A
pydantic bump can change any of these without our code changing at all. This
file is the upgrade gate for a library built on dependency internals: run
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

from notifig import ConfigSection
from notifig.section.dynamic import DynamicSection
from notifig.section.static import ConfigSection as BaseSection
from notifig.section.validation import candidate_class_from_section_class

from .conftest import TestConfig


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

    class Settings(TestConfig):
        plugins: Plugins

    settings = Settings(plugins={})
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

    class Settings(TestConfig):
        plugins: Plugins

    settings = Settings(plugins={"ghost": {"enabled": False}})
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

    class Settings(TestConfig):
        plugins: Plugins

    with pytest.raises(ValidationError):
        Settings(plugins={"ghost": {"enabled": True, "surplus": 1}})


def test_t1_dynamic_entry_writes_stamp_name_and_fields_set():
    """T1. Writing a dynamic entry onto `DynamicSection` commits it into
    `__pydantic_extra__`, stamps its `_name` and `_parent` through
    `_stamp_parent_on_subsections`, and records the name in
    `__pydantic_fields_set__`, so `save()` with `exclude_unset=True`
    round-trips the entry."""

    class Entry(ConfigSection):
        enabled: bool = True

    class Plugins(DynamicSection[Entry]):
        pass

    class Settings(TestConfig):
        plugins: Plugins

    settings = Settings(plugins={})
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

    class Settings(TestConfig):
        plugins: Plugins

    settings = Settings(plugins={})
    settings.plugins["ghost"] = {"enabled": True}
    assert "ghost" in settings.plugins.model_extra
    assert "ghost" in settings.plugins  # __contains__ sees it
    assert settings.plugins["ghost"].enabled is True  # and so does __getattr__


def test_t1_model_validate_keeps_section_instances_by_identity():
    """T1. A validation candidate is built from the section's CURRENT field
    values with the batch's writes laid on top, and every untouched
    sub-section is passed in as an instance rather than as data. Building a
    candidate is only cheap, and only free of surprises, while pydantic's
    default `revalidate_instances="never"` hands that instance straight back:
    a copy would give the candidate its own tree, and the commit step would
    then write values no validator ever saw."""

    class Api(ConfigSection):
        url: str = "https://default.test"

    class Settings(TestConfig):
        api: Api

    settings = Settings(api=Api())
    candidate = candidate_class_from_section_class(
        Settings, frozenset({"api"})
    ).model_validate({"api": settings.api})
    assert candidate.api is settings.api


def test_t1_a_validation_candidate_does_not_reparent_the_sections_it_is_built_from():
    """T1. `ConfigSection._stamp_parent_on_subsections` is a `model_validator`
    of mode `after`, so pydantic runs it on a candidate exactly as it runs on
    a real section. A candidate is built from the LIVE `Api` instance, so
    without `ValidationSection`'s override of it the live `Api` would be
    re-parented onto a throwaway object, and every `_path` computed on it
    afterwards would name the candidate instead of the config."""

    class Api(ConfigSection):
        url: str = "https://default.test"

    class Settings(TestConfig):
        api: Api

    settings = Settings(api=Api())
    api = settings.api
    assert api._parent is settings

    candidate_class_from_section_class(Settings, frozenset({"api"})).model_validate(
        {"api": api}
    )

    assert api._parent is settings


def test_t1_a_validation_candidate_registers_no_observer():
    """T1. `Config.model_post_init` is called by pydantic on a candidate too,
    and it registers the auto-save observer there. A candidate must have no
    effects on anything - it is a throwaway object built only to find out
    whether a batch is valid - so the observer has to stay off it."""

    class Api(ConfigSection):
        url: str = "https://default.test"

    class Settings(TestConfig):
        api: Api

    settings = Settings(api=Api())
    assert len(settings._observers) == 1  # the auto-save observer

    candidate = candidate_class_from_section_class(
        Settings, frozenset({"api"})
    ).model_validate({"api": settings.api})

    assert candidate._observers == []


def test_t2_a_candidate_error_names_the_original_class():
    """T2. The candidate class is literally named `ValidationCandidate`, so a
    `ValidationError` raised while validating one would read "for
    ValidationCandidate" - a class the caller never wrote and cannot look up.
    The `title` in the candidate's `model_config` is the only thing that keeps
    the original class name in the message."""

    class Api(ConfigSection):
        url: str = "https://default.test"
        port: int = 1

    with pytest.raises(ValidationError) as error:
        candidate_class_from_section_class(
            Api, frozenset({"url", "port"})
        ).model_validate({"url": "x", "port": "not-a-number"})

    assert str(error.value).splitlines()[0] == "1 validation error for Api"
