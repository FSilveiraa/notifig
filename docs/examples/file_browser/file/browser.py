import os
import stat
from pathlib import Path

from .config import FilesystemConfig


class FileBrowser:
    def __init__(self, config: FilesystemConfig):
        self.config = config

    def list_directory(self, path: str | Path) -> list[Path]:
        """Lists visible paths under given path."""
        full_path = Path(path)
        if not full_path.is_absolute():
            full_path = (self.config.root / full_path)
        full_path = full_path.resolve()
        if not full_path.is_relative_to(self.config.root):
            raise PermissionError(
                f"Path {path} goes above config root {self.config.root}."
            )

    def _is_visible(self, path: Path) -> bool:
        """
        Can this path be shown?

        The entire path is matched against ignore patterns,
         and whether it's hidden in the filesystem.
        """
        while path != self.config.root:
            if (
                    (not self.config.show_hidden and self._is_hidden(path))
                    or self._is_ignored(path)
            ):
                return False
            path = path.parent
        return True

    @staticmethod
    def _is_hidden(path: Path) -> bool:
        """Is this path hidden?"""
        # Assume dotfile-means-hidden convention regardless of OS
        if path.name.startswith("."):
            return True
        # Check if the file/dir is hidden in Windows using the confusing
        #  block everyone has been copying from StackOverflow since 2014
        if os.name == "nt":
            if bool(
                    path.lstat().st_file_attributes
                    & stat.FILE_ATTRIBUTE_HIDDEN
            ):
                return True
        return False

    def _is_ignored(self, path: Path) -> bool:
        """Does this path match ignore patterns?"""
        return any(
                path.match(str(pattern))
                for pattern in self.config.ignore
        )

