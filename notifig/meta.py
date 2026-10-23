"""Describes a Config's own config."""

from os import PathLike

from pydantic_settings import SettingsConfigDict


class ConfigDict(SettingsConfigDict, total=False):
    """
    Takes pydantic-settings defaults like `cli_shortcuts`.
    Adds `default_config_files`, the default files to read a config from
    if none was specified.

    `env_file` is inherited from `Config` as `".env"`, so a `.env` in the working
    directory is read without naming it. Set `env_file=None` to disable it.
    """

    # Config files read when no source names any; each is read only if it exists
    default_config_files: list[str | PathLike]
