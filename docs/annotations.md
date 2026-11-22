## Annotations

Notifig adds several custom type annotations that allow expressing behavior from types:

### Choice

`Choice(choices: dict | Iterable)` restricts the allowed values for an entry.

If `choices` is a dict, it will map each string name to their actual value.
Otherwise, it will restrict values to those within a passed iterable.

```python
from enum import Enum
from typing import Annotated
from notifig import Config, Choice

class Theme(Enum):
    DARK="dark"
    LIGHT="light"
    HIGH_CONTRAST="high-contrast"
    
THEMES = {
    theme.name.lower(): theme
    for theme in Theme
}

class AppConfig(Config):
    theme: Annotated[Theme, Choice(THEMES)]


config = AppConfig(theme="light")
# or set `theme=light` in the config file, pass `--theme light` in the CLI
assert config.theme == Theme.LIGHT
```

### CLI Flags

Any config entry can always be set through its full CLI path (`--path.to.entry`),
`CLIFlags` adds custom CLI shortcut flags on top.

```python
from typing import Annotated
from notifig import Config, ConfigSection, CLIFlags

class ServerConfig(ConfigSection):
    port: Annotated[int, CLIFlags("p", "port")] = 8080

class AppConfig(Config):
    server: ServerConfig = ServerConfig()

# `port` can now be set on CLI via `--server.port 9000`, `--port 9000`, or `-p 9000`
```

`CLIFlags` accepts flags both as varargs (`CLIFlags("p", "port")`) or as a single list
(`CLIFlags(["p", "port"])`).

Single-character-names become short flags (`-p`) while longer names become `--kebab-case` flags.

### Delimited

`Delimited(delimiter: str = ",")` expresses a list of values as a delimited string.

It accepts an optional `delimiter` (defaulting to comma) and uses it to parse an input string
into a list, and serialize it back into a string when saving.

```python
from typing import Annotated
from notifig import Config, Delimited

class AppConfig(Config):
    hosts: Annotated[list[str], Delimited()] = ["localhost"]
    ports: Annotated[list[int], Delimited(":")] = [80, 443]

config = AppConfig(hosts="api.example.com,db.example.com", ports="8080:8443")
assert config.hosts == ["api.example.com", "db.example.com"]
assert config.ports == [8080, 8443]
```

### Link
`Link(path: str)` links a config entry (*target*) to another entry (*source*) through its path.
Whenever the source changes, so does the target.

It's especially useful to mirror a value across several sub-sections in a scoped config
structure. 

```python
from typing import Annotated, ClassVar
from pydantic import Field, HttpUrl, SecretStr
from notifig import Config, ConfigSection, Choice, Link, Path

class FileLogConfig(ConfigSection):
    level: ClassVar[Link[str]] = Link("/log.level")
    path: Path

class ServerLogConfig(ConfigSection):
    level: ClassVar[Link[str]] = Link("/log.level")
    url: HttpUrl
    key: SecretStr

class LogConfig(ConfigSection):
    level: Annotated[str, Choice(["debug", "info", "error"])] = Field(default="info")
    file: FileLogConfig
    server: ServerLogConfig

class AppConfig(Config):
    log: LogConfig

config = AppConfig()
config.log.level = "error"
assert config.log.server.level == config.log.file.level == "error"
```

**NOTES**:
* Links are currently the only annotation understanding absolute-path semantics through `/`
* Link targets must be `typing.ClassVar`.

### Paths

Notifig adds 3 types of Path annotations:
* `Path` validates and serializes a string as a filesystem path,
typed as `pathlib.Path`.
* `AbsolutePath` also expands `~` and resolves a relative path into an absolute path.
* `PathPattern` expands `~` and accepts glob patterns (`*/**`).

```python
import pathlib
from notifig import Config, Path, AbsolutePath

class AppConfig(Config):
    log_file: Path = "logs/app.log"
    cache_dir: AbsolutePath = "~/.cache/app"

config = AppConfig()
assert config.log_file == pathlib.Path("logs/app.log")
assert config.cache_dir.is_absolute()
```
**NOTE: Path annotations do not enforce path existence on the filesystem.**
