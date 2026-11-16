from __future__ import annotations

import asyncio
import functools
import inspect
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import replace, dataclass, field
from typing import TYPE_CHECKING, Any, Self, TypeIs, NoReturn, assert_never

from pydantic import BaseModel, ConfigDict, PrivateAttr, model_validator, ValidationError
from pydantic.fields import FieldInfo
from pydantic_core import InitErrorDetails

from ..annotation.cli_flags import CLIFlags
from ..exceptions import AsyncObserversNotCalled, NotASection, PathNotFound, CantDeleteRetypeDeclaredField, \
    OverlappingChangePaths
from ..utils import split_parent_path_and_leaf, matches_prefix
from ..write.notification import ConfigChange, Created, Deleted, Updated
from ..write.request import ChangeRequest, Assign, Delete, SetType, RemoveType

# Model configs used by pydantic to inform model validation.
# Forces model re-validation on re-assignment and allows arbitrary types.
_MUTABLE = ConfigDict(
    # Every config.api.url = "..." re-validates at assignment time, not just
    # at parse. So a bad value can't be set live mid-run and corrupt state.
    validate_assignment=True,
    # The config holds non-pydantic types. Without this flag,
    # pydantic rejects a field whose type isn't a BaseModel/builtin.
    arbitrary_types_allowed=True,
    # Ensures that unknown keys raise an error
    extra="forbid",
)


@dataclass(frozen=True)
class _ChangeGroup:
    # Common parent path of each change
    parent_path: str
    # The parent section for the path's leaf
    parent_section: ConfigSection
    # Changes to be applied to leaves of parent
    change_requests_by_leaf_name: dict[str, ChangeRequest] = field(default_factory=dict)


@dataclass(frozen=True)
class _CandidateData:
    # Data for declared fields and typed entries
    data: dict[str, Any] = field(default_factory=dict)
    # Untyped data that goes into `unresolved` for DynamicSection
    unresolved: dict[str, Any] = field(default_factory=dict)
    # Name of entries built from model defaults, must not be marked as set
    built_from_defaults: set[str] = field(default_factory=set)
    # Events for each leaf
    events_by_leaf: dict[str, ConfigChange] = field(default_factory=dict)


