from __future__ import annotations

from collections.abc import Iterable, Sequence
from os import PathLike
from typing import Any

import anyconfig
from pydantic import TypeAdapter
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

from .meta import ConfigDict
from .utils import absolute_path

FILE_EXTENSIONS: tuple[str, ...] = ("json", "yaml", "yml", "toml", "ini")

# Re-exports the error anyconfig raises under our own name.
# If later we stop using anyconfig, we keep the exception name
#  and point it at something else
UnknownFormat = anyconfig.UnknownFileTypeError


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


def _merge_configs(
    low: Any, high: Any, *, merge_lists: bool, merge_lists_unique: bool = False
) -> Any:
    if isinstance(low, dict) and isinstance(high, dict):
        merged = dict(low)
        for key, value in high.items():
            merged[key] = (
                _merge_configs(
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


def _parent_and_leaf_name_of(data: dict, path: str) -> tuple[dict | None, str]:
    """Finds the parent and leaf name of the given path in `data`."""
    *parents, leaf = path.split(".")
    node = data
    for parent in parents:
        child = node.get(parent)
        if not isinstance(child, dict):
            return None, leaf
        node = child
    return node, leaf


def _prune_empty(data: dict) -> dict:
    """
    Drops blocks that hold nothing, so removing a section's last entry doesn't leave
    an empty parent.

    `{"plugins": {"alpha": {}}}` becomes `{"plugins": {}}` becomes `{}`, which reads
    back identically: a block with no keys and no block at all mean the same thing.
    """
    pruned = {}
    for key, value in data.items():
        if isinstance(value, dict):
            value = _prune_empty(value)
            if not value:
                continue
        pruned[key] = value
    return pruned


def _without_paths(data: dict[str, Any], paths: Iterable[str]) -> dict[str, Any]:
    """
    Returns a copy of `data` with the leaf of each `path` removed.
    Parents left empty also get deleted.
    """
    for path in paths:
        parent, leaf = _parent_and_leaf_name_of(data, path)
        if parent is not None:
            parent.pop(leaf, None)
    return _prune_empty(data)


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
        data = anyconfig.load(absolute_path(path))
        if isinstance(data, dict):
            merged = _merge_configs(
                low=merged,  # The data parsed so far
                high=data,  # The new data source to merge
                merge_lists=merge_lists,  # Whether to merge lists
                # Whether to exclude duplicate values when merging lists
                merge_lists_unique=merge_lists_unique,
            )
    return merged


def save_config(
    data: dict,
    path: str | PathLike,
    *,
    remove: list[str] | None = None,
    patch: bool = False,
) -> None:
    path = absolute_path(path)
    if patch:
        try:
            file_data = anyconfig.load(path)
        except FileNotFoundError:
            file_data = {}

        data = _merge_configs(
            low=file_data,
            high=data,
            merge_lists=False,  # Patch, don't append
        )
        if remove:
            data = _without_paths(data, remove)

    anyconfig.dump(data, path)


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
