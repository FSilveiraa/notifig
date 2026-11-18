from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager

from pydantic import BaseModel, ValidationError


class PathNotFound(AttributeError):
    def __init__(self, model: type[BaseModel], path: str) -> None:
        super().__init__(f"{model.__name__!r} does not contain {path!r}")


class NotASection(AttributeError):
    def __init__(self, name: str, path: str | None = None) -> None:
        msg = f"{name} is not a section"
        if path:
            msg = f"{path!r}: {msg}"
        super().__init__(msg)


class InvalidEntryName(ValueError):
    def __init__(self, name: str) -> None:
        super().__init__(
            f"{name!r} cannot be an entry name. Dots are used as a delimiter in "
            f"dotted path semantics."
        )


class EntryRequiresData(ValueError):
    """A section was asked to give a name a type that its data cannot build.

    Raised when the entry type declares fields with no default, and the data the
    section has for the entry — nothing, an incomplete block, or another entry's
    own values — carries none for them. A caller that can live without the entry
    catches this and decides what the absence means; a caller that needs the
    entry lets it travel.
    """

    def __init__(
        self,
        model: type[BaseModel],
        name: str,
        missing_fields: Sequence[str],
    ) -> None:
        self.model = model
        self.name = name
        self.missing_fields = list(missing_fields)
        fields = ", ".join(repr(field) for field in self.missing_fields)
        plural = len(self.missing_fields) > 1
        super().__init__(
            f"the {name!r} entry cannot be built as {model.__name__!r}: {fields} "
            f"{'have' if plural else 'has'} no default, and the data available for the "
            f"entry carries no value for {'them' if plural else 'it'}. Declare a "
            f"default, or let {'them' if plural else 'it'} accept absence."
        )


class AsyncObserversNotCalled(RuntimeError):
    """
    At least one async observer was found when attempting to set a value in a
    context that cannot return or call it (ex: `section[name]=value`).
    """

    def __init__(
        self, path: str, callbacks: Sequence[str], applied: bool = False
    ) -> None:
        self.path = path
        self.callbacks = list(callbacks)
        if applied:
            tail = (
                "The change was applied and sync observers were notified; the async "
                "observers were not called and will not run until the next change "
                "to this path."
            )
        else:
            tail = (
                "Nothing was applied. Write this path with `section.set(...)` and "
                "await what it returns, or `await section.set_and_wait(...)`."
            )
        super().__init__(
            f"the {path!r} change reaches async observer(s) {', '.join(self.callbacks)}, "
            f"which a plain assignment can neither run nor return. {tail}"
        )


class FlagIsReserved(ValueError):
    """At least one CLI flag cannot be used."""

    def __init__(self, flags: Sequence[str]):
        super().__init__(
            f"'{','.join(flags)}' {'are' if len(flags) > 1 else 'is'}"
            f" reserved by the CLI parser and can't be used as flags."
        )


class CantDeleteRetypeDeclaredField(AttributeError):
    def __init__(self, model: type[BaseModel], field_name: str) -> None:
        super().__init__(
            f"{field_name!r} is a declared field of {model.__name__!r} and"
            f" can't be deleted or change its type"
        )


class OverlappingChangePaths(ValueError):
    def __init__(self, paths: Sequence[tuple[str, str]]):
        pairs = "\n".join(
            f"  {path!r} and {other_path!r}" for path, other_path in paths
        )
        super().__init__(
            f"Cannot apply changes to the following overlapping paths:\n{pairs}"
        )


@contextmanager
def entry_requires_data_when_fields_are_missing(
    entry_type: type[BaseModel], entry_name: str
) -> Iterator[None]:
    """
    Creates a context that catches a ValidationError; if all the sub-errors are
     about missing fields it raises an `EntryRequiresData`, otherwise re-raises.

    The motivation is exposing a clear message about what failed, which Pydantic
     condenses into a single ValidationError. This way, missing data for a required
     field is raised differently from existing but incorrect data for that field.
    """
    try:
        yield
    except ValidationError as error:
        line_errors = error.errors()
        # At least one item was not about missing data for a required field
        if not line_errors or any(
            line_error["type"] != "missing" for line_error in line_errors
        ):
            raise
        # All items are about missing data for required fields, raise appropriately
        missing_fields = [
            ".".join(str(part) for part in line_error["loc"])
            for line_error in line_errors
        ]
        raise EntryRequiresData(entry_type, entry_name, missing_fields) from error
