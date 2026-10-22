"""The access protocols: by name, by attribute, by dotted path.

One rule per door: `[]` raises `KeyError` because it is the dict protocol, the
path API (`get`/`walk`) raises `PathNotFound`, and walking *through* a leaf
raises `NotASection`. Everything reads and writes the same one tree, so the
three doors must agree on the value they produce.
"""

from __future__ import annotations

import pytest

from notifig import PathNotFound
from notifig.exceptions import NotASection

from .conftest import EntryConfig, make_settings


def test_name_attribute_and_dotted_path_agree(settings):
    assert settings.api["url"] == settings.api.url
    assert settings["api.url"] == settings.api.url
    assert settings["api"]["url"] == settings.api.url
    assert settings["api.url"] == settings["api"]["url"]


def test_a_write_through_any_door_is_visible_through_the_others(settings):
    settings.api["url"] = "https://a.test"
    assert settings.api.url == "https://a.test"
    settings["api.url"] = "https://b.test"
    assert settings.api["url"] == "https://b.test"
    settings.api.url = "https://c.test"
    assert settings["api.url"] == "https://c.test"


def test_dotted_writes_reach_nested_sections(settings):
    settings["api.inner.token"] = "t2"
    assert settings.api.inner.token == "t2"
    assert settings["api.inner.token"] == "t2"


def test_a_missing_name_is_a_keyerror_and_nothing_is_created(settings):
    for path in ("api.nope", "api.inner.nope", "nope.url", "api.url.x"):
        with pytest.raises(KeyError):
            settings[path]
    assert "nope" not in settings
    assert "nope" not in settings.api


def test_the_path_api_uses_its_own_errors(settings):
    """`get`/`walk` are not the dict protocol: they name the path."""
    with pytest.raises(PathNotFound):
        settings.get("api.nope")
    with pytest.raises(PathNotFound):
        list(settings.walk("api.nope"))  # walk is a generator: consume it
    # A leaf is not a section, so there is no level to walk into.
    with pytest.raises(NotASection):
        list(settings.walk("api.url.x"))


def test_get_can_serialize_the_leaf_by_its_own_rules(settings):
    assert settings.get("api.inner", as_json=True) == {"token": "t"}


def test_iteration_yields_names_and_values(settings):
    assert dict(settings.api) == {
        "url": "https://example.test/v1",
        "model": None,
        "inner": settings.api.inner,
    }


def test_sections_unite_as_dicts(settings):
    assert settings.api.inner | settings.api.inner == {
        "token": "t",
    }


def test_editable_fields_walks_the_instance(settings):
    fields = settings.editable_fields()
    assert "api.url" in fields
    assert "api.inner.token" in fields
    # Sections are walked into, not offered as editable leaves.
    assert "api.inner" not in fields
    # `exclude=True` fields are neither editable nor walked into.
    assert not any(path.startswith("config_files") for path in fields)


def test_editable_fields_includes_typed_dynamic_entries(settings):
    class Rich(EntryConfig):
        label: str = ""

    settings.plugins["rich"] = {"enabled": True}
    settings.plugins.set_type(rich=Rich)
    assert "plugins.rich.label" in settings.editable_fields()


def test_walk_star_visits_the_level_and_double_star_the_subtree(settings):
    api = settings.api
    assert [name for name, _ in api.walk("*")] == ["url", "model", "inner"]
    assert [name for name, _ in api.walk("**")] == [
        "url",
        "model",
        "inner",
        "inner.token",
    ]
    # From the root the same walk carries the path it took to get there.
    assert [name for name, _ in settings.walk("api.*")] == [
        "api",
        "api.url",
        "api.model",
        "api.inner",
    ]


def test_a_double_star_must_be_last(settings):
    with pytest.raises(ValueError, match="last segment"):
        list(settings.walk("**.api"))


def test_a_standalone_section_builds_from_its_defaults():
    """A section is a valid model on its own; only a link needs a root."""
    standalone = make_settings().api.inner
    assert standalone.token == "t"
