from __future__ import annotations

from dataclasses import dataclass

from notifig.exceptions import FlagIsReserved

# Flags that can't be reserved because they're owned by some library
RESERVED_FLAGS = frozenset(
    {
        # pydantic-settings uses argparse which reserves `-h/--help`
        "h",
        "help",
    }
)


@dataclass(frozen=True, slots=True)
class CLIFlags:
    """
    Represents the CLI flags for a section field.
    Can be set both with `CLIFlags(*flags)` or `CLIFlags([flag, ...])`.
    """

    flags: tuple[str, ...]

    def __init__(self, flag: str | list[str], *flags: str) -> None:
        # NOTE: Not a real hack, this is the documented way to expose an alternative
        #  constructor with a frozen dataclass. The alternative is using `.of(*flags)`
        if isinstance(flag, str):
            all_flags = (flag, *flags)
        elif flags:
            raise ValueError(
                'Specify flags either as a list (`CLIFlags(["c", "config"])`)'
                'or several entries (`CLIFlags("c", "config")`)'
            )
        else:
            all_flags = tuple(flag)
        if reserved := [name for name in all_flags if name in RESERVED_FLAGS]:
            raise FlagIsReserved(reserved)
        object.__setattr__(self, "flags", all_flags)

    def to_argparse(self) -> list[str]:
        return [
            (f"{'-' if len(flag) == 1 else '--'}{flag.replace('_', '-')}")
            for flag in self.flags
        ]
