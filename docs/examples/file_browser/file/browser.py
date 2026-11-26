import os
import stat
from pathlib import Path

from .config import FilesystemConfig


class FileBrowser:
    def __init__(self, config: FilesystemConfig):
        self.config = config


    def list_directory(self, path: str | Path) -> list[Path]:
        """Lists visible paths under given path."""
        # Find the absolute path
        full_path = Path(path)
        if not full_path.is_absolute():
            full_path = (self.config.root / full_path)
        full_path = full_path.resolve()
        # Confirm the path is legal
        self._check_path(full_path)
        # Return the entries if it's a directory, or the full path otherwise
        subpaths = [full_path]
        if full_path.is_dir():
            subpaths = []
            for path in full_path.iterdir():
                try:
                    self._check_visibility(path)
                except PermissionError:
                    continue
                else:
                    subpaths.append(path)
        return subpaths


    def _check_path(self, path: Path) -> None:
        """
        Raises if the path is illegal, matching any of these
        - Escapes the config root.
        - Does not exist in the filesystem.
        - It, or any part between it and the config root, should not be shown.
        """
        # Confirm the path does not escape the config root
        if not path.is_relative_to(self.config.root):
            raise PermissionError(
                f"Path '{path}' goes above config root '{self.config.root}'."
            )
        # Confirm each section of the path can be shown
        ancestor_path = path
        while ancestor_path != self.config.root:
            self._check_visibility(ancestor_path)
            ancestor_path = ancestor_path.parent
        # NOTE: only check this at the end, permissions errors be raised first to avoid letting
        #  a bad actor check if paths exist on the filesystem by matching exception classes
        if not path.exists():
            raise FileNotFoundError(f"'{path}' does not exist.")


    def _check_visibility(self, path: Path) -> None:
        """Checks whether a path can be shown according to the config."""
        if not self.config.show_hidden and self._is_hidden(path):
            raise PermissionError(f"'{path}' is hidden and cannot be shown according to the settings.")
        if self._is_ignored(path):
            raise PermissionError(f"'{path}' matches an ignore pattern.")


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
        """Does this path match the config's ignore patterns?"""
        return any(
                path.match(str(pattern))
                for pattern in self.config.ignore
        )

