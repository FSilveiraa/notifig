"""A list written as one delimited string.

    tags:   Annotated[list[str], Delimited()]
    apis:   Annotated[list[Annotated[APIType, Choice(API_TYPES)]], Delimited()]
    search: Annotated[list[PathPattern], Delimited(";")]

The item type decides what a token means and how it is written back. A token
cannot contain the delimiter: there is no quoting or escaping.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from annotated_types import GroupedMetadata
from pydantic import GetCoreSchemaHandler, GetJsonSchemaHandler
from pydantic_core import CoreSchema, core_schema
from pydantic_core.core_schema import SerializerFunctionWrapHandler
from pydantic_settings import NoDecode

DEFAULT_DELIMITER = ","


@dataclass(frozen=True, slots=True)
class _Delimited:
    """Splits a string on the way in, joins the items back on the way out."""

    delimiter: str = DEFAULT_DELIMITER

    def __get_pydantic_core_schema__(
        self, source: Any, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        def split(value: Any) -> Any:
            if not isinstance(value, str):
                return value
            tokens = (token.strip() for token in value.split(self.delimiter))
            return [token for token in tokens if token]

        def join(value: Any, next_handler: SerializerFunctionWrapHandler) -> str:
            return self.delimiter.join(str(item) for item in next_handler(value))

        return core_schema.no_info_before_validator_function(
            split,
            handler(source),
            serialization=core_schema.wrap_serializer_function_ser_schema(
                join,
                return_schema=core_schema.str_schema(),
                when_used="json",
            ),
        )

    def __get_pydantic_json_schema__(
        self, schema: dict[str, Any], handler: GetJsonSchemaHandler
    ) -> dict[str, Any]:
        return handler(core_schema.str_schema())


@dataclass(frozen=True, slots=True)
class Delimited(GroupedMetadata):
    """A list[T] that can validate from, and serialize to, a delimited string."""

    delimiter: str = DEFAULT_DELIMITER

    def __post_init__(self) -> None:
        if not self.delimiter:
            raise ValueError("a delimiter cannot be empty")

    # NOTE: Until https://github.com/pydantic/pydantic-settings/pull/1102 gets
    #  released, Delimited() needs to be annotated next to NoDecode.
    # Now:    value: Annotated[list[str], NoDecode, Delimited()]
    # After:  value: Annotated[list[str], Delimited()]
    def __iter__(self):
        yield NoDecode
        yield _Delimited(self.delimiter)
