from __future__ import annotations

from collections.abc import Awaitable, Iterator
from types import GenericAlias
from typing import Any, ClassVar, TypeVar, override

from pydantic import (
    BaseModel,
    ConfigDict,
    PrivateAttr,
    TypeAdapter,
    model_serializer,
    model_validator,
)
from pydantic.fields import FieldInfo

from ..exceptions import PathNotFound, CantDeleteRetypeDeclaredField, InvalidEntryName, \
    entry_requires_data_when_fields_are_missing
from ..utils import is_pydantic_model
from ..write.notification import Created, Deleted, Updated
from ..write.request import ChangeRequest, RemoveType, SetType
from .static import _MUTABLE, ConfigSection, _CandidateData, _run_async_observers, _is_section

# Like _MUTABLE, but the section accepts keys it does not declare. A dynamic
#  section keeps its entries there, so they can't be rejected as unknown.
_MUTABLE_ALLOW = _MUTABLE | ConfigDict(
    extra="allow",
)


class DynamicSection[T](ConfigSection):
    """
    Config section that allows inserting, removing and re-typing
    entries at runtime.

    The section expects each entry to be an instance of `T` and will
    try to instantiate it as such as soon as data is available. Data
    `T` refuses raises a `ValidationError`; data that carries no value
    for a field `T` declares with no default raises `EntryRequiresData`,
    so a caller can tell "never given" from "given and wrong". A
    parameter that isn't a model has nothing to instantiate with, so
    entries wait in `_unresolved` as written.

    If `T` is a pydantic BaseModel, then validation will reject fields
    not present in `T`'s schema unless `T` has `extra="allow"` in its
    `model_config`. This allows using `T` as a strict model for dynamic
    sections where entries are of a single known type, and `T` with
    `extra="allow"` for sections with dynamic entries sub-classing `T`.

    It's also possible to force the extra fields to be preserved in an
    entry typed after a model that does not allow extra fields by setting
    `force_preserve_extra` either in the subclass:
    ```python
    class MyDynamicSection(DynamicSection[MyStrictType]):
        force_preserve_extra: ClassVar[bool] = True
    ```
    or in the section inheritance:
    ```
    class MyDynamicSection(DynamicSection[MyStrictType], force_preserve_extra=True):
        ...
    ```
    """

    model_config = _MUTABLE_ALLOW

    # What an entry value is validated into: the `T` in `DynamicSection[T]`,
    #  or that type rebuilt to allow extra keys.
    _entry_type: ClassVar[Any] = Any
    # The `T` in `DynamicSection[T]` as the author wrote it. `_entry_type` may be
    #  that type rebuilt to allow extra keys, and a rebuilt copy refuses an instance
    #  of a subclass of the original, so `model_extra` must accept either.
    _declared_entry_type: ClassVar[Any] = Any
    # Whether extra fields should be preserved on the dynamic entry model
    force_preserve_extra: ClassVar[bool] = False
    # Data waiting for an entry type: written when the parameter isn't a
    #  model, and sent back here by `remove_type`.
    _unresolved: dict[str, Any] = PrivateAttr(default_factory=dict)

    def __init_subclass__(
        cls, *, force_preserve_extra: bool | None = None, **kwargs: Any
    ) -> None:
        """Decide the entry type, and absorb the `force_preserve_extra` keyword.

        Runs while the section's pydantic model is being built, which is the only
        moment a change to its entry type is read.
        """
        super().__init_subclass__(**kwargs)
        if force_preserve_extra is not None:
            cls.force_preserve_extra = force_preserve_extra
        # If `T` is a pydantic model, and we want to force it to allow extra fields,
        #  (to allow entries typed after subclasses of `T`) then rebuild `T` with `extra="allow"`
        # NOTE: `cls.__dict__` asks whether THIS class declares the flag. The plain
        #  attribute would also be true for a section that inherits it, and
        #  that section would then rebuild an already-rebuilt type
        #  (`ToolConfig_Preserving_Preserving`).
        if is_pydantic_model(cls._entry_type) and cls.__dict__.get(
            "force_preserve_extra"
        ):
            # A type that already allows extras needs no copy: the copy's only job is
            #  to let a surplus dict through, and this type lets it through already.
            #  Rebuilding it would add a second class with a synthetic name, visible
            #  in reprs and validation errors, for exactly the same behaviour.
            if cls._entry_type.model_config.get("extra") != "allow":
                # A plain subclass, not `create_model`: the copy needs exactly
                #  one thing from pydantic - that a child's `model_config`
                #  overrides its parent's - and a class statement asks for it
                #  in the way every pydantic 2.x answers, where
                #  `create_model(__base__=..., __config__=...)` is only legal
                #  since 2.11. One less internal to stand on.
                cls._entry_type = type(
                    f"{cls._entry_type.__name__}_Preserving",
                    (cls._entry_type,),
                    {"model_config": ConfigDict(extra="allow")},
                )
            # The entry type of this section. pydantic reads this line while it is
            #  still building the model, and never looks at the value again — a write
            #  from `__pydantic_init_subclass__` is too late to be read at all.
            # NOTE: the union collapses to `T` itself when no copy was needed, and
            #  that is what the permissive `T` accepts: a surplus dict and a subclass
            #  of `T` are both `T` there.
            cls.__annotations__["__pydantic_extra__"] = GenericAlias(
                dict, (str, cls._entry_type | cls._declared_entry_type)
            )

    @override
    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        """
        Runs after the section's pydantic model is built.

        Captures `T` from `DynamicSection[T]` — the only moment the parameter can
        be read. The entry type's own annotation was written by `__init_subclass__`
        while the class was still being built, and pydantic never reads that value
        again, so there is nothing to write here.
        """
        super().__pydantic_init_subclass__(**kwargs)

        # If we got here through `DynamicSection[T]` with `T` being an actual class,
        #  make this class expect dynamic entries (in `model_extra`) to be of type T
        if (class_args := cls.__pydantic_generic_metadata__["args"]) and not isinstance(
            class_args[0], TypeVar
        ):
            cls._entry_type = class_args[0]
            cls._declared_entry_type = class_args[0]

    @model_validator(mode="wrap")
    @classmethod
    def _instantiate_entries(cls, data: Any, handler) -> Any:
        """THE entrypoint a block crosses to become an entry.

        A block is validated against the entry type the moment it arrives -
        at construction (a config file at parse time) and at every assignment
        re-validation - so a malformed block is a loud error where it lands,
        never a latent one. An entry type that is not a pydantic model cannot
        hold a block: those wait in `_unresolved`, as written, until
        `set_type()` gives the section a model to build with.

        Assignment re-validation hands this validator the already-built model
        rather than a dict (writes build through `DynamicSection._apply_assign`); it
        passes through untouched."""
        if isinstance(data, BaseModel) or not isinstance(data, dict):
            # Assignment re-validation sees the built model; non-dict data is
            #  pydantic's business.
            return handler(data)

        declared, pending = {}, {}
        for name, value in data.items():
            if name in cls.model_fields:
                declared[name] = value
            else:
                pending[name] = value

        # An entry's name carries no dots - see `InvalidEntryName`: refused
        #  at the door, not kept as an unreachable object.
        for name in pending:
            if "." in name:
                raise InvalidEntryName(name)

        if is_pydantic_model(cls._entry_type):
            # Build every block NOW, from the entry type: one malformed block
            #  is one loud error, and a valid block is a live entry.
            for name, value in pending.items():
                pending[name] = TypeAdapter(cls._entry_type).validate_python(value)
            return handler({**declared, **pending})

        section = handler(declared)
        for name, value in pending.items():
            section[name] = value
        return section

    @model_serializer(mode="wrap")
    def _save_unresolved_entries(self, handler) -> dict[str, Any]:
        """Write unresolved entries when serializing."""
        return {**self._unresolved, **handler(self)}

    def set_type(self, *args, **types: Any) -> list[Awaitable]:
        """
        Give entries their types: `set_type(my_entry=MyModel)`, or
        `set_type(my_entry, MyModel)` for a single one, mirroring `set`.

        Each new entry is built from the existing value for its name: the entry it
        already holds (re-serialized), else its data in `_unresolved`, else the
        model's own defaults. Entries already of their type are kept, not rebuilt -
        it wouldn't really do anything meaningful and replacing a section would
        clear its observers. Notified once for the whole batch; returns the async
        observers' coroutines for the caller to await.

        An entry the data cannot build raises `EntryRequiresData` when the data
        carries no value for a field its model declares with no default, and
        `ValidationError` when the data is there and the model refuses it. Either
        way the whole batch is refused, and nothing in it is applied.
        """
        types_to_set = types
        if args:
            if len(args) == 2:
                types_to_set |= {args[0]: args[1]}
            else:
                raise ValueError(
                    f"`set_type` expects exactly 2 or no positional arguments, found {len(args)}: {args}."
                )

        requests: list[ChangeRequest] = [
            SetType(path=path, entry_type=entry_type)
            for path, entry_type in types_to_set.items()
        ]
        return self._validate_and_commit_changes(requests)

    async def set_type_and_wait(self, *args, **types: Any) -> None:
        """`set_type`, and also run the async observers it returns."""
        await _run_async_observers(self.set_type(*args, **types))

    def remove_type(self, *names: str) -> list[Awaitable]:
        """
        Send entries back to `unresolved` as untyped data, keeping their values.
        Distinct from `delete()`/`__delattr__`, which also remove the data. Notified
        once for the whole batch; returns the async observers' coroutines.
        """
        requests: list[ChangeRequest] = [RemoveType(path=path) for path in names]
        return self._validate_and_commit_changes(requests)

    async def remove_type_and_wait(self, *names: str) -> None:
        """`remove_type`, and also run the async observers it returns."""
        await _run_async_observers(self.remove_type(*names))

    @property
    def unresolved(self) -> list[str]:
        """Names of entries still waiting for their type."""
        return list(self._unresolved)

    @override
    def __eq__(self, other: object) -> bool:
        """Untyped entries are data too, though they live in a private attribute."""
        if not isinstance(other, DynamicSection):
            return NotImplemented
        return super().__eq__(other) and self._unresolved == other._unresolved

    @override
    @classmethod
    def walk_schema(cls, path: str) -> Iterator[tuple[str, FieldInfo]]:
        """
        Like `ConfigSection.walk_schema`, and a name that isn't declared is a
        dynamic entry of type T. `*` and `**` only cover declared fields:
        dynamic entries exist on instances, not on the class.
        """
        name, _delimiter, rest = path.partition(".")
        # Patterns, or a declared field: the base class walks these
        if name in ("*", "**") or name in cls.model_fields:
            yield from super().walk_schema(path)
            return

        # An undeclared name is a dynamic entry, and the class cannot say which
        #  class that entry is — a subclass carries fields of its own. So the
        #  entry is yielded as the annotated type, and any path below it is
        #  accepted rather than guessed from a type the entry may not be.
        yield name, FieldInfo.from_annotation(cls._entry_type)
        if rest:
            yield f"{name}.{rest}", FieldInfo.from_annotation(Any)

    @override
    def _start_candidate_data(self) -> _CandidateData:
        return _CandidateData(
            data=dict(self),
            unresolved=dict(self._unresolved)
        )

    @override
    def _apply_assign(
            self,
            candidate_data: _CandidateData,
            leaf_name: str,
            value: Any
    ) -> Created | Updated | None:
        """Sets the value of `leaf_name` to `value` in `candidate_data`."""
        try:
            # Declared field or typed dynamic entry
            before = candidate_data.data[leaf_name]
        except KeyError:
            # New dynamic entry, or update for untyped dynamic entry
            try:
                before = candidate_data.unresolved[leaf_name]
                _exists = True
            except KeyError:
                before = None
                _exists = False
            # Default entry type is a pydantic model, rebuild `value` as an instance
            #  pydantic's using type adapter
            if is_pydantic_model(self._entry_type):
                value = TypeAdapter(self._entry_type).validate_python(value)
                candidate_data.data[leaf_name] = value
                candidate_data.unresolved.pop(leaf_name, None)
            # Default entry type is not buildable, keep in unresolved
            else:
                candidate_data.unresolved[leaf_name] = value
            if not _exists:
                return Created(path=leaf_name, value=value)
        else:
            # If it's a dynamic entry and the types don't match, build the
            #  new instance using the type of the existing one.
            # Because if it's a declared entry, pydantic's validators will handle it
            if (
                    leaf_name not in type(self).model_fields
                    and not isinstance(value, type(before))
               ):
                value = TypeAdapter(type(before)).validate_python(value)
            candidate_data.data[leaf_name] = value
        return None if before == value else Updated(path=leaf_name, before=before, after=value)

    @override
    def _apply_delete(self, candidate_data: _CandidateData, leaf_name: str) -> Deleted:
        """Removes `leaf_name` from `candidate_data`."""
        # Refuse deleting a declared field
        if leaf_name in type(self).model_fields:
            raise CantDeleteRetypeDeclaredField(type(self), leaf_name)
        # Try deleting from typed data first, then unresolved data
        try:
            value = candidate_data.data.pop(leaf_name)
        except KeyError:
            try:
                value = candidate_data.unresolved.pop(leaf_name)
            except KeyError:
                raise PathNotFound(type(self), leaf_name) from None
        return Deleted(path=leaf_name, value=value)

    @override
    def _apply_set_type(
            self, candidate_data: _CandidateData, leaf_name: str, entry_type: Any
    ) -> Created | Updated | None:
        """Sets `entry_type` as the type for `leaf_name` in `candidate_data`. """
        # Refuse re-typing a declared field
        if leaf_name in type(self).model_fields:
            raise CantDeleteRetypeDeclaredField(type(self), leaf_name)
        current_entry = candidate_data.data.get(leaf_name)
        # 1. This entry exists and already has a type
        if current_entry is not None:
            if type(current_entry) is entry_type:
                # Same type: keep the built entry, so it keeps its observers
                return None
            else:
                # Dump the entry into raw data
                raw_data = TypeAdapter(type(current_entry)).dump_python(
                    current_entry,
                    mode="json",
                    # Don't include entries built from defaults, the new
                    #  model might have different ones
                    exclude_unset=True,
                )
        # 2. Entry exists as untyped data
        elif leaf_name in candidate_data.unresolved:
            raw_data = candidate_data.unresolved[leaf_name]
        # 3. The entry doesn't exist, but we can try to build a pydantic model from defaults
        elif is_pydantic_model(entry_type):
            raw_data = {}
            candidate_data.built_from_defaults.add(leaf_name)
        else:
            raise PathNotFound(type(self), leaf_name)

        # Try to build an entry from the gathered raw data
        # Use context manager to raise appropriate exception in case anything fails
        with entry_requires_data_when_fields_are_missing(entry_type, leaf_name):
            new_entry = TypeAdapter(entry_type).validate_python(raw_data)
        # The entry is now typed so it moves to `data` and leaves `unresolved` if it's there
        candidate_data.data[leaf_name] = new_entry
        candidate_data.unresolved.pop(leaf_name, None)
        if current_entry is None:
            return Created(path=leaf_name, value=new_entry)
        return Updated(path=leaf_name, before=current_entry, after=new_entry)


    @override
    def _apply_remove_type(self, candidate_data: _CandidateData, leaf_name: str) -> Deleted:
        """Un-types `leaf_name` into `candidate_data.unresolved`."""
        # Refuse removing the type for a declared field
        if leaf_name in type(self).model_fields:
            raise CantDeleteRetypeDeclaredField(type(self), leaf_name)
        # Get entry if it exists (should be in model_extra), dump it
        #  and move it to unresolved.
        try:
            entry = candidate_data.data.pop(leaf_name)
        except KeyError:
            raise PathNotFound(type(self), leaf_name) from None
        candidate_data.unresolved[leaf_name] = TypeAdapter(type(entry)).dump_python(
            entry, mode="json", exclude_unset=True
        )
        return Deleted(path=leaf_name, value=entry)


    @override
    def _commit_candidate(
            self,
            candidate: ConfigSection,
            candidate_data: _CandidateData,
    ) -> None:
        """
        Sets dynamic entries first, then passes the data to ConfigSection
        to set the declared fields and do the rest of the maintenance
        """
        live_dynamic_entries = self.__pydantic_extra__
        candidate_dynamic_entries = candidate.__pydantic_extra__ or {}
        assert live_dynamic_entries is not None
        # Delete from this section the dynamic entries missing from
        #  the candidate. Iterate a copy, to avoid altering the list
        #  we're iterating
        for name in list(live_dynamic_entries.keys()):
            if name not in candidate_dynamic_entries:
                del live_dynamic_entries[name]
        # Add new or updated dynamic entries
        for name, value in candidate_dynamic_entries.items():
            try:
                live_value = live_dynamic_entries[name]
            except KeyError:
                # Missing value, add it
                live_dynamic_entries[name] = value
            else:
                if live_value != value:
                    # Different value, copy observers if it's a section,
                    #  then also add it
                    if _is_section(live_value) and _is_section(value):
                        value._take_observers_from(live_value)
                    live_dynamic_entries[name] = value
        # Copy unresolved entries. The candidate's unresolved is always empty
        #  since we never write to it, use candidate_data.unresolved
        self._unresolved = candidate_data.unresolved
        super()._commit_candidate(candidate, candidate_data)
