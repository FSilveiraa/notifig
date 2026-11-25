from pathlib import Path
from pydantic import Field
from notifig import AbsolutePath, ConfigSection, PathPattern


class FilesystemConfig(ConfigSection):
    root: AbsolutePath = Field(
        description="Where to start listing the filesystem.",
        default=Path.home(),
    )

    ignore: list[PathPattern] = Field(
        description="Path patterns to avoid listing.",
        # By default show all files and directories
        default_factory=list,
    )

    show_hidden: bool = Field(
        description="Show hidden files.",
        default=False,
    )
