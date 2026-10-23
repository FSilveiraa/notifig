from __future__ import annotations

import typing
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated, Any

from pydantic import GetCoreSchemaHandler, GetJsonSchemaHandler
from pydantic.fields import FieldInfo
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema, core_schema

from notifig.utils import declared_type, type_name


@dataclass(slots=True, init=False)
class Choice:
    entries: Mapping[str, Any]
    label: str

    def __init__(self, entries: Mapping[str, Any] | Sequence[str], label: str = ""):
        if isinstance(entries, str):
            raise TypeError("Choice takes a mapping or sequence, not a string")
        self.entries = (
            entries
            if isinstance(entries, Mapping)
            else {str(item): item for item in entries}
        )
        self.label = label

    @property
    def options(self) -> list[str]:
        return sorted(self.entries)

    def _name_for_entry(self, entry: Any) -> str:
        """
        Choice expects the entries Mapping to be mutable anytime, entries can
        be added and removed, so the reverse mapping is built on-need.
        """
        for name, value in self.entries.items():
            # NOTE: there's a reason we need both
            if value is entry or value == entry:
                return name
        raise ValueError(f"{entry!r} is not one of {self.options!r}")

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
            name_to_member,  # The function to use over the schema
            handler(source),  # The schema to use it on
            serialization=core_schema.plain_serializer_function_ser_schema(
                self._name_for_entry,  # How to get an entry's name
                return_schema=core_schema.str_schema(),
                when_used="json",
            ),
        )

    def __get_pydantic_json_schema__(
        self, schema: dict[str, Any], handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        json_schema = handler(core_schema.str_schema())
        json_schema.update(type="string", enum=self.options)
        return json_schema

    @classmethod
    def of(cls, field_info: FieldInfo) -> Iterator[tuple[Choice, Any]]:
        """
        Returns an iterator for choices for a FieldInfo.

        If `field_info.metadata` is empty, `field_info.annotation` contains
        the full annotation. Otherwise it's been split into two, with
        `field_info.annotation` containing the type and `field_info.metadata`
        containing the extra annotations.
        """
        if not field_info.metadata:
            return cls._get_choices_from_annotation(field_info.annotation)
        return cls._get_choices_from_annotation(
            Annotated[field_info.annotation, *field_info.metadata]
        )

    @classmethod
    def _get_choices_from_annotation(
        cls, annotation: Any
    ) -> Iterator[tuple[Choice, Any]]:
        """Gets all choices from the full type annotation."""
        for extra in getattr(annotation, "__metadata__", ()):
            if isinstance(extra, cls):
                yield extra, annotation
        for argument in typing.get_args(annotation):
            yield from cls._get_choices_from_annotation(argument)
