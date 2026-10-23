"""The sources: files deep-merge, env overrides files, CLI overrides env.

Precedence is high -> low: constructor kwargs, CLI, env, config files (see
`Config.settings_customise_sources`). `config_files` named by code, CLI or env
must exist; `default_config_files` are optional by design, so a missing one is
skipped rather than fatal.
"""

from __future__ import annotations

import pytest
from pydantic import Field

from notifig import Config, ConfigDict, ConfigSection

from .conftest import set_argv, write_files


class Settings(Config):
    api_url: str = "unset"
    feature: str | None = None


def test_later_files_win_and_earlier_keys_survive(tmp_path):
    global_cfg, local_cfg = write_files(
        tmp_path,
        **{
            "global.yaml": "api_url: from-global\nfeature: from-global\n",
            "local.yaml": "api_url: from-local\n",
        },
    )
    settings = Settings(config_files=[global_cfg, local_cfg], parse_cli=False)
    assert settings.api_url == "from-local"  # later file wins
    assert settings.feature == "from-global"  # earlier key survives the merge


def test_a_json_file_is_read(tmp_path):
    (path,) = write_files(tmp_path, **{"conf.json": '{"api_url": "from-json"}'})
    assert Settings(config_files=[path], parse_cli=False).api_url == "from-json"


def test_a_yaml_file_is_read(tmp_path):
    (path,) = write_files(tmp_path, **{"conf.yaml": "api_url: from-yaml\n"})
    assert Settings(config_files=[path], parse_cli=False).api_url == "from-yaml"


def test_a_yml_file_is_read(tmp_path):
    (path,) = write_files(tmp_path, **{"conf.yml": "api_url: from-yml\n"})
    assert Settings(config_files=[path], parse_cli=False).api_url == "from-yml"


def test_a_toml_file_is_read(tmp_path):
    (path,) = write_files(tmp_path, **{"conf.toml": 'api_url = "from-toml"\n'})
    assert Settings(config_files=[path], parse_cli=False).api_url == "from-toml"


def test_an_ini_file_is_read(tmp_path):
    """INI has no flat form: every value sits under a section header, so the file
    feeds a section-shaped config rather than a top-level field."""

    class Api(ConfigSection):
        base_url: str = "unset"

    class Nested(Config):
        api: Api = Field(default_factory=Api)

    (path,) = write_files(tmp_path, **{"conf.ini": "[api]\nbase_url = from-ini\n"})
    assert Nested(config_files=[path], parse_cli=False).api.base_url == "from-ini"


def test_dicts_are_merged_key_by_key_across_files(tmp_path):
    low, high = write_files(
        tmp_path,
        **{
            "low.yaml": "section:\n  a: from-low\n  b: from-low\n",
            "high.yaml": "section:\n  b: from-high\n",
        },
    )

    class Nested(Config):
        section: dict[str, str] = {}

    merged = Nested(config_files=[low, high], parse_cli=False).section
    assert merged == {"a": "from-low", "b": "from-high"}


def test_a_list_from_a_later_file_replaces_by_default(tmp_path):
    low, high = write_files(
        tmp_path,
        **{
            "low.yaml": "tags:\n  - one\n  - two\n",
            "high.yaml": "tags:\n  - three\n",
        },
    )

    class Listed(Config):
        tags: list[str] = []

    assert Listed(config_files=[low, high], parse_cli=False).tags == ["three"]


def test_lists_from_a_later_file_are_appended_when_asked(tmp_path):
    low, high = write_files(
        tmp_path,
        **{
            "low.yaml": "tags:\n  - one\n  - two\n",
            "high.yaml": "tags:\n  - three\n",
        },
    )

    class Listed(Config):
        model_config = ConfigDict(merge_lists_in_files=True)
        tags: list[str] = []

    assert Listed(config_files=[low, high], parse_cli=False).tags == [
        "one",
        "two",
        "three",
    ]


def test_a_merged_list_keeps_repeats_unless_asked_for_unique(tmp_path):
    low, high = write_files(
        tmp_path,
        **{
            "low.yaml": "tags:\n  - shared\n",
            "high.yaml": "tags:\n  - shared\n  - extra\n",
        },
    )

    class Repeated(Config):
        model_config = ConfigDict(merge_lists_in_files=True)
        tags: list[str] = []

    assert Repeated(config_files=[low, high], parse_cli=False).tags == [
        "shared",
        "shared",
        "extra",
    ]

    class Unique(Config):
        model_config = ConfigDict(merge_lists_in_files=True, merge_lists_unique=True)
        tags: list[str] = []

    assert Unique(config_files=[low, high], parse_cli=False).tags == ["shared", "extra"]


