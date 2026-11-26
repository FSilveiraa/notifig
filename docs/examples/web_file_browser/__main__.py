import asyncio
from .config import WebFileBrowserConfig
from .filesystem.browser import FilesystemBrowser
from .server.server import FileBrowserServer


def run():
    config = WebFileBrowserConfig()
    file_browser = FilesystemBrowser(config.filesystem)
    server = FileBrowserServer(config.server, file_browser)
    server.run()


run()
