from __future__ import annotations

import pathlib
from typing import Annotated

from pydantic import BeforeValidator, PlainSerializer

from notifig.utils import absolute_path

Path = Annotated[
    pathlib.Path,
    BeforeValidator(lambda value: pathlib.Path(value)),
    PlainSerializer(str, return_type=str, when_used="json"),
]

AbsolutePath = Annotated[
    pathlib.Path,
    BeforeValidator(lambda value: absolute_path(value)),
    PlainSerializer(str, return_type=str, when_used="json"),
]


def _resolve_pattern(value) -> pathlib.Path:
    path = pathlib.Path(value)
    # Don't expand bare names, unlike AbsolutePath
    if str(path) == path.name:
        return path
    return absolute_path(value)


PathPattern = Annotated[
    pathlib.Path,
    BeforeValidator(_resolve_pattern),
]