def test_a_dotenv_file_is_read_without_naming_it(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("API_URL=from-dotenv\n")
    monkeypatch.chdir(tmp_path)
    assert Settings(parse_cli=False).api_url == "from-dotenv"


def test_a_real_env_var_beats_the_dotenv_file(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("API_URL=from-dotenv\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("API_URL", "from-env")
    assert Settings(parse_cli=False).api_url == "from-env"


def test_a_dotenv_file_beats_a_config_file(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("API_URL=from-dotenv\n")
    (path,) = write_files(tmp_path, **{"conf.yaml": "api_url: from-file\n"})
    monkeypatch.chdir(tmp_path)
    assert Settings(config_files=[path], parse_cli=False).api_url == "from-dotenv"


def test_a_subclass_config_does_not_drop_the_default_dotenv(tmp_path, monkeypatch):
    """A subclass naming its own `model_config` inherits the parent's keys, so the
    `.env` default survives -- only naming `env_file` changes it."""

    (tmp_path / ".env").write_text("APP_API_URL=from-dotenv\n")
    monkeypatch.chdir(tmp_path)

    class Prefixed(Config):
        model_config = ConfigDict(env_prefix="APP_")
        api_url: str = "unset"

    assert Prefixed(parse_cli=False).api_url == "from-dotenv"


def test_env_file_none_turns_the_dotenv_off(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("API_URL=from-dotenv\n")
    monkeypatch.chdir(tmp_path)

    class NoDotEnv(Config):
        model_config = ConfigDict(env_file=None)
        api_url: str = "unset"

    assert NoDotEnv(parse_cli=False).api_url == "unset"


def test_an_explicitly_missing_config_file_raises(tmp_path):
    missing = str(tmp_path / "nope.yaml")
    with pytest.raises(FileNotFoundError, match="not found"):
        Settings(config_files=[missing], parse_cli=False)


def test_a_missing_default_config_file_is_simply_skipped(tmp_path):
    class Defaulted(Config):
        model_config = ConfigDict(
            default_config_files=[str(tmp_path / "absent.yaml")],
        )
        api_url: str = "default"

    assert Defaulted(parse_cli=False).api_url == "default"


def test_default_files_are_overridden_by_a_named_one(tmp_path):
    (default_path,) = write_files(
        tmp_path, **{"default.yaml": "api_url: from-default\n"}
    )
    (named_path,) = write_files(tmp_path, **{"named.yaml": "api_url: from-named\n"})

    class Defaulted(Config):
        model_config = ConfigDict(default_config_files=[default_path])
        api_url: str = "default"

    assert Defaulted(parse_cli=False).api_url == "from-default"
    # A path from code/CLI/env REPLACES the defaults rather than layering on them.
    assert Defaulted(config_files=[named_path], parse_cli=False).api_url == "from-named"


def test_the_read_files_are_reported_on_the_instance(tmp_path):
    first, second = write_files(
        tmp_path,
        **{"first.yaml": "api_url: one\n", "second.yaml": "api_url: two\n"},
    )
    settings = Settings(config_files=[first, second], parse_cli=False)
    assert [str(path) for path in settings.config_files] == [first, second]


def test_cli_overrides_env_which_overrides_files(tmp_path, monkeypatch):
    (path,) = write_files(tmp_path, **{"conf.yaml": "api_url: from-file\n"})
    monkeypatch.setenv("API_URL", "from-env")
    settings = Settings(config_files=[path], parse_cli=False)
    assert settings.api_url == "from-env"

    set_argv(monkeypatch, "--api-url", "from-cli")
    assert Settings(config_files=[path]).api_url == "from-cli"

    # Constructor kwargs outrank every source.
    assert Settings(config_files=[path], api_url="from-code").api_url == "from-code"


def test_config_files_can_be_named_on_the_command_line(tmp_path, monkeypatch):
    (path,) = write_files(tmp_path, **{"conf.toml": 'api_url = "from-cli-file"\n'})
    set_argv(monkeypatch, "--config-files", path)
    settings = Settings()
    assert settings.api_url == "from-cli-file"
    assert [str(one) for one in settings.config_files] == [path]


def test_nested_env_names_need_the_delimiter_and_work_with_it(monkeypatch):
    """A nested section is one field, so its env form is either the whole
    section as JSON or `PREFIX_SECTION__FIELD` once `env_nested_delimiter` is
    set. Both shapes are pinned here because the recipe is not guessable."""

    class Nested(Config):
        model_config = ConfigDict(env_prefix="APP_")
        section: dict[str, str] = {}

    monkeypatch.setenv("APP_SECTION", '{"a": "one"}')
    assert Nested(parse_cli=False).section == {"a": "one"}

    class Delimited(Config):
        model_config = ConfigDict(env_prefix="APP_", env_nested_delimiter="__")
        section: dict[str, str] = {}

    monkeypatch.setenv("APP_SECTION__A", "two")
    assert Delimited(parse_cli=False).section == {"a": "two"}
