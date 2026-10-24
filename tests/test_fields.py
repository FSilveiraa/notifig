"""Reading a field's shape, for a caller that has to ask a person for a value.

`unwrap_optional` answers the one question a prompt needs before it can ask
anything: is this field optional, and what is the type behind the optional.
"""

from __future__ import annotations

from pydantic import BaseModel

from notifig import unwrap_optional


def test_unwrap_optional_sees_through_a_bare_union():
    assert unwrap_optional(str | None) == (True, str)
    assert unwrap_optional(str) == (False, str)


def test_unwrap_optional_reads_a_field_that_holds_the_union():
    class Holder(BaseModel):
        maybe: int | None = None
        sure: str = "x"

    assert unwrap_optional(Holder.model_fields["maybe"]) == (True, int)
    assert unwrap_optional(Holder.model_fields["sure"]) == (False, str)