class ConfigSection(BaseModel):
    """
    Base class for all config sections.

    Ensures fields accept arbitrary types and validate the pydantic model
     on re-assignment, and unknown fields raise.
     Supports plain assignment (`section.foo = "bar"`) and multi-path awaitable
     (`await section.set(foo="bar", **options)`).
     Iterating yields fields through (name, value).
     Supports dict operators OR and IN: `if field in (section1 | section2)`.
    """

    # Pydantic internal
    model_config = _MUTABLE

    # Paths across sections are walked using their `name` and `parent` references.
    # NOTE: Both of these are set post-construction inside `_stamp_parent_on_subsections`
    # The name of the field pointing to this section
    _name: str = PrivateAttr(default="")
    # The section owning this one
    _parent: ConfigSection | None = PrivateAttr(default=None)

    # observers for field changes: (callback, paths_filter | None)
    _observers: list[tuple[ConfigObserver, frozenset[str] | None]] = PrivateAttr(
        default_factory=list
    )

    @property
    def _path(self) -> str:
        if self._parent is None or not self._parent._path:
            return self._name
        return f"{self._parent._path}.{self._name}"

    @model_validator(mode="after")
    def _stamp_parent_on_subsections(self) -> ConfigSection:
        """
        Sets this section as the parent of each subsection it holds.

        Runs on construction AND on assignment (if `validate_assignment==True`),
        so a section swapped in at runtime is bound by the same path as the
        original. There is no post-init step to forget.

        HACK: setting private fields post-construction of subsections is bad.
        """
        for name, value in self:
            if isinstance(value, ConfigSection):
                value._parent = self
                value._name = name
        return self

    def __getitem__(self, name: str) -> Any:
        """Dict-style access by NAME, or by a dotted PATH that navigates.

        `section["api.url"]` is `section["api"]["url"]` is `section.api.url`:
        the dot always means "one level down", so a name carrying one is
        unreachable here — declared fields cannot carry dots, and dynamic
        entries refuse them (`DynamicSection._set`). Missing names raise
        `KeyError`: the dict protocol's own failure (`PathNotFound` stays the
        path API's)."""
        section, _dot, rest = name.partition(".")
        if section not in self:
            raise KeyError(name)
        value = getattr(self, section)
        if not rest:
            return value
        if not isinstance(value, ConfigSection):
            raise KeyError(name)
        try:
            return value[rest]
        except KeyError:
            raise KeyError(name) from None

    def __setitem__(self, name: str, value: Any) -> None:
        """Dict-style assignment by NAME, or by a dotted PATH: navigate to the
        path's owner and assign there, so `section["api.url"] = ...` carries
        exactly the semantics — including the async-observer refusal — of
        `section.api.url = ...`. A missing intermediate section raises
        `KeyError`, never creates one: `[]` writes into the tree, it does not
        declare structure."""
        parent, _dot, leaf = name.rpartition(".")
        target = self[parent] if parent else self
        setattr(target, leaf, value)

    # NOTE: hidden from type checkers. A visible `__setattr__(name: str, ...)`
    #  tells them any attribute may be assigned, so `config.api.modle = "x"`
    #  would type-check. Hidden, they check writes against the declared fields.
    #  The same pattern pydantic uses for its own `__getattr__`.
    if not TYPE_CHECKING:

        def __setattr__(self, name: str, value: Any) -> None:
            """Plain assignment (`config.api.model = "x"`).

            Refuses before applying when the change would reach an async observer,
            because a plain assignment can neither run nor return one: write this
            path with `set` and await what it returns, or `set_and_wait`.
            """
            if name in self.__private_attributes__:
                super().__setattr__(name, value)
                return

            was_present = name in self
            # A write that changes nothing notifies nobody, so it never refuses.
            #  Exact for fields and built entries; a raw dict that would build into
            #  the same entry still asks, and errs loud.
            if not was_present or self[name] != value:
                # The walk reads only the change's path, so these are the written
                #  values, not the entry they would build into.
                change = (
                    Updated(path=name, before=self[name], after=value)
                    if was_present
                    else Created(path=name, value=value)
                )
                reached = [
                    handler
                    for _section, handler, _paths in self._observers_for(change)
                    if inspect.iscoroutinefunction(handler)
                ]
                if reached:
                    raise AsyncObserversNotCalled(
                        name, [getattr(h, "__name__", repr(h)) for h in reached]
                    )
            before = self._set(name, value)
            # What the section holds now, which is not always what was written: a
            #  dynamic entry is built from the value, a validated field is coerced.
            # A name waiting for its type holds the value as written, not an entry.
            after = self[name] if name in self else value
            if before != after:
                # A name that was already there is an update; one that was not is a
                #  creation, and its observer needs the entry that was built.
                async_callbacks = self._notify(
                    Updated(path=name, before=before, after=after)
                    if was_present
                    else Created(path=name, value=after)
                )
                if async_callbacks:
                    # The pre-check approved this write on the promise that no async
                    #  observer would hear it; one registered itself mid-notification.
                    for awaitable in async_callbacks:
                        if inspect.iscoroutine(awaitable):
                            awaitable.close()
                    raise AsyncObserversNotCalled(
                        name,
                        [getattr(a, "__name__", repr(a)) for a in async_callbacks],
                        applied=True,
                    )

    # `model_extra` will be None for static sections, available for dynamic ones.
    # I could have made an override in DynamicSection, but this isn't worse design.
    def __contains__(self, name: str) -> bool:
        return name in type(self).model_fields or name in (self.model_extra or {})

    def __or__(self, other: ConfigSection) -> dict[str, Any]:
        return dict(self) | dict(other)

    @classmethod
    @functools.cache
    def _validation_candidate_class(cls) -> type[Self]:
        """
        Creates a dynamic class as a validation candidate for an altered
        config section.
        """

        # NOTE: `type: ignore` is necessary since mypy refuses a variable as a
        #  base class.
        class ValidationCandidate(cls):  # type: ignore[valid-type, misc]
            # Config inherits BaseSettings, and its constructor reads config
            #  files, env and CLI. We don't want that to happen for validation
            #  candidates. So use BaseModel.__init__ instead, which just
            #  handles the data it is passed.
            __init__ = BaseModel.__init__
            # The Python module path for this class, otherwise it would be
            #  the path for this file.
            __module__ = cls.__module__
            model_config = ConfigDict(
                # Errors use the original class name
                title=cls.model_config.get("title", cls.__name__),
                # `populate_by_name=True` supposedly allows fields with aliases
                #  to be addressable by name. The parent's model_config is merged.
                # TODO: understand this need better
                populate_by_name=True,
            )
            # Disable parent stamping and `model_post_init` checks
            _is_validation_candidate = True

        return ValidationCandidate

    @classmethod
    def walk_schema(cls, path: str) -> Iterator[tuple[str, FieldInfo]]:
        """
        Walks this section's schema depth-first, yielding each visited field's
        (dotted path, field_info), ending at the leaf.

        A segment is a field name, `*` (every field at this level, the rest of
        the path continuing from each) or `**` (every field from here down,
        only as the last segment).
        """
        segment, _delimiter, rest = path.partition(".")
        if segment == "**" and rest:
            raise ValueError(f"'**' must be the last segment: {path!r}")
        names = list(cls.model_fields) if segment in ("*", "**") else [segment]
        for name in names:
            if name not in cls.model_fields:
                raise PathNotFound(cls, name)
            field_info = cls.model_fields[name]
            section = field_info.annotation
            if rest and not _is_section(section):
                # `*.url` skips the fields that can't contain `url`
                if segment == "*":
                    continue
                raise NotASection(name, path)
            yield name, field_info
            # `**` keeps going to the bottom
            next_path = "**" if segment == "**" else rest
            if next_path and _is_section(section):
                for sub_path, sub_info in section.walk_schema(next_path):
                    yield f"{name}.{sub_path}", sub_info

    @classmethod
    def get_schema(cls, path) -> FieldInfo:
        *_rest, (_leaf_name, leaf_field_info) = cls.walk_schema(path)
        return leaf_field_info

    def walk(self, path: str) -> Iterator[tuple[str, Any]]:
        """
        Walks `path` starting from `self` depth-first, yielding each visited
        field's (dotted path, value), ending at the leaf.

        Segments are read like in `walk_schema`, except `*` and `**` also
        visit dynamic entries, which only exist on instances.
        """
        segment, _delimiter, rest = path.partition(".")
        if segment == "**" and rest:
            raise ValueError(f"'**' must be the last segment: {path!r}")
        if segment in ("*", "**"):
            # Iterating a section yields its dynamic entries too
            matches = list(self)
        elif segment in self:
            matches = [(segment, getattr(self, segment))]
        else:
            raise PathNotFound(type(self), segment)
        for name, value in matches:
            is_section = isinstance(value, ConfigSection)
            if rest and not is_section:
                # `*.url` skips the fields that can't contain `url`
                if segment == "*":
                    continue
                raise NotASection(name, path)
            yield name, value
            # `**` keeps going to the bottom
            next_path = "**" if segment == "**" else rest
            if next_path and is_section:
                for sub_path, sub_value in value.walk(next_path):
                    yield f"{name}.{sub_path}", sub_value

    def editable_fields(self) -> dict[str, str]:
        """
        Every editable field below this section, as {dotted path: description}.

        Walks the instance, so typed dynamic entries are included.
        Sections are walked into rather than edited, and a field excluded from
        exports (`exclude=True`) isn't editable, nor is anything inside it.
        Undescribed fields fall back to their path.
        """
        fields: dict[str, str] = {}
        excluded: list[str] = []
        for path, value in self.walk("**"):
            # `**` visits a section before what's inside it
            if any(matches_prefix(path, prefix) for prefix in excluded):
                continue
            _parent, _name, field_info = self.leaf_parent_name_and_schema(path)
            if field_info.exclude:
                excluded.append(path)
                continue
            if not isinstance(value, ConfigSection):
                fields[path] = field_info.description or path
        return fields

    def set(self, *args, **fields: Any) -> list[Awaitable]:
        """
        Write dotted paths and call the sync observers; returns the async
        observers' coroutines for the caller to await.

        `config.api.set(model="x", url="y")` — await what it returns to also run
        the async observers, or `await config.api.set_and_wait(model="x")` to have
        that done for you. Notifying can raise exceptions.
        """
        # Each path resolves to the section that holds it, and to the leaf's
        #  name there, so a write is notified through whatever section has observers.
        fields_to_change = fields
        if args:
            if len(args) == 2:
                fields_to_change |= {args[0]: args[1]}
            else:
                raise ValueError(
                    f"`set` expects exactly 2 or no positional arguments, found {len(args)}: {args}."
                )

        # Only paths whose value actually changed are notified about
        changes: list[ConfigChange] = []
        for path, value in fields_to_change.items():
            section, leaf_name, _ = self.leaf_parent_name_and_schema(path)
            was_present = leaf_name in section
            before = section._set(leaf_name, value)
            after = section[leaf_name] if leaf_name in section else value
            if before != after:
                changes.append(
                    Updated(path=path, before=before, after=after)
                    if was_present
                    else Created(path=path, value=after)
                )
        return self._notify(*changes)

    async def set_and_wait(self, *args, **fields: Any) -> None:
        """
        `set`, and also run the async observers it returns.

        The one async surface of this library: every other writer is sync and
        hands the async observers back to the caller.
        """
        pending = self.set(*args, **fields)
        if not pending:
            return
        failures: list[Exception] = []
        for result in await asyncio.gather(*pending, return_exceptions=True):
            if isinstance(result, Exception):
                failures.append(result)
            # Cancellation or similar: propagate, don't collect
            elif isinstance(result, BaseException):
                raise result
        if failures:
            raise ExceptionGroup("Config observers failed", failures)

    def _set(self, name: str, value: Any) -> Any:
        """Writes `self.name = value` and returns the previous value.

        A name this section doesn't hold raises `PathNotFound`; whether the new
        value differs is the caller's question.
        """
        try:
            before = self[name]
        except KeyError as error:
            # If `name` doesn't already exist on a static section, it's not settable
            raise PathNotFound(type(self), name) from error
        else:
            super().__setattr__(name, value)
            self._mark_ancestors_as_set()
            return before

        # # Only apply changes for new names, or new values for existing names
        # if name in self and getattr(self, name) == value:
        #     return False
        # super().__setattr__(name, value)
        # self._mark_ancestors_as_set()
        # return True

    # TODO: review need and usage across project, replace with [] where it makes sense
    def get(self, path: str, *, as_json: bool = False) -> Any:
        """
        Find a config field given its path.

        `as_json` allows serializing the nested Pydantic structure and complex
         objects according to their own `@field_serializer` rules.
        """
        if not as_json:
            *_rest, (_leaf_path, value) = self.walk(path)
            return value
        parent, leaf, _ = self.leaf_parent_name_and_schema(path)
        # Serialize the owner object, return the target
        dumped = parent.model_dump(mode="json", include={leaf})
        return dumped[leaf]

    @classmethod
    def _cli_flags_from_schema(cls, *paths) -> dict[str, list[CLIFlags]]:
        """
        Finds every `CliFlags` declared under this section, mapped by field path.
        :return Mapping of each field's path to its CLI shortcut flags.
        :raises ValueError if 2 fields declare the same shortcut.
        """
        # Generator for fields to check for flags
        fields = (
            ((path, cls.get_schema(path)) for path in paths)
            if paths
            else cls.walk_schema("**")
        )
        path_to_flags: dict[str, list[CLIFlags]] = {}
        claimed_by: dict[str, str] = {}
        # A field excluded from exports can still declare CLI shortcuts
        for path, field_info in fields:
            all_cli_flags = [
                flags
                for flags in field_info.metadata
                if isinstance(flags, CLIFlags)
                # for flag in metadata.flags
            ]
            if not all_cli_flags:
                continue

            # Check whether this flag was previously used, raise on conflicts
            # NOTE: this design allows defining multiple groups of flags, but nothing
            #  prevents them from defining the same flag 5 times, and then the error
            #  message will say "Flag 'x' is claimed by both MyClass and MyClass".
            for cli_flags in all_cli_flags:
                for flag in cli_flags.flags:
                    if flag in claimed_by:
                        raise ValueError(
                            f"Flag {flag!r} is claimed by both "
                            f"{claimed_by[flag]} and {path!r}"
                        )
                    claimed_by[flag] = path
                # Add all the found flags to the result map
                path_to_flags.setdefault(path, []).append(cli_flags)

        return path_to_flags

    def _observers_for(
        self, *changes: ConfigChange
    ) -> list[tuple[ConfigSection, ConfigObserver, tuple[ConfigChange, ...]]]:
        """
        Every (section, observer, changes) triple these changes reach, above and
        below self, from root to each changed path's owner.

        Pure: it calls nothing, so a writer can ask who would hear a change
        before applying it.
        """
        # Who needs notifications about what. Maps a section's python ID to itself
        # and the dict of path->ConfigChange for that section.
        to_notify: dict[int, tuple[ConfigSection, dict[str, ConfigChange]]] = {}
        for change in changes:
            _self, my_own_changes = to_notify.setdefault(id(self), (self, {}))
            my_own_changes[change.path] = change
            # Down: every section between self and the owner of the changed field
            path_to_parent, _delimiter, _leaf_name = change.path.rpartition(".")
            if path_to_parent:
                for sub_path, section in self.walk(path_to_parent):
                    _section, section_paths = to_notify.setdefault(
                        id(section), (section, {})
                    )
                    # Create a new ConfigChange with the path for the sub-section
                    # Ex: change happened at `interface.tui.theme`, there's an observer
                    #  at `interface`, it needs to be notified about `tui.theme`.
                    path_to_notify = change.path.removeprefix(f"{sub_path}.")
                    section_paths[path_to_notify] = replace(change, path=path_to_notify)
            # Up: every section above self
            parent, path_from_leaf_to_parent = self, change.path
            while parent._parent is not None:
                path_from_leaf_to_parent = f"{parent._name}.{path_from_leaf_to_parent}"
                parent = parent._parent
                _parent, paths_for_parent = to_notify.setdefault(
                    id(parent), (parent, {})
                )
                paths_for_parent[path_from_leaf_to_parent] = replace(
                    change, path=path_from_leaf_to_parent
                )

        listeners = []
        for section, changes_by_path in to_notify.values():
            # NOTE: iterates a copy, an observer may register another observer
            for handler, observer_paths in list(section._observers):
                # NOTE: if later we introduce glob pattern semantics to
                #  observer paths, this is the place to parse it
                paths_to_notify = (
                    list(changes_by_path.values())
                    if not observer_paths
                    else [
                        change
                        for change in changes_by_path.values()
                        if any(
                            matches_prefix(
                                change.path, registered_path
                            )  # Below: watching `foo.bar`, change happened in `foo.bar.baz`
                            or matches_prefix(
                                registered_path, change.path
                            )  # Above: watching `foo.bar.baz`, change happened in `foo.bar`
                            for registered_path in observer_paths
                        )
                    ]
                )
                if not paths_to_notify:
                    continue
                listeners.append((section, handler, tuple(paths_to_notify)))
        return listeners

    def _notify(self, *changes: ConfigChange) -> list[Awaitable]:
        """
        Call the sync observers of these changes and collect the async ones.

        Returns the async observers' coroutines, for a caller that can carry them
        to await; raises `ExceptionGroup("Config observers failed", ...)` when a
        sync observer raised. An observer that must be awaited is `async def`.
        """
        pending_async: list[Awaitable] = []
        failures: list[Exception] = []
        for section, handler, paths in self._observers_for(*changes):
            # NOTE: observers are isolated, a failing one doesn't stop the rest
            if inspect.iscoroutinefunction(handler):
                # Calling an async handler only builds the coroutine; its body
                #  runs when the caller awaits it.
                pending_async.append(handler(section, paths))
                continue
            try:
                handler(section, paths)
            except Exception as error:  # noqa: BLE001
                failures.append(error)
        if failures:
            raise ExceptionGroup("Config observers failed", failures)
        return pending_async

    def _mark_ancestors_as_set(self):
        """
        Marks every section above self as explicitly set in the config,
        ensuring an export with `model_dump(exclude_unset=True)` keeps it.
        """
        section = self
        while section._parent is not None:
            section._parent.__pydantic_fields_set__.add(section._name)
            section = section._parent

    def leaf_parent_name_and_schema(
        self, path: str
    ) -> tuple[ConfigSection, str, FieldInfo]:
        """
        Resolves `path` into a leaf, returns its parent, name and FieldInfo.
        Raises AttributeError raised by `walk`, or if somehow the penultimate
         segment of `path` isn't a ConfigSection.
        """
        parent_section = self
        # "a.b.c.d" -> "a.b.c", "d"
        path_to_parent, _delimiter, leaf_name = path.rpartition(".")
        if path_to_parent:
            # Exhaust the walk
            *_rest, (_, parent_section) = self.walk(path_to_parent)
            if not isinstance(parent_section, ConfigSection):
                raise NotASection(path_to_parent, path)
        return (
            parent_section,
            leaf_name,
            parent_section.get_schema(leaf_name),
        )  # type(parent_section).model_fields[leaf_name])

    def on_change(self, *paths: str) -> Callable[[ConfigObserver], ConfigObserver]:
        """
        Decorator: register a callback for the changes to provided dotted *paths,
        which may be exact fields or section prefixes. Empty *paths means every change.
        ```python
            @config.on_change("api.model", "api.url")
            async def _on_api_change(config, paths): ...
        ```
        """
        # Validate that registered paths exist, or stop existing
        #  at a DynamicSection
        for path in paths:
            for _subpath in self.walk_schema(path):
                pass

        frozen_paths = frozenset(paths) if paths else None

        def register(fn: ConfigObserver) -> ConfigObserver:
            self._observers.append((fn, frozen_paths))
            return fn

        return register

    def _delete(self, name: str) -> Any:
        """A plain section declares everything it holds, so deletion always raises."""
        if name in type(self).model_fields:
            raise AttributeError(
                f"{name!r} is a declared field of {type(self).__name__!r} and can't be deleted"
            )
        raise PathNotFound(type(self), name)

    def __delattr__(self, name: str) -> None:
        if name in self.__private_attributes__:
            super().__delattr__(name)
        else:
            if name in self:
                # Ask before deleting, so a refusal leaves the entry in place.
                reached = [
                    handler
                    for _section, handler, _paths in self._observers_for(
                        Deleted(path=name, value=self[name])
                    )
                    if inspect.iscoroutinefunction(handler)
                ]
                if reached:
                    raise AsyncObserversNotCalled(
                        name, [getattr(h, "__name__", repr(h)) for h in reached]
                    )
            value = self._delete(name)
            async_callbacks = self._notify(Deleted(path=name, value=value))
            if async_callbacks:
                # Only an observer registering another one mid-notification
                #  can get here; the deletion already happened.
                for awaitable in async_callbacks:
                    if inspect.iscoroutine(awaitable):
                        awaitable.close()
                raise AsyncObserversNotCalled(
                    name,
                    [getattr(a, "__name__", repr(a)) for a in async_callbacks],
                    applied=True,
                )

    def unset(self, *paths: str) -> list[Awaitable]:
        changes = []
        for path in paths:
            section, leaf_name, _ = self.leaf_parent_name_and_schema(path)
            value = section._delete(leaf_name)
            changes.append(Deleted(path=path, value=value))
        return self._notify(*changes)




    ###########################################################################
    # BEGIN Atomic writing
    ###########################################################################

    def _group_changes_by_parent(self, requests: list[ChangeRequest]) -> dict[str, _ChangeGroup]:
        """Group changes by parent section path."""
        changes_by_parent: dict[str, _ChangeGroup] = {}
        for change_request in requests:
            parent_path, leaf_name = split_parent_path_and_leaf(change_request.path)
            # Find the parent section,
            parent_section, _leaf_name, field_info = self.leaf_parent_name_and_schema(change_request.path)
            assert leaf_name == _leaf_name
            try:
                change_group_for_parent = changes_by_parent[parent_path]
            except KeyError:
                # First change for this parent.
                change_group_for_parent = changes_by_parent[parent_path] = _ChangeGroup(
                    parent_path=parent_path,
                    parent_section=parent_section,
                )
            change_group_for_parent.change_requests_by_leaf_name[leaf_name] = change_request
        return changes_by_parent


    @classmethod
    def _refuse_writing_to_frozen_fields(cls, change_requests: dict[str, ChangeRequest]) -> None:
        """
        Raises an error if any change attempts to modify a frozen field, or if
        the section itself is frozen (`model_config["frozen"]`).
        `change_requests` maps leaf names (not paths) to their change requests,
        basically the contents of `_ChangeGroup.change_requests_by_leaf_name`.
        A failure is raised exactly as pydantic does it, indicating failed names.
        """
        # Check if this entire section is frozen, otherwise check per field
        if cls.model_config.get("frozen"):
            error_type = "frozen_instance"
            refused_names = list(change_requests)
        else:
            error_type = "frozen_field"
            refused_names = []
            for leaf_name in change_requests:
                # A leaf name that's not on `model_fields` can still exist
                #  in model_extra for DynamicSection and can't be frozen
                if (
                    (field_info := cls.model_fields.get(leaf_name))
                    and field_info.frozen
                ):
                    refused_names.append(leaf_name)

        # Raise a joint error for frozen fields
        if refused_names:
            line_errors: list[InitErrorDetails] = []
            for leaf_name in refused_names:
                change_request = change_requests[leaf_name]
                line_errors.append({
                    "type": error_type,
                    "loc": (leaf_name,),
                    "input": change_request.value if isinstance(change_request, Assign) else None,
                })
            raise ValidationError.from_exception_data(
                title=cls.__name__,
                line_errors=line_errors,
            )

    @staticmethod
    def _refuse_changes_for_overlapping_paths(change_requests: list[ChangeRequest]) -> None:
        """
        Raise if any changes are for overlapping or identical paths.
        Ex: delete `api`, assign a value to `api.host.url`.
        """
        paths = [request.path for request in change_requests]
        overlapping = [
            (path, other_path)
            for index, path in enumerate(paths)
            for other_path in paths[index + 1 :]
            if (
                matches_prefix(path, other_path)
                or matches_prefix(other_path, path)
            )
        ]
        if overlapping:
            raise OverlappingChangePaths(overlapping)


    def _start_candidate_data(self) -> _CandidateData:
        return _CandidateData(data=dict(self))

    @classmethod
    def _raise_deleting_or_retyping_declared_field(cls, leaf_name: str) -> NoReturn:
        if leaf_name in cls.model_fields:
            raise CantDeleteRetypeDeclaredField(cls, leaf_name)
        raise PathNotFound(cls, leaf_name)

    def _apply_assign(
            self,
            candidate_data: _CandidateData,
            leaf_name: str,
            value: Any
    ) -> Created | Updated | None:
        before = candidate_data.data[leaf_name]
        candidate_data.data[leaf_name] = value
        return None if before == value else Updated(path=leaf_name, before=before, after=value)

    def _apply_delete(self, candidate_data: _CandidateData, leaf_name: str) -> Deleted:
        self._raise_deleting_or_retyping_declared_field(leaf_name)

    def _apply_set_type(self, candidate_data: _CandidateData, leaf_name: str, entry_type: Any) -> Created | Updated | None:
        self._raise_deleting_or_retyping_declared_field(leaf_name)

    def _apply_remove_type(self, candidate_data: _CandidateData, leaf_name: str) -> Deleted:
        self._raise_deleting_or_retyping_declared_field(leaf_name)

    def _candidate_data_from_change_requests(
            self,
            change_requests: dict[str, ChangeRequest],
    ) -> _CandidateData:
        candidate_data = self._start_candidate_data()
        for leaf_name, change_request in change_requests.items():
            event: ConfigChange | None = None
            match change_request:
                case Assign(value=value):
                    event = self._apply_assign(candidate_data, leaf_name, value)
                case Delete():
                    event = self._apply_delete(candidate_data, leaf_name)
                case SetType(entry_type=entry_type):
                    event = self._apply_set_type(candidate_data, leaf_name, entry_type)
                case RemoveType():
                    event = self._apply_remove_type(candidate_data, leaf_name)
                case _:
                    assert_never(change_request)
            if event is not None:
                candidate_data.events_by_leaf[leaf_name] = event
        return candidate_data


    @staticmethod
    def _fix_notification_values(
            events_by_leaf_name: dict[str, ConfigChange],
            candidate: ConfigSection,
            parent_path: str,
    ) -> list[ConfigChange]:
        """
        Takes the notifications for each leaf of a parent, corrects
        their values to reflect post-validation.
        Example that should come up in a test:
        ```
        class ApiSection(ConfigSection):
            port: int = Field(default=8000)

        # The input value is different, but the end value is the same
        ApiSection().port="8000"
        ```
        """
        fixed_notifications: list[ConfigChange] = []
        for leaf_name, event in events_by_leaf_name.items():
            # The paths passed by _apply_* methods are just the leaf name
            path = f"{parent_path}.{leaf_name}" if parent_path else leaf_name
            try:
                value = candidate[leaf_name]
            except KeyError:
                # If the leaf is an untyped entry, validators don't run
                #  on it and the final value is already correct, just
                #  fix the path.
                # NOTE: this means the `case Deleted()` below will never
                #  actually run.
                fixed_notifications.append(replace(event, path=path))
                continue
            match event:
                case Created():
                    fixed_notifications.append(
                        replace(event, path=path, value=value)
                    )
                case Updated(before=before):
                    if value == before:
                        # No real change happened, don't send a notification
                        continue
                    fixed_notifications.append(
                        replace(event, path=path, after=value)
                    )
                case Deleted():
                    fixed_notifications.append(
                        replace(event, path=path)
                    )
        return fixed_notifications


    def _validate_and_commit_changes(self, requests: list[ChangeRequest]):
        """
        Single entrypoint for all updates/deletions to section tree.
        Changes are validated as an atomic batch using validation candidates.
        If any change would fail on a candidate, the entire batch fails.
        Otherwise, the changes are copied from the candidates back to the
         live parent sections.

        NOTE: This solution applies start data to validation candidates, and if
         successful it *copies* final data from candidates to live section.__dict__.
         Because of field/model validators, start data may differ from final data,
         and this solution chooses to only run validators on the candidates, bypassed
         on the live sections. It also means running the parent stamp again.
         There's an argument to re-apply the start data on the live sections if
         validation passes, which would run validators again, but would follow the
         normal assignment flow.
        """

        # HACK: local import to avoid import cycle.
        # validation.py import static.py so validation candidates can inherit ConfigSection,
        #  and static.py imports validation.py here to build a candidate from change request data.
        from .validation import candidate_class_from_section_class

        self._refuse_changes_for_overlapping_paths(requests)
        # Group changes by parent path
        changes_by_parent = self._group_changes_by_parent(requests)

        validated_change_group_data_and_candidates: list[tuple[_ChangeGroup, _CandidateData, ConfigSection]] = []

        for change_group in changes_by_parent.values():
            parent_section = change_group.parent_section
            section_changes = change_group.change_requests_by_leaf_name
            # Ensure no change happens on a frozen field/section
            parent_section._refuse_writing_to_frozen_fields(section_changes)
            # Build data for validation candidate by applying changes to parent data
            candidate_data = parent_section._candidate_data_from_change_requests(section_changes)
            # Find all declared fields with changes
            changed_declared_fields = frozenset(
                name for name in section_changes.keys()
                if name in type(parent_section).model_fields
            )
            # Get the validation candidate class that skips validators for unchanged fields
            validation_class = candidate_class_from_section_class(
                section_class=type(parent_section),
                written_field_names=changed_declared_fields,
            )
            # Build the candidate from the candidate class and final data
            validation_candidate = validation_class.model_validate(candidate_data.data)
            # Preserve the change group, the candidate data and the candidate itself
            validated_change_group_data_and_candidates.append(
                (change_group, candidate_data, validation_candidate)
            )



    ###########################################################################
    # END Atomic writing
    ###########################################################################






def _is_section(cls: Any) -> TypeIs[type[ConfigSection]]:
    return isinstance(cls, type) and issubclass(cls, ConfigSection)


# A config observer: (section, the changes it hears about), sync or async.
ConfigObserver = Callable[
    [ConfigSection, tuple[ConfigChange, ...]], Awaitable[None] | None
]
