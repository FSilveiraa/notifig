from __future__ import annotations

import types
import typing
from types import UnionType
from typing import Any, Union

from pydantic import BaseModel
from pydantic.fields import FieldInfo

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


def unwrap_optional(_type: type | FieldInfo) -> tuple[bool, Any]:
    """
    Finds whether `_type` is a union of a type with None.
    Union[X, None] -> (True, X). Anything else -> (False, _type).
    :param _type:
    :return:
    """
    annotation = _type.annotation if isinstance(_type, FieldInfo) else _type
    origin = typing.get_origin(annotation)
    # `X | None` and `Union[X, None]` are the same shape but different objects:
    # PEP 604 gives `types.UnionType`, which is only unified with `typing.Union`
    # from 3.14 on. Both spellings must answer the same here.
    if origin is Union or origin is UnionType:
        non_none = [arg for arg in typing.get_args(annotation) if arg is not type(None)]
        if len(non_none) == 1:
            return (True, non_none[0])
    return (False, annotation)
