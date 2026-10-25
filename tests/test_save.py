"""Writing a config back out: where it goes, what it contains, what it refuses.

`save()` is the writer half of the file sources: it writes to the file the config
was read from unless told otherwise, writes only what was set unless asked for
everything, and lets the path's extension pick the format.
"""

from __future__ import annotations

import json

import pytest
import yaml

from notifig import Config, UnknownFormat

from .conftest import write_files


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
