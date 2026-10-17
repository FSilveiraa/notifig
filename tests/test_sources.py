"""The sources: layered files deep-merge, later (higher-priority) files win,
and an explicitly named config file must exist."""

import pytest

from notifig import Config


def write_settings(tmp_path, files: dict[str, str]) -> list:
    paths = []
    for name, content in files.items():
        path = tmp_path / name
        path.write_text(content)
        paths.append(str(path))
    return paths


def make_settings_class(config_files: list[str] | None = None):
    class Settings(Config):
        api_url: str = "unset"
        feature: str | None = None

    return Settings, config_files


def test_later_files_win_and_earlier_values_survive(tmp_path):
    paths = write_settings(
        tmp_path,
        {
            "global.yaml": "api_url: from-global\nfeature: from-global\n",
            "local.yaml": "api_url: from-local\n",
        },
    )
    # `parse_cli=False`: the tests must not read pytest's own argv. The files
    # are passed on the `config_files` FIELD, so they are required to exist.
    settings = make_settings_class(paths)[0](config_files=paths, parse_cli=False)
    assert settings.api_url == "from-local"  # later file wins
    assert settings.feature == "from-global"  # earlier value survives the merge


def test_an_explicitly_missing_config_file_raises(tmp_path):
    missing = str(tmp_path / "nope.yaml")
    settings_class, _ = make_settings_class([missing])
    with pytest.raises(FileNotFoundError):
        settings_class(config_files=[missing], parse_cli=False)
