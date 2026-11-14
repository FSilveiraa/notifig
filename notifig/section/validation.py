import functools
from typing import Any, Self, override

from pydantic import BaseModel, ConfigDict, model_validator

from .static import ConfigSection


class ValidationSection(ConfigSection):
    @override
    def __init__(self, **kwargs: Any) -> None:
        BaseModel.__init__(self, **kwargs)

    @override
    def model_post_init(self, context: Any) -> None:
        return None

    @override
    @model_validator(mode="after")
    def _stamp_parent_on_subsections(self) -> Self:
        return self


# NOTE: we usually don't cache things since it's cheap to rebuild
#  and allows for dynamics, but this is building schema from schema
@functools.cache
def candidate_class_from_section_class(section_class: type[ConfigSection]) -> type[ConfigSection]:
    """Returns a section that can validate writing to `section` atomically."""

    class ValidationCandidate(ValidationSection, section_class):  # type: ignore[valid-type, misc]
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
