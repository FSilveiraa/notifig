"""
This project exists to allow a living Config to exist as the single
source-of-truth for every persistent value in a project's settings.

It's built on modern python with strict pydantic typing and
highly declarative, reactive principles. Declaration is behavior.

It can interact with several file formats for saving/loading,
supports both plain python syntax as well as config path semantics,
offers sync and async update notifications, declarative CLI flags,
field values restricted by Choice, static and dynamic section entries,
field links.
"""

from __future__ import annotations

from .annotation import (
    AbsolutePath,
    Choice,
    CLIFlags,
    Delimited,
    Link,
    Path,
    PathPattern,
)
from .config import Config
from .exceptions import (
    AsyncObserversNotCalled,
    CantDeleteRetypeDeclaredField,
    EntryRequiresData,
    InvalidEntryName,
    NotASection,
    OverlappingChangePaths,
    PathNotFound,
)
from .meta import ConfigDict
from .section.dynamic import DynamicSection
from .section.static import ConfigSection
from .sources import FILE_EXTENSIONS, UnknownFormat
from .utils import nested_include, unwrap_optional
from .write.notification import ConfigChange, Created, Deleted, Updated

__all__ = [
    "Config",
    "ConfigDict",
    "ConfigSection",
    "DynamicSection",
    "Choice",
    "CLIFlags",
    "Delimited",
    "Link",
    "Path",
    "AbsolutePath",
    "PathPattern",
    "PathNotFound",
    "NotASection",
    "InvalidEntryName",
    "EntryRequiresData",
    "CantDeleteRetypeDeclaredField",
    "OverlappingChangePaths",
    "AsyncObserversNotCalled",
    "FILE_EXTENSIONS",
    "UnknownFormat",
    "nested_include",
    "unwrap_optional",
    "ConfigChange",
    "Updated",
    "Deleted",
    "Created",
]
