import functools
from dataclasses import replace
from typing import Annotated, Any, Self, override

from pydantic import (
    BaseModel,
    ConfigDict,
    SkipValidation,
    create_model,
    model_validator,
)
from pydantic.fields import FieldInfo

from .static import ConfigSection
from .dynamic import DynamicSection


class ValidationSection(ConfigSection):
    # NOTE: if you declare an explicit `def __init__`, pydantic treats
    #  it as a custom constructor and runs model validators twice.
    #  Avoid this:
    #     def __init__(self, **kwargs: Any) -> None:
    #         BaseModel.__init__(self, **kwargs)
    __init__ = BaseModel.__init__

    @override
    def model_post_init(self, context: Any) -> None:
        return None

    @override
    @model_validator(mode="after")
    def _stamp_parent_on_subsections(self) -> Self:
        return self


class DynamicValidationSection(ValidationSection, DynamicSection):
    """
    This class exists only because DynamicSection has a model_validator
    named `_instantiate_entries` that turns the values of dynamic fields
    into dynamic entries typed after its default entry type.
    For validation candidates, the entry values are already built into
    their final types by the `_apply_*` methods, which we want to keep
    because there the **current** entry type is known.
    We don't want the model validator to try to force this final data into
    the default entry type in the validation candidate, so we override it
    and just adopt the default Pydantic behavior.
    TODO: overall review this behavior, consider moving type coercion out
     of a model validator.
    """
    @override
    @model_validator(mode="wrap")
    @classmethod
    def _instantiate_entries(cls, data: Any, handler) -> Any:
        return handler(data)


# NOTE: we usually don't cache things since it's cheap to rebuild
#  and allows for dynamics, but this is building schema from schema
@functools.cache
def _candidate_base_class(section_class: type[ConfigSection]) -> type[ConfigSection]:
    """The candidate behaviour every batch on `section_class` shares,
    whatever fields it writes."""

    validation_section_class = (
        DynamicValidationSection
        if issubclass(section_class, DynamicSection)
        else ValidationSection
    )

    class ValidationCandidate(validation_section_class, section_class):  # type: ignore[valid-type, misc]
        # Copy the python module path
        __module__ = section_class.__module__
        # NOTE: use pydantic's own ConfigDict, ours only has extras that can
        #  only make something break
        model_config = ConfigDict(
            # Use `or` instead of get's default, explicit `title=None`
            #  should be respected
            title=section_class.model_config.get("title") or section_class.__name__,
            # Allows filling values with both names and aliases
            populate_by_name=True,
        )

    return ValidationCandidate


@functools.cache
def candidate_class_from_section_class(
    section_class: type[ConfigSection], written_field_names: frozenset[str]
) -> type[ConfigSection]:
    """Returns a section that can validate writing `written_field_names` of
    `section_class` atomically.

    The candidate receives every field, so model validators judge the whole
    state the batch produces. Only the written fields run their field-level
    validation, though: an unwritten field arrives with its live, already
    validated value, and validating it again would apply a validator that is
    not idempotent (a suffix, a unit conversion) a second time.

    NOTE: pydantic decides which validators run when it builds a class, not
     when it validates data, so the set of written fields is part of the class.
     `written_field_names` is a frozenset so `functools.cache` can key on it.
    """
    # 1. Re-declare every unwritten field so pydantic takes its value as is.
    #  `SkipValidation` skips the type check and every validator inside the
    #  annotation. Clearing the copy's metadata drops the annotated ones
    #  (`AfterValidator`, `Gt`, ...), which the copy would otherwise reattach.
    unwritten_field_definitions: dict[str, Any] = {}
    for field_name, field_info in section_class.model_fields.items():
        if field_name in written_field_names:
            continue
        # A single-argument merge is a copy: the live class's FieldInfo
        #  must not lose its metadata.
        skipped_field_info = FieldInfo.merge_field_infos(field_info)
        skipped_field_info.metadata = []
        unwritten_field_definitions[field_name] = (
            Annotated[field_info.annotation, SkipValidation],
            skipped_field_info,
        )

    # NOTE: create_model, not a class statement, because the field
    #  declarations are only known at runtime.
    candidate_class: type[ConfigSection] = create_model(
        "ValidationCandidate",
        __base__=_candidate_base_class(section_class),
        __module__=section_class.__module__,
        **unwritten_field_definitions,
    )

    # 2. Narrow every `@field_validator` to the written fields.
    #  `SkipValidation` does not stop these: pydantic attaches decorator
    #  validators by field name, outside the annotation.
    # HACK: `__pydantic_decorators__` is not a documented API. Each class owns
    #  its own copy of the table, so editing the candidate's leaves
    #  `section_class` untouched. Guarded by tests/test_pydantic_invariants.py.
    field_validators = candidate_class.__pydantic_decorators__.field_validators
    for method_name, decorator in list(field_validators.items()):
        kept_field_names = tuple(
            name for name in decorator.info.fields if name in written_field_names
        )
        if kept_field_names:
            field_validators[method_name] = replace(
                decorator, info=replace(decorator.info, fields=kept_field_names)
            )
        else:
            del field_validators[method_name]

    # 3. The class's validator was compiled from the table before step 2
    #  edited it, so compile it again.
    candidate_class.model_rebuild(force=True)
    return candidate_class
