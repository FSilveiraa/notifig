## Observers

You can react to any change in your config by registering a callable (*observer*) 
on a section that will be called whenever a change occurs.

Observers are registered with the `@on_change` decorator on a section, and can optionally
be restricted to a path relative to that section, pointing to an entry or sub-section.

The observer will receive the section and changes. Observers can be sync or async, and
any number can be registered on any section for the same path.

```python
from pydantic import Field, HttpUrl
from notifig import Config, ConfigSection, ConfigChange, Updated

class ApiConfig(ConfigSection):
    url: HttpUrl | None = None

class AppConfig(Config):
    api: ApiConfig = Field(default_factory=ApiConfig)

config = AppConfig()

@config.api.on_change("url")
def reconnect_api(section: ConfigSection, changes: tuple[ConfigChange, ...]):
    assert isinstance(change := changes[0], Updated)
    print(f"API URL changed from {change.before!r} to {change.after!r}")

config.api.url = "https://example.com"
# API URL changed from None to HttpUrl('https://example.com/')
```