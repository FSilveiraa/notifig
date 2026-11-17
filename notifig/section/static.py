from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import replace, dataclass, field
from typing import TYPE_CHECKING, Any, TypeIs, NoReturn, assert_never

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


# What `_validate_changes` hands to `_commit_validated_changes` for each parent
#  section: its change requests, the data the candidate was built from (with
#  the fixed notifications), and the validated candidate.
type _ValidatedChangeGroup = tuple[_ChangeGroup, _CandidateData, ConfigSection]


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
        entries refuse them (`DynamicSection._instantiate_entries`). Missing names raise
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
        parent, leaf = split_parent_path_and_leaf(name)
        target = self[parent] if parent else self
        setattr(target, leaf, value)

    def __delitem__(self, name: str) -> None:
        """Dict-style deletion by NAME, or by a dotted PATH, with exactly the
        semantics of `del section.api.entry`, like `__setitem__`."""
        parent, leaf = split_parent_path_and_leaf(name)
        target = self[parent] if parent else self
        delattr(target, leaf)

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
            self._validate_and_commit_refusing_async_observers(
                Assign(path=name, value=value)
            )

    # `model_extra` will be None for static sections, available for dynamic ones.
    # I could have made an override in DynamicSection, but this isn't worse design.
    def __contains__(self, name: str) -> bool:
        return name in type(self).model_fields or name in (self.model_extra or {})

    def __eq__(self, other: object) -> bool:
        """

        When we create a section, that runs a model validator that does 2 things
         to each of their its (dynamic and declared):
         - sets their _parent to their parent section
         - sets their _name with the name they are mapped to in the parent
        Whenever we try to apply an update to a section, the parent section is copied
         into a validation candidate that skips that model_validator, to avoid the
         real parent section's subsections having their _parent reference changed to
         the candidate. If the update we're trying to apply is for an entry that also
         happens to be a section, that means that new subsection that is built into
         the candidate does not have _name and _parent. And if that update happens to
         contain exactly the same data as the existing subsection, it should be
         ignored, but instead the before-after checks look at both subsections, sees
         their _parent/_name differs, and decides they're different sections, despite
         being exactly the same.
        To avoid this problem, __eq__ skips comparing private attributes, and asserts
         2 sections are equal when their actual data matches.

        NOTE: same problem exists with observers, also fixed by not comparing privates.
        TODO: move observers to top-level root, this only fixes updates, but maintains
         the more serious problem of observers not being copied to a replaced section.

        NOTE: comparing `__dict__` relies on sections never using
         `functools.cached_property`, which stores its value there. pydantic's
         own `__eq__` falls back to comparing only the fields for that reason.
        """
        if not isinstance(other, ConfigSection):
            return NotImplemented
        return (
            type(self) is type(other)
            and self.__dict__ == other.__dict__
            and self.__pydantic_extra__ == other.__pydantic_extra__
        )

    def __or__(self, other: ConfigSection) -> dict[str, Any]:
        return dict(self) | dict(other)

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
            if rest and not _is_section_class(section):
                # `*.url` skips the fields that can't contain `url`
                if segment == "*":
                    continue
                raise NotASection(name, path)
            yield name, field_info
            # `**` keeps going to the bottom
            next_path = "**" if segment == "**" else rest
            if next_path and _is_section_class(section):
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

        requests: list[ChangeRequest] = [
            Assign(path=path, value=value) for path, value in fields_to_change.items()
        ]
        return self._validate_and_commit_changes(requests)

    async def set_and_wait(self, *args, **fields: Any) -> None:
        """`set`, and also run the async observers it returns."""
        await _run_async_observers(self.set(*args, **fields))

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

    def __delattr__(self, name: str) -> None:
        """Plain deletion (`del config.plugins.ghost`).

        Refuses before applying when the change would reach an async observer,
        like plain assignment.
        """
        if name in self.__private_attributes__:
            super().__delattr__(name)
            return
        self._validate_and_commit_refusing_async_observers(Delete(path=name))

    def delete(self, *paths: str) -> list[Awaitable]:
        """
        Delete dynamic entries by dotted path and call the sync observers;
        returns the async observers' coroutines for the caller to await.
        """
        requests: list[ChangeRequest] = [Delete(path=path) for path in paths]
        return self._validate_and_commit_changes(requests)

    async def delete_and_wait(self, *paths: str) -> None:
        """`delete`, and also run the async observers it returns."""
        await _run_async_observers(self.delete(*paths))

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
    ) -> None:
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
        # Copy the dict before to avoid altering it mid-iteration
        for leaf_name, event in dict(events_by_leaf_name).items():
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
                events_by_leaf_name[leaf_name] = replace(event, path=path)
                continue
            match event:
                case Created():
                    events_by_leaf_name[leaf_name] = replace(event, path=path, value=value)
                case Updated(before=before):
                    if value == before:
                        # No real change happened, don't send a notification
                        del events_by_leaf_name[leaf_name]
                    else:
                        events_by_leaf_name[leaf_name] = replace(event, path=path, after=value)
                case Deleted():
                    events_by_leaf_name[leaf_name] = replace(event, path=path)


    def _commit_candidate(
            self,
            candidate: ConfigSection,
            candidate_data: _CandidateData,
    ) -> None:
        """
        Copies every declared field from the validation candidate into
         this live section, then marks those fields as explicitly set,
         marks the parent on each sub-section and marks each ancestor
         section as set.
        The reason it has to repeat so much work that building a config
         normally already does is that the only way to set all final
         values on a section at once, and validate an entire batch before
         writing to the live section, while avoiding re-running validators,
         is to write the final post-validation values into __dict__.
         Also the parent and sub-section stamping is something the validation
         candidate explicitly skips, so it has to be done here.
        NOTE: this assumes CandidateData has had its notifications map fixed
        """
        # Copy declared fields that changed
        # Not just the ones from the changes, since a before model_validator
        #  could have written to a separate field
        for field_name in type(self).model_fields:
            # NOTE: this comparison needs our custom __eq__ to avoid comparing
            #  fields that are should not be set on the candidate like `_parent`
            live_value = self[field_name]
            if (candidate_value := candidate[field_name]) != live_value:
                # If a changed sub-entry is a sub-section, copy their observers
                #  recursing into further sub-sub-sections.
                if _is_section(live_value) and _is_section(candidate_value):
                    candidate_value._take_observers_from(live_value)
                self.__dict__[field_name] = candidate_value
        # Set which fields changed on this section
        # Notification changes only contain the actual changed paths,
        #  after ignoring some, so use them to figure out which fields
        #  actually changed
        for leaf_name, event in candidate_data.events_by_leaf.items():
            if isinstance(event, Deleted) or leaf_name in candidate_data.built_from_defaults:
                self.__pydantic_fields_set__.discard(leaf_name)
            else:
                self.__pydantic_fields_set__.add(leaf_name)
        # Mark ancestors as set and set self as the parent for subsections
        if candidate_data.events_by_leaf:
            self._mark_ancestors_as_set()
        # HACK: mypy refuses treating a model_validator method as a callable without this
        self._stamp_parent_on_subsections()  # type: ignore[operator]


    def _validate_and_commit_changes(self, requests: list[ChangeRequest]) -> list[Awaitable]:
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
        return self._commit_validated_changes(self._validate_changes(requests))

    def _validate_and_commit_refusing_async_observers(self, request: ChangeRequest) -> None:
        """
        The write behind plain assignment and `del`, and their `[]` forms, which
        can neither run nor return an async observer.

        Refuses before committing when the change would reach one. The check
         reads the notifications after validation fixed them, so a write that
         changes nothing never refuses.
        """
        validated_change_groups = self._validate_changes([request])
        notifications = [
            notification
            for _change_group, candidate_data, _candidate in validated_change_groups
            for notification in candidate_data.events_by_leaf.values()
        ]
        reached_async_observers = [
            handler
            for _section, handler, _paths in self._observers_for(*notifications)
            if inspect.iscoroutinefunction(handler)
        ]
        if reached_async_observers:
            raise AsyncObserversNotCalled(
                request.path,
                [getattr(h, "__name__", repr(h)) for h in reached_async_observers],
            )
        pending_async_observers = self._commit_validated_changes(validated_change_groups)
        if pending_async_observers:
            # Only an observer that registered an async one mid-notification can
            #  get here, so the change is already applied.
            for awaitable in pending_async_observers:
                if inspect.iscoroutine(awaitable):
                    awaitable.close()
            raise AsyncObserversNotCalled(
                request.path,
                [getattr(a, "__name__", repr(a)) for a in pending_async_observers],
                applied=True,
            )

    def _validate_changes(self, requests: list[ChangeRequest]) -> list[_ValidatedChangeGroup]:
        """
        Validates the batch on one candidate per parent section, and fixes each
         group's notifications. Raises on the first refusal, and nothing live
         is touched.
        """
        # HACK: local import to avoid import cycle.
        # validation.py import static.py so validation candidates can inherit ConfigSection,
        #  and static.py imports validation.py here to build a candidate from change request data.
        from .validation import candidate_class_from_section_class

        self._refuse_changes_for_overlapping_paths(requests)
        # Group changes by parent path
        changes_by_parent = self._group_changes_by_parent(requests)

        validated_change_group_data_and_candidates: list[_ValidatedChangeGroup] = []

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
            # Fix notifications to have correct paths and values
            self._fix_notification_values(
                events_by_leaf_name=candidate_data.events_by_leaf,
                candidate=validation_candidate,
                parent_path=change_group.parent_path,
            )
            # Preserve the change group, the candidate data and the candidate itself
            validated_change_group_data_and_candidates.append(
                (change_group, candidate_data, validation_candidate)
            )
        return validated_change_group_data_and_candidates

    def _commit_validated_changes(
            self,
            validated_change_group_data_and_candidates: list[_ValidatedChangeGroup],
    ) -> list[Awaitable]:
        """
        Copies every validated candidate into its live section, then notifies
         once for the whole batch. Nothing here can refuse.
        """
        all_notifications: list[ConfigChange] = []
        for change_group, candidate_data, validation_candidate in validated_change_group_data_and_candidates:
            change_group.parent_section._commit_candidate(
                candidate=validation_candidate,
                candidate_data=candidate_data,
            )
            all_notifications.extend(candidate_data.events_by_leaf.values())

        return self._notify(*all_notifications)

    def _take_observers_from(self, other: ConfigSection):
        """
        Copies the observers from another section, recursing into sub-sections.
        Useful when a section gets replaced.
        """
        self._observers.extend(other._observers)
        # NOTE: should we do this? Seems too opinionated.
        other._observers.clear()
        for name, value in self:
            try:
                other_value = other[name]
            except KeyError:
                continue
            # if this value is a sub-section in both this section and
            # the other one, recurse inside it and copy its observers
            if _is_section(value) and _is_section(other_value):
                value._take_observers_from(other_value)


async def _run_async_observers(pending: list[Awaitable]) -> None:
    """
    Awaits the async observers a sync writer returned. Every `_and_wait`
     method is its writer followed by this.
    """
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


def _is_section_class(cls: Any) -> TypeIs[type[ConfigSection]]:
    return isinstance(cls, type) and issubclass(cls, ConfigSection)

def _is_section(cls: Any) -> TypeIs[ConfigSection]:
    return isinstance(cls, ConfigSection)


# A config observer: (section, the changes it hears about), sync or async.
ConfigObserver = Callable[
    [ConfigSection, tuple[ConfigChange, ...]], Awaitable[None] | None
]
