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

    # Whether a list named in several config files is appended to, or replaced by
    # the later file's value. Off by default.
    # NOTE: dicts always merge key by key.
    merge_lists_in_files: bool

    # Whether a merged list drops repeats (keeping the first sighting). Only
    # consulted when `merge_lists_in_files` is on.
    merge_lists_unique: bool
