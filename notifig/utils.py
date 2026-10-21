import types
import typing
from typing import Any

from pydantic import BaseModel

UNION_ORIGINS = (typing.Union, types.UnionType)

CONTAINER_TYPES = (list, set, frozenset, tuple, dict)

# Not a named kind of value: `Any` and `None` name nothing at all.
UNNAMEABLE = (Any, type(None))


def is_pydantic_model(model: Any) -> bool:
    return isinstance(model, type) and issubclass(model, BaseModel)


def declared_type(annotation: Any) -> Any:
    """The type an annotation declares: `Annotated[X, ...]` and `X | None` both give `X`;
    a list, a dict or a two-type union give the annotation itself.

    Hand it the annotation a rule is written next to, not the attribute's whole
    annotation: `list[Annotated[X, ...]]` is a list, not `X`.
    """
    if hasattr(annotation, "__metadata__"):  # Annotated[X, ...] keeps X first
        annotation = typing.get_args(annotation)[0]
    if typing.get_origin(annotation) in UNION_ORIGINS:
        members = [arg for arg in typing.get_args(annotation) if arg is not type(None)]
        if len(members) == 1:
            return members[0]
    return annotation


def type_name(declared: Any) -> str | None:
    """The word for a declared type in a message: a named kind of value gives its own name
    (`APIType`, `Path`, a NewType, `int`); a container, a union, a `Literal`, `Any`, a
    TypeVar or another `typing` form gives `fallback`."""
    if declared in UNNAMEABLE or typing.get_origin(declared) is not None:
        return None
    if not isinstance(declared, (type, typing.NewType)):
        return None  # a TypeVar, `Self`, `Never`, an instance in slot 0
    return getattr(declared, "__name__", None)
