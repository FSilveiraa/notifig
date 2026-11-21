## Observers

You can react to any change in your config by registering a callable (*observer*) 
on a section that will be called whenever a change occurs.

Observers are registered with the `@on_change` decorator on a section, and can optionally
be restricted to a path relative to that section, pointing to an entry or sub-section.

The observer will receive the section and changes. Observers can be sync or async, and
any number can be registered on any section for the same path.

```python
from pydantic import HttpUrl
from notifig import ConfigSection, Config, Created, Updated, Deleted

class ApiConfig(ConfigSection):
    url: HttpUrl

class AppConfig(Config):
    api: ApiConfig

config = AppConfig()

@config.api.on_change("url")
def reconnect_api(section: ConfigSection, changes: tuple[ConfigChange, ...]):
    for change in changes:
        match change:
            case Created(value=url):
                print(f"API URL set to {url}")
            case Updated(before=old_url, after=new_url):
                print(f"API URL changed from {old_url} to {new_url}")
                # disconnect()
                # connect(new_url)
            case Deleted(value=old_url):
                print(f"API URL unset (was {old_url}")
                # disconnect()
            case _:
                pass
```


## Annotations

Notifig adds several custom type annotations that allow expressing behavior from types:

### Choice

`Choice(choices: dict | Iterable)` restricts the allowed values for an entry.

If `choices` is a dict, it will map each string name to their actual value.
Otherwise, it will restrict values within a passed iterable.

```python
from enum import Enum
from typing import Annotated
from notifig import Config, Choice

class Theme(Enum):
    DARK="dark"
    LIGHT="light"
    HIGH_CONTRAST="high-contrast"
    
THEMES = {
    theme.name.lower(): theme.value
    for theme in Theme
}

class AppConfig(Config):
    theme: Annotated[Theme, Choice(THEMES)]


config = AppConfig(theme="light")
# or set `theme=light` in the config file, pass `--theme light` in the CLI
assert config.theme == Theme.LIGHT
```

### CLI Flags

Any config entry can always be set through its full CLI path (`--path.to.entry`), `CliFlags(flags: list[str])`
adds custom CLI shortcut flags on top.

Single-character-names become short flags (`-p`) while longer names become `--kebab-case` flags.

```python
from typing import Annotated
from notifig import Config, ConfigSection, CLIFlags

class ServerConfig(ConfigSection):
    port: Annotated[int, CLIFlags("p", "port")] = 8080

class AppConfig(Config):
    server: ServerConfig = ServerConfig()

# `port` can now be set on CLI via `--server.port 9000`, `--port 9000`, or `-p 9000`
```

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

config = AppConfig(hosts="api.example.com, db.example.com", ports="8080:8443")
assert config.hosts == ["api.example.com", "db.example.com"]
assert config.ports == [8080, 8443]
```

### Link
`Link(path: str)` links a config entry (*target*) to another entry (*source*) through its path.
Whenever the source changes, so does the target.

It's especially useful to mirror a value across several sub-sections in a distributed config
structure. 

```python
from typing import ClassVar
from pydantic import HttpUrl, SecretStr
from notifig import Config, ConfigSection, Path, Link

class FileLogConfig(ConfigSection):
    level: ClassVar[Link[str]] = Link("/log.level")
    path: Path

class ServerLogConfig(ConfigSection):
    level: Link[str] = Link("/log.level")
    url: HttpUrl
    key: SecretStr

class LogConfig(ConfigSection):
    level: Annotated[str, Choice()] = Field(default="info")
    file: FileLogConfig
    server: ServerLogConfig

class AppConfig(Config):
    log: LogConfig

config = AppConfig()
config.log.level = "error"
assert config.log.server.level == config.log.file.level == "error"
```

**NOTE: Links are currently the only annotation understanding absolute-path semantics through `/`**

### Path

`Path` allows validating and serializing a string entry as a filesystem path,
typed as `pathlib.Path`.
`AbsolutePath` also expands `~` and resolved a relative path into an absolute path.
`PathPattern`

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
