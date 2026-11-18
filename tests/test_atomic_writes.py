"""Atomic batch writes.

`set()` validates its whole batch before anything changes:
- A batch with one refused path applies nothing and notifies nobody.
- A batch is validated once, against the state it produces, so a cross-field
  validator never sees a state left halfway by the order of the paths.
- The commit writes into the live sections, so references to them and the
  observers registered on them survive.
- A value a model validator derives is committed without emitting its own event.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import pytest
from pydantic import (
    AfterValidator,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from notifig import Config, ConfigDict, ConfigSection, PathNotFound
from notifig.exceptions import OverlappingChangePaths

from .conftest import ApiConfig, make_settings, write_files


class Ports(ConfigSection):
    """Two fields that are only valid together."""

    low: int = 1000
    high: int = 2000

    @model_validator(mode="after")
    def ordered(self):
        if self.low > self.high:
            raise ValueError("low must be <= high")
        return self


class Clamped(ConfigSection):
    """A model validator that derives `high` from `low`."""

    low: int = 0
    high: int = 10

    @model_validator(mode="before")
    @classmethod
    def raise_high_to_low(cls, data):
        if isinstance(data, dict) and data.get("high", 10) < data.get("low", 0):
            data = {**data, "high": data["low"]}
        return data


class Labelled(ConfigSection):
    aliased: int = Field(default=0, alias="the-value")
    frozen: int = Field(default=0, frozen=True)


class NetworkSettings(Config):
    name: str = "net"
    ports: Ports = Field(default_factory=Ports)
    clamped: Clamped = Field(default_factory=Clamped)
    labelled: Labelled = Field(default_factory=Labelled)


def make_network_settings(**values) -> NetworkSettings:
    return NetworkSettings(**values, parse_cli=False)


def record_changes(section: ConfigSection) -> list[list[str]]:
    """Register an observer on `section`; each notification appends its paths."""
    heard: list[list[str]] = []

    @section.on_change()
    def hear(_section, changes):
        heard.append([change.path for change in changes])

    return heard


# --- Nothing applied, nobody notified ---------------------------------------


def test_a_batch_with_one_refused_value_applies_nothing_and_notifies_nobody():
    settings = make_settings()
    heard = record_changes(settings)
    with pytest.raises(ValidationError):
        settings.set(**{"api.model": "m", "api.url": ["not", "a", "string"]})
    assert settings.api.model is None
    assert settings.api.url == "https://example.test/v1"
    assert heard == []


def test_a_refused_batch_across_two_sections_leaves_both_untouched():
    settings = make_settings()
    heard = record_changes(settings)
    with pytest.raises(ValidationError):
        settings.set(**{"api.model": "m", "api.inner.token": ["not", "a", "string"]})
    assert settings.api.model is None
    assert settings.api.inner.token == "t"
    assert heard == []


def test_a_path_that_does_not_resolve_refuses_the_whole_batch():
    settings = make_settings()
    with pytest.raises(PathNotFound):
        settings.set(**{"api.model": "m", "api.nope": 1})
    assert settings.api.model is None


# --- Validated against the final state --------------------------------------


def test_a_batch_is_validated_against_the_state_it_produces():
    """Written one path at a time, `low=3000` would meet `high=2000` and fail."""
    settings = make_network_settings()
    heard = record_changes(settings)
    settings.set(**{"ports.low": 3000, "ports.high": 4000})
    assert (settings.ports.low, settings.ports.high) == (3000, 4000)
    assert heard == [["ports.low", "ports.high"]]


def test_a_batch_of_one_still_refuses_an_invalid_final_state():
    settings = make_network_settings()
    with pytest.raises(ValidationError, match="low must be <= high"):
        settings.ports.set(low=3000)
    assert settings.ports.low == 1000


def test_a_value_a_model_validator_derives_is_committed():
    settings = make_network_settings()
    heard = record_changes(settings)
    settings.clamped.set(low=20)
    assert (settings.clamped.low, settings.clamped.high) == (20, 20)
    assert heard == [["clamped.low"]]


# --- The live tree survives --------------------------------------------------


def test_references_and_their_observers_survive_a_batch():
    settings = make_settings()
    api = settings.api
    heard = record_changes(api)
    settings.set(**{"api.url": "https://new.test"})
    assert settings.api is api
    assert heard == [["url"]]


def test_a_root_write_keeps_every_subsection_it_did_not_touch():
    settings = make_network_settings()
    ports = settings.ports
    heard = record_changes(ports)
    settings.set(name="renamed")
    assert settings.ports is ports
    settings.ports.set(low=1)
    assert heard == [["low"]]


def test_a_section_swapped_in_is_bound_to_the_live_tree():
    settings = make_settings()
    heard = record_changes(settings)
    settings.set(api={"url": "https://swapped.test"})
    assert settings.api._parent is settings
    assert settings.api.inner._parent is settings.api
    settings.api.inner.set(token="z")
    assert heard[-1] == ["api.inner.token"]


def test_a_section_instance_swapped_in_is_the_object_given():
    settings = make_settings()
    replacement = ApiConfig(url="https://given.test")
    settings.set(api=replacement)
    assert settings.api is replacement
    assert replacement._parent is settings


# --- A candidate is only a candidate -----------------------------------------


def test_a_root_write_does_not_reread_the_config_files(tmp_path):
    """A candidate of the root `Config` validates only the data it is given: if
    it ran the settings sources again, a config file deleted after load would
    make every root write fail."""
    paths = write_files(tmp_path, **{"config.yaml": "name: from-file\n"})
    settings = make_network_settings(config_files=paths)
    Path(paths[0]).unlink()
    settings.set(name="changed")
    assert settings.name == "changed"


def test_a_root_write_registers_no_second_auto_save_observer():
    settings = make_network_settings()
    observers_before = len(settings._observers)
    settings.set(name="renamed")
    assert len(settings._observers) == observers_before


def test_a_field_declared_with_an_alias_is_still_written_by_name():
    settings = make_network_settings()
    settings.labelled.set(aliased=3)
    assert settings.labelled.aliased == 3


def test_a_frozen_field_still_refuses_a_write():
    settings = make_network_settings()
    with pytest.raises(ValidationError, match="frozen"):
        settings.labelled.set(frozen=1)
    assert settings.labelled.frozen == 0


def test_a_frozen_section_refuses_every_write():
    class FrozenSection(ConfigSection):
        model_config = ConfigDict(frozen=True)
        port: int = 8000

    class FrozenSettings(Config):
        frozen: FrozenSection = Field(default_factory=FrozenSection)

    settings = FrozenSettings(parse_cli=False)
    with pytest.raises(ValidationError, match="frozen"):
        settings.frozen.set(port=9000)
    assert settings.frozen.port == 8000


def test_overlapping_paths_in_a_batch_are_refused():
    settings = make_settings()
    with pytest.raises(OverlappingChangePaths, match="'api' and 'api.url'"):
        settings.set(
            api={"url": "https://a.test"},
            **{"api.url": "https://b.test"},
        )
    assert settings.api.url == "https://example.test/v1"


def test_unwritten_fields_do_not_rerun_their_field_validators():
    """Writing `other` must not run `tagged`'s non-idempotent validators a
    second time on its already-validated live value."""

    class NonIdempotent(ConfigSection):
        annotated: Annotated[str, AfterValidator(lambda v: f"{v}!")] = "a"
        decorated: str = "b"
        shared_a: str = "x"
        shared_b: str = "y"
        other: int = 0

        @field_validator("decorated")
        @classmethod
        def append_hash(cls, value: str) -> str:
            return f"{value}#"

        @field_validator("shared_a", "shared_b")
        @classmethod
        def append_star(cls, value: str) -> str:
            return f"{value}*"

    class AppSettings(Config):
        box: NonIdempotent = Field(default_factory=NonIdempotent)

    settings = AppSettings(
        box={"annotated": "a", "decorated": "b", "shared_a": "x", "shared_b": "y"},
        parse_cli=False,
    )
    assert (
        settings.box.annotated,
        settings.box.decorated,
        settings.box.shared_a,
        settings.box.shared_b,
    ) == ("a!", "b#", "x*", "y*")

    # Writing `other` and `shared_a` runs `append_star` on `shared_a` only;
    # `annotated`, `decorated`, and `shared_b` are untouched.
    settings.box.set(other=1, shared_a="x2")
    assert (
        settings.box.annotated,
        settings.box.decorated,
        settings.box.shared_a,
        settings.box.shared_b,
    ) == ("a!", "b#", "x2*", "y*")


def test_a_coerced_write_that_equals_the_live_value_notifies_nobody():
    settings = make_network_settings()
    heard = record_changes(settings)
    # `"1000"` differs from `1000` before validation, then validates to `1000`.
    settings.ports.set(low="1000")
    assert settings.ports.low == 1000
    assert heard == []
