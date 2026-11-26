from typing import Any
from pathlib import Path

import uvicorn
# from fastapi import FastAPI, Request
# from fastapi.responses import PlainTextResponse
from blacksheep import Application, Request, Response, json, text

from .config import ServerConfig
from ..file.browser import FileBrowser


class FileBrowserServer(Application):
    def __init__(self, config: ServerConfig, file_browser: FileBrowser, **kwargs: Any):
        super().__init__(**kwargs)
        self.config = config
        self.file_browser = file_browser
        self._register_routes()
        self._register_error_handlers()


    def _register_routes(self):
        @self.router.get("/")
        async def list_root() -> Response:
            return await read_path(Path("."))

        @self.router.get("/files/{path:path}")
        async def read_path(path: str) -> Response:
            entries = self.file_browser.list_directory(path)
            return json([str(entry) for entry in entries])


    def _register_error_handlers(self):
        # Return a 404 for a missing path
        @self.exception_handler(FileNotFoundError)
        async def file_not_found(
                application: Application,
                request: Request,
                exception: FileNotFoundError
        ) -> Response:
            return text(str(exception), status=404)

        # Return a 403 for an unavailable path
        @self.exception_handler(PermissionError)
        async def permission_error(
                application: Application,
                request: Request,
                exception: PermissionError
        ) -> Response:
            return text(str(exception), status=403)



    def run(self) -> None:
        uvicorn.run(
            self,
            host=str(self.config.listen),
            port=self.config.port,
        )
