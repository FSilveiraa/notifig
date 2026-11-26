from pydantic import Field

from notifig import Config

from .file import FilesystemConfig
from .server import ServerConfig
from .user import UsersConfig



class WebFileBrowserConfig(Config):
    users: UsersConfig = Field(
        description="Registered users. If empty, login is disabled.",
        default_factory=UsersConfig,
    )

    filesystem: FilesystemConfig = Field(
        description="How to watch the filesystem",
        default_factory=FilesystemConfig,
    )

    server: ServerConfig = Field(
        description="Application server",
        default_factory=ServerConfig,
    )