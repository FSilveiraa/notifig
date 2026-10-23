from __future__ import annotations

import pathlib
from collections.abc import Sequence
from os import PathLike
from typing import Any

import anyconfig
from pydantic import TypeAdapter
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

from .meta import ConfigDict

FILE_EXTENSIONS: tuple[str, ...] = ("json", "yaml", "yml", "toml", "ini")


def _merge_lists(low: list, high: list, *, unique: bool) -> list:
    items = [*low, *high]
    if not unique:
        return items
    # Equality, not hashing: a config list may hold dicts or lists. These lists are
    # config-sized, so the quadratic scan costs nothing worth optimising for.
    deduped: list = []
    for item in items:
        if item not in deduped:
            deduped.append(item)
    return deduped


def _merge(low: Any, high: Any, *, merge_lists: bool, merge_lists_unique: bool) -> Any:
    if isinstance(low, dict) and isinstance(high, dict):
        merged = dict(low)
        for key, value in high.items():
            merged[key] = (
                _merge(
                    low=low[key],
                    high=value,
                    merge_lists=merge_lists,
                    merge_lists_unique=merge_lists_unique,
                )
                if key in low
                else value
            )
        return merged
    if merge_lists and isinstance(low, list) and isinstance(high, list):
        return _merge_lists(low, high, unique=merge_lists_unique)
    return high


def load_and_merge_configs(
    paths_low_to_high: Sequence[str | PathLike],
    *,
    merge_lists: bool = False,
    merge_lists_unique: bool = False,
) -> dict:
    """Merge multiple config files with higher precedence winning.

    A later file wins for a scalar, and for a dict it merges key by key, so the
    earlier file's other keys survive.

    A list is replaced by the later file's value, unless `merge_lists` is set, in
    which case the files' items are appended; `unique` then drops repeats, keeping
    the first sighting.
    """
    merged: dict = {}
    for path in paths_low_to_high:
        data = anyconfig.load(pathlib.Path(path).resolve())
        if isinstance(data, dict):
            merged = _merge(
                low=merged,  # The data parsed so far
                high=data,  # The new data source to merge
                merge_lists=merge_lists,  # Whether to merge lists
                # Whether to exclude duplicate values when merging lists
                merge_lists_unique=merge_lists_unique,
            )
    return merged


def save_config(data: dict, path: str | PathLike) -> None:
    anyconfig.dump(data, pathlib.Path(path).expanduser().resolve())


class ConfigFileSource(PydanticBaseSettingsSource):
    """A pydantic-settings source for the files named by `--config FILE` (repeatable).
    They are read and merged in the order given -- a later file wins, except that
    lists are appended when the config sets `merge_lists_in_files` (deduped if it
    also sets `merge_lists_unique`, see `load_and_merge_configs`) -- and the
    resolved paths are stored in the `config_files` field."""

    config: ConfigDict

    def __init__(
        self,
        settings_cls: type[BaseSettings],
        field_name: str = "config_files",
    ):
        super().__init__(settings_cls)
        self._field_name = field_name

    # pydantic-settings base-class override
    def get_field_value(self, field, field_name):
        return None, "", False

    def __call__(self) -> dict[str, Any]:
        field = self.settings_cls.model_fields[self._field_name]

        # `current_state` holds what every higher-priority source produced,
        #  already merged in priority order
        found_paths = self.current_state.get(self._field_name)
        # Non-default paths (code, CLI flags, env vars) are required to exist
        required = found_paths is not None
        if found_paths is None:
            found_paths = self.config["default_config_files"]

        # Parsed by the field's own type: "a,b" from env or CLI, paths from code
        paths = TypeAdapter(field.rebuild_annotation()).validate_python(found_paths)

        # If paths are required and any is missing/not-a-file, raise
        if required and (missing := [path for path in paths if not path.is_file()]):
            raise FileNotFoundError(
                f"Config file(s) not found: {', '.join(map(str, missing))}"
            )

        read = [path for path in paths if path.is_file()]
        data = load_and_merge_configs(
            read,
            merge_lists=bool(self.config.get("merge_lists_in_files", False)),
            merge_lists_unique=bool(self.config.get("merge_lists_unique", False)),
        )
        data[self._field_name] = read
        return data
