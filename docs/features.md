## Observers

You can react to any change in your config by registering a callable/awaitable (*observer*) 
on a section that will be called whenever a change occurs.
Observers can be sync or async.

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

**NOTE: if any async observer is registered on a certain path, changes to that path must be done through
async methods (`section.*_and_wait/`) to ensure that observer is called.**

## Sections

Notifig exposes two types of sections:

- `ConfigSection` accepts declared fields
- `DynamicSection` also accepts dynamic entries


## Atomic writing

Multiple changes can be applied to a section at once in a transaction.
All changes are validated before applying and returned to the original method if anything fails.

Config sections support 

The methods `set, delete, set_type, remove_type` support
