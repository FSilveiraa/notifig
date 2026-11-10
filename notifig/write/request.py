"""
The types of change requests that can be applied to a section.
"""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class BaseChangeRequest:
    """A change to a config path."""

    path: str


@dataclass(frozen=True)
class Assign(BaseChangeRequest):
    """Assign a value to a path."""

    value: Any


@dataclass(frozen=True)
class Delete(BaseChangeRequest):
    """Delete an entry inside a dynamic section."""


@dataclass(frozen=True)
class SetType(BaseChangeRequest):
    """Set the type of an entry inside a dynamic section."""

    entry_type: Any


@dataclass(frozen=True)
class RemoveType(BaseChangeRequest):
    """
    Remove the type of an entry inside a dynamic section.
    Data is preserved inside `_unresolved`.
    """


# Type unions are stricter than checking base class instance for things
#  like a `match` where I forget a case
ChangeRequest = Assign | Delete | SetType | RemoveType
