from __future__ import annotations

import operator
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import GetCoreSchemaHandler, GetJsonSchemaHandler
from pydantic.fields import FieldInfo
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema, core_schema

from notifig.utils import declared_type, type_name


@dataclass(frozen=True, slots=True)
class Choice:
    entries: Mapping[str, Any]
    # TODO: review, consider repr or something and forcing callers to declare explicit key lambdas
    key: Callable[[Any], str] = operator.attrgetter("name")
    label: str = ""

    @classmethod
    def of(cls, field_info: FieldInfo) -> Choice | None:
        """Returns the Choice for a field, if it exists."""
        for annotation in field_info.metadata:
            if isinstance(annotation, Choice):
                return annotation
        return None

    @property
    def options(self) -> list[str]:
        return sorted(self.entries)

    def __get_pydantic_core_schema__(
        self, source: Any, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        """Defines how to validate and serialize `source` using `handler`."""
        # Find the error label either from explicit setting
        #  or the class name if it's not a built-in
        if not (label := self.label):
            label = type_name(declared_type(source)) or "choice"

        # Converts an entry's name to its mapped value
        def name_to_member(value):
            if not isinstance(value, str):
                return value
            try:
                return self.entries[value]
            except KeyError as error:
                raise ValueError(
                    f"Not a valid {label}. Use one of {self.options}"
                ) from error

        # Create a schema from a before-validator function
        return core_schema.no_info_before_validator_function(
            name_to_member,   # The function to use over the schema
            handler(source),  # The schema to use it on
            serialization=core_schema.plain_serializer_function_ser_schema(
                self.key,  # How to get an entry's name
                return_schema=core_schema.str_schema(),
                when_used="json",
            )
        )

    def __get_pydantic_json_schema__(
        self, schema: dict[str, Any], handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        json_schema = handler(core_schema.str_schema())
        json_schema.update(type="string", enum=self.options)
        return json_schema