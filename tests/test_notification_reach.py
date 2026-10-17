"""The notification-reach contract: a change is heard by everything it reaches.

Measured rules, now pinned:
- The change walks DOWN from the written section and UP to the root; every
  section on that path hears it.
- Paths are RELATIVE to the observer's section: the root hears `api.model`,
  the `api` section hears `model`.
- A registered filter narrows to paths under it, and matches two ways: an
  observer filtered on `api.inner` hears `api.inner.token`, and one on the
  deeper `api.inner.token` hears its own change.
- An unfiltered observer hears everything reaching its section.
"""


def hear_into(settings, heard: list, *filters: str):
    """Register one sync observer on the ROOT and return nothing; the caller
    reads `heard` after writing."""

    @settings.on_change(*filters)
    def hear(_section, changes):
        heard.extend(change.path for change in changes)


def test_root_hears_a_nested_write_by_its_full_path(settings):
    heard: list = []
    hear_into(settings, heard)
    settings.api.model = "m"
    assert heard == ["api.model"]


def test_root_hears_a_deeply_nested_write_by_its_full_path(settings):
    heard: list = []
    hear_into(settings, heard)
    settings.api.inner.token = "t2"
    assert heard == ["api.inner.token"]


def test_the_written_section_hears_by_the_relative_name(settings):
    heard: list = []

    @settings.api.on_change()
    def hear(_section, changes):
        heard.extend(change.path for change in changes)

    settings.api.model = "m"
    assert heard == ["model"]


def test_a_filter_narrows_what_the_root_hears(settings):
    heard: list = []
    hear_into(settings, heard, "api.model")
    settings.api.url = "https://other.test"
    assert heard == []
    settings.api.model = "m"
    assert heard == ["api.model"]


def test_a_filter_matches_both_ways(settings):
    """An observer filtered on `api.inner` hears writes under it; one filtered
    on the leaf hears the leaf; neither hears the sibling `api.url`."""
    under: list = []
    leaf: list = []
    hear_into(settings, under, "api.inner")
    hear_into(settings, leaf, "api.inner.token")
    settings.api.url = "https://other.test"
    assert under == [] and leaf == []
    settings.api.inner.token = "t2"
    assert under == ["api.inner.token"] and leaf == ["api.inner.token"]


def test_a_created_change_says_it_was_created(settings):
    heard: list = []

    @settings.on_change("plugins")
    def hear(_section, changes):
        heard.extend(changes)

    settings.plugins["ghost"] = {"enabled": True}
    assert [type(change).__name__ for change in heard] == ["Created"]
    assert heard[0].value.enabled is True
