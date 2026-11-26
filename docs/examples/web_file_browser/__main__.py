import asyncio
from .config import WebFileBrowserConfig
from .file.browser import FileBrowser
from .server.server import FileBrowserServer


def run():
    config = WebFileBrowserConfig()
    file_browser = FileBrowser(config.filesystem)
    server = FileBrowserServer(config.server, file_browser)
    server.run()


if __name__ == "__main__":
    run()