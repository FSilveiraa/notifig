"""The custom annotations: what each one accepts, produces, and writes back.

These are the library's public vocabulary, so each test states the *shape* a
declaration produces — the member behind a name, the list behind a comma
string, the live value behind a link — not just that validation passed.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass
from typing import Annotated, ClassVar

import pytest
from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from notifig import (
    AbsolutePath,
    Choice,
    CLIFlags,
    CommaSeparated,
    Config,
    ConfigSection,
    Link,
    PathPattern,
)

# --- CLIFlags -----------------------------------------------------------------


def test_cli_flags_accepts_every_documented_shape():
    assert CLIFlags("c").flags == ("c",)
    assert CLIFlags("c", "config").flags == ("c", "config")
    assert CLIFlags(["c", "config"]).flags == ("c", "config")
    # The field declares a tuple, so every shape must land as one.
    assert isinstance(CLIFlags("c", "config").flags, tuple)


def test_cli_flags_refuses_mixing_the_two_shapes():
    with pytest.raises(ValueError, match="either as a list"):
        CLIFlags(["c"], "config")


# --- CommaSeparated -----------------------------------------------------------


def test_comma_separated_splits_strips_and_drops_empties():
    adapter = TypeAdapter(CommaSeparated[str])
    assert adapter.validate_python(" a, bb ,,c,") == ["a", "bb", "c"]
    assert adapter.validate_python("") == []
    assert adapter.validate_python(["a"]) == ["a"]  # a real list passes through


def test_comma_separated_writes_back_one_string():
    class Holder(BaseModel):
        tags: CommaSeparated[str] = []

    assert Holder(tags=["a", "b"]).model_dump(mode="json") == {"tags": "a,b"}


# --- Choice -------------------------------------------------------------------


@dataclass(frozen=True)
class Backend:
    name: str


BACKENDS = {"memory": Backend("memory"), "redis": Backend("redis")}
BackendChoice = Annotated[Backend, Choice(BACKENDS)]


class Client(BaseModel):
    backend: BackendChoice = BACKENDS["memory"]


def test_choice_maps_a_name_to_its_member():
    assert Client(backend="redis").backend is BACKENDS["redis"]


def test_choice_accepts_the_member_itself():
    assert Client(backend=BACKENDS["redis"]).backend is BACKENDS["redis"]


def test_choice_names_the_valid_options_in_its_error():
    with pytest.raises(ValidationError, match=r"Use one of \['memory', 'redis'\]"):
        Client(backend="mysql")


def test_choice_writes_the_name_back():
    assert Client(backend="redis").model_dump(mode="json") == {"backend": "redis"}


def test_choice_composes_inside_a_comma_separated_list():
    class Multi(BaseModel):
        backends: CommaSeparated[Annotated[Backend, Choice(BACKENDS)]] = []

    multi = Multi(backends="memory,redis")
    assert multi.backends == [BACKENDS["memory"], BACKENDS["redis"]]


@pytest.mark.xfail(
    strict=True,
    reason="CommaSeparated's join stringifies items with str(), so an item-level "
    "serializer (Choice's) never runs",
)
def test_a_choice_inside_a_list_writes_its_names_back():
    class Multi(BaseModel):
        backends: CommaSeparated[Annotated[Backend, Choice(BACKENDS)]] = []

    assert Multi(backends="memory,redis").model_dump(mode="json") == {
        "backends": "memory,redis"
    }


def test_a_choice_over_members_without_a_name_writes_back():
    class Level(BaseModel):
        value: Annotated[int, Choice({"debug": 10, "info": 20})] = 20

    assert Level(value=20).model_dump(mode="json") == {"value": "info"}


def test_choice_of_finds_the_choice_through_each_annotation_shape():
    class Flat(BaseModel):
        one: BackendChoice = BACKENDS["memory"]

    class Nested(BaseModel):
        many: CommaSeparated[Annotated[Backend, Choice(BACKENDS)]] = Field(
            default_factory=list
        )

    class Optional(BaseModel):
        maybe: Annotated[Backend, Choice(BACKENDS)] | None = None

    for model in (Flat, Nested, Optional):
        field_info = next(iter(model.model_fields.values()))
        found = list(Choice.of(field_info))
        assert len(found) == 1  # one Choice per field, wherever it is nested
        choice, annotation = found[0]
        assert choice.entries is BACKENDS
        assert choice.options == ["memory", "redis"]
        assert annotation is not None


# --- Link ---------------------------------------------------------------------


class Api(ConfigSection):
    url: str = "https://default.test"


class Webhooks(ConfigSection):
    api_url: ClassVar[Link[str]] = Link("/api.url")
    local: str = "local-value"
    local_link: ClassVar[Link[str]] = Link("local")


class Linked(Config):
    api: Api = Field(default_factory=Api)
    webhooks: Webhooks = Field(default_factory=Webhooks)


def test_a_rooted_link_follows_the_source_field():
    settings = Linked(parse_cli=False)
    assert settings.webhooks.api_url == "https://default.test"
    settings.api.url = "https://changed.test"
    assert settings.webhooks.api_url == "https://changed.test"  # live, not copied


def test_a_relative_link_reads_from_its_own_section():
    assert Linked(parse_cli=False).webhooks.local_link == "local-value"


def test_a_link_is_reachable_from_the_class_as_itself():
    assert isinstance(Webhooks.api_url, Link)
    assert Webhooks.api_url.source_path == "/api.url"


def test_a_link_needs_a_path():
    with pytest.raises(ValueError, match="needs a path"):
        Link("/")


def test_a_link_on_an_unattached_section_says_so():
    with pytest.raises(ValueError, match="isn't attached to a config"):
        _ = Webhooks().api_url


def test_writing_a_link_raises():
    """A link is read-only. The error today is a path error rather than a
    read-only one, but it is an `AttributeError`, so the write never lands."""
    settings = Linked(parse_cli=False)
    with pytest.raises(AttributeError):
        settings.webhooks.api_url = "https://nope.test"
    assert settings.webhooks.api_url == "https://default.test"


# --- Paths --------------------------------------------------------------------


def test_an_absolute_path_expands_the_user_and_resolves():
    resolved = TypeAdapter(AbsolutePath).validate_python("~/notes/x.txt")
    assert resolved.is_absolute()
    assert str(resolved).startswith(str(pathlib.Path.home()))


def test_an_absolute_path_resolves_a_relative_one_against_the_cwd():
    resolved = TypeAdapter(AbsolutePath).validate_python("rel/x.txt")
    assert resolved == (pathlib.Path.cwd() / "rel" / "x.txt").resolve()


def test_a_path_pattern_keeps_a_bare_name_and_expands_a_path():
    adapter = TypeAdapter(PathPattern)
    assert adapter.validate_python("*.py") == pathlib.Path("*.py")
    assert adapter.validate_python("~/x/*.py").is_absolute()


def test_paths_write_back_as_strings():
    adapter = TypeAdapter(AbsolutePath)
    resolved = adapter.validate_python("/tmp/x")
    assert adapter.dump_python(resolved, mode="json") == "/tmp/x"
