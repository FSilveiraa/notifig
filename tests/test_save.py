"""Writing a config back out: where it goes, what it contains, what it refuses.

`save()` is the writer half of the file sources: it writes to the file the config
was read from unless told otherwise, writes only what was set unless asked for
everything, and lets the path's extension pick the format.
"""

from __future__ import annotations

import json
import os

import pytest
import yaml

from notifig import Config, UnknownFormat
from notifig.sources import save_config
from notifig.utils import nested_include

from .conftest import make_settings, write_files


class Settings(Config):
    api_url: str = "unset"
    feature: str | None = None


def test_save_writes_to_the_last_file_it_read(tmp_path):
    (path,) = write_files(tmp_path, **{"conf.yaml": "api_url: from-file\n"})
    settings = Settings(config_files=[path], parse_cli=False)

    settings.api_url = "changed"
    settings.save()

    assert yaml.safe_load((tmp_path / "conf.yaml").read_text()) == {
        "api_url": "changed"
    }


def test_save_writes_only_the_values_that_were_set(tmp_path):
    settings = Settings(api_url="from-code", parse_cli=False)
    target = tmp_path / "out.json"

    settings.save(target)

    assert json.loads(target.read_text()) == {"api_url": "from-code"}


def test_save_full_writes_every_field(tmp_path):
    settings = Settings(api_url="from-code", parse_cli=False)
    target = tmp_path / "out.json"

    settings.save(target, full=True)

    # `auto_save` is a real setting, not an internal switch like `parse_cli`, so
    # a full dump carries it too.
    assert json.loads(target.read_text()) == {
        "auto_save": False,
        "api_url": "from-code",
        "feature": None,
    }


def test_what_save_wrote_reads_back(tmp_path):
    first = Settings(api_url="from-code", parse_cli=False)
    target = tmp_path / "out.yaml"
    first.save(target)

    assert Settings(config_files=[str(target)], parse_cli=False).api_url == "from-code"


def test_save_refuses_an_extension_no_writer_knows(tmp_path):
    with pytest.raises(UnknownFormat):
        Settings(parse_cli=False).save(tmp_path / "out.unknown")


def test_save_without_a_path_or_a_read_file_raises():
    with pytest.raises(ValueError, match="no path was given"):
        Settings(parse_cli=False).save()


def test_save_preserves_the_existing_files_permissions(tmp_path):
    target = tmp_path / "secret.yaml"
    target.write_text("api_url: old\n")
    os.chmod(target, 0o600)

    Settings(api_url="new", parse_cli=False).save(target)

    assert target.stat().st_mode & 0o777 == 0o600


def test_auto_save_patches_only_changed_paths_and_never_leaks_env(
    tmp_path, monkeypatch
):
    (path,) = write_files(
        tmp_path,
        **{"conf.yaml": "api:\n  url: https://from-file.test\n  model: kept\n"},
    )
    monkeypatch.setenv("API__INNER__TOKEN", "secret-from-env")
    settings = make_settings(config_files=[path], auto_save=True)

    settings.api.url = "https://updated.test"

    # `api.url` updated, `api.model` survived from the file, and `api.inner.token`
    # from the environment was never written out.
    assert yaml.safe_load((tmp_path / "conf.yaml").read_text()) == {
        "api": {
            "url": "https://updated.test",
            "model": "kept",
        }
    }


def test_auto_save_removes_deleted_entries_and_prunes_empty_sections(tmp_path):
    (path,) = write_files(
        tmp_path,
        **{
            "conf.yaml": (
                "api:\n  url: https://example.test/v1\n"
                "plugins:\n  ghost:\n    enabled: true\n"
            )
        },
    )
    settings = make_settings(config_files=[path], auto_save=True)

    settings.plugins.delete("ghost")

    # Deleting `plugins.ghost` emptied `plugins`, so `_prune_empty` dropped it too.
    assert yaml.safe_load((tmp_path / "conf.yaml").read_text()) == {
        "api": {"url": "https://example.test/v1"}
    }


def test_auto_save_tolerates_removing_a_path_missing_from_the_file(tmp_path):
    (path,) = write_files(
        tmp_path, **{"conf.yaml": "api:\n  url: https://example.test/v1\n"}
    )
    settings = make_settings(config_files=[path])
    settings.plugins["ghost"] = {"enabled": True}
    settings.auto_save = True

    settings.plugins.delete("ghost")

    assert yaml.safe_load((tmp_path / "conf.yaml").read_text()) == {
        "api": {"url": "https://example.test/v1"},
        "auto_save": True,
    }


def test_save_config_patch_creates_a_missing_file(tmp_path):
    target = tmp_path / "created.json"
    save_config({"api_url": "fresh"}, target, patch=True)
    assert json.loads(target.read_text()) == {"api_url": "fresh"}


def test_auto_save_without_a_config_file_raises():
    settings = Settings(auto_save=True, parse_cli=False)
    with pytest.raises(ExceptionGroup) as raised:
        settings.api_url = "changed"
    assert isinstance(raised.value.exceptions[0], ValueError)
    assert "no config file was read" in str(raised.value.exceptions[0])


def test_nested_include_collapses_narrower_paths_under_wider_ones():
    assert nested_include(["api.inner.token", "api", "plugins.ghost"]) == {
        "api": True,
        "plugins": {"ghost": True},
    }
