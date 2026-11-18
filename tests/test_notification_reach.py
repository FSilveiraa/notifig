"""The notification-reach contract: a change is heard by everything it reaches.

Measured rules, pinned:
- The change walks DOWN from the written section and UP to the root; every
  section on that path hears it.
- Paths are RELATIVE to the observer's section: the root hears `api.model`,
  the `api` section hears `model`.
- A registered filter narrows to paths under it, and matches two ways: an
  observer filtered on `api.inner` hears `api.inner.token`, and one filtered on
  the deeper `api.inner.token` hears its own change.
- An unfiltered observer hears everything reaching its section.
"""

from __future__ import annotations

from notifig import Created, Deleted, Updated


def watch(settings, *filters):
    """One sync observer on the root, appending the changes it hears."""
    heard: list = []

    @settings.on_change(*filters)
    def hear(_section, changes):
        heard.extend(changes)

    return heard


def test_root_hears_a_nested_write_by_its_full_path(settings):
    heard = watch(settings)
    settings.api.model = "m"
    assert [change.path for change in heard] == ["api.model"]


def test_root_hears_a_deeply_nested_write_by_its_full_path(settings):
    heard = watch(settings)
    settings.api.inner.token = "t2"
    assert [change.path for change in heard] == ["api.inner.token"]


def test_a_section_between_root_and_leaf_hears_the_rest_of_the_path(settings):
    """The `api` section's own observer reports what is below it, so the root's
    `api.inner.token` arrives there as `inner.token`."""
    heard: list = []

    @settings.api.on_change()
    def hear(_section, changes):
        heard.extend(change.path for change in changes)

    settings.api.inner.token = "t2"
    assert heard == ["inner.token"]


def test_the_written_section_hears_by_the_relative_name(settings):
    heard: list = []

    @settings.api.on_change()
    def hear(_section, changes):
        heard.extend(change.path for change in changes)

    settings.api.model = "m"
    assert heard == ["model"]


def test_a_filter_narrows_what_the_root_hears(settings):
    heard = watch(settings, "api.model")
    settings.api.url = "https://other.test"
    assert heard == []
    settings.api.model = "m"
    assert [change.path for change in heard] == ["api.model"]


def test_a_filter_matches_both_ways(settings):
    """An observer filtered on `api.inner` hears writes under it; one filtered
    on the leaf hears the leaf; neither hears the sibling `api.url`."""
    under: list = []
    leaf: list = []

    @settings.on_change("api.inner")
    def hear_under(_section, changes):
        under.extend(change.path for change in changes)

    @settings.on_change("api.inner.token")
    def hear_leaf(_section, changes):
        leaf.extend(change.path for change in changes)

    settings.api.url = "https://other.test"
    assert under == [] and leaf == []
    settings.api.inner.token = "t2"
    assert under == ["api.inner.token"] and leaf == ["api.inner.token"]


def test_an_unfiltered_observer_hears_every_branch(settings):
    heard = watch(settings)
    settings.api.model = "m"
    settings.plugins["ghost"] = {"enabled": True}
    assert [change.path for change in heard] == ["api.model", "plugins.ghost"]


def test_each_change_type_carries_what_it_means(settings):
    heard = watch(settings)
    settings.plugins["ghost"] = {"enabled": True}
    settings.plugins["ghost"] = {"enabled": False}
    settings.plugins.delete("ghost")

    created, updated, deleted = heard
    assert isinstance(created, Created) and created.value.enabled is True
    assert isinstance(updated, Updated)
    assert updated.before.enabled is True and updated.after.enabled is False
    assert isinstance(deleted, Deleted) and deleted.value.enabled is False
    assert [change.path for change in heard] == ["plugins.ghost"] * 3


def test_a_change_that_changes_nothing_notifies_nobody(settings):
    heard = watch(settings)
    settings.api.model = None  # already None
    settings.api.url = "https://example.test/v1"  # already this
    assert heard == []


def test_observers_inside_a_replaced_section_hear_inner_changes(settings):
    heard_api: list[str] = []
    heard_inner: list[str] = []

    @settings.api.on_change()
    def on_api(_section, changes):
        heard_api.extend(change.path for change in changes)

    @settings.api.inner.on_change()
    def on_inner(_section, changes):
        heard_inner.extend(change.path for change in changes)

    # 1. Dotted write from root: exercises the existing Down walk (`self.walk("api.inner")`)
    settings.set(**{"api.inner.token": "t2"})
    assert heard_api == ["inner.token"]
    assert heard_inner == ["token"]
    # 2. Whole-section replacement: exercises `_find_changes_compared_to` + routing into `api` and `api.inner`
    heard_api.clear()
    heard_inner.clear()
    settings.set(api={"url": "https://example.test/v1", "inner": {"token": "t3"}})
    assert heard_api == ["inner.token"]
    assert heard_inner == ["token"]


def test_observers_inside_a_replaced_dynamic_section_hear_created_and_deleted_entries(
    settings,
):
    settings.plugins["old"] = {"enabled": True}
    heard_plugins: list[tuple[str, str]] = []

    @settings.plugins.on_change()
    def on_plugins(_section, changes):
        heard_plugins.extend((type(c).__name__, c.path) for c in changes)

    settings.set(plugins={"fresh": {"enabled": False}})
    assert ("Created", "fresh") in heard_plugins
    assert ("Deleted", "old") in heard_plugins
