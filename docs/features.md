## Sections

Notifig offers two types of sections:

- `ConfigSection` accepts declared fields
- `DynamicSection` also accepts dynamic entries


## Creating

Configs are created by merging multiple data sources (highest to lowest priority):
- CLI args
- Config files
- Environment variables
- `.env` files


## Changing

Multiple changes for different paths can be applied to a section at once in one atomic change.

### Transactions

Changes are almost fully transactional, and I am looking into to ensuring full ACID compliance.

- **Atomicity** - Any validation error fails the entire change batch, and the original state is retained.

- **Consistency** - Values that would break validation are rejected before applying.
Notifig doesn't roll back upon errors, it never commits them.

- **Isolation** - Sections cannot expose a half-done state.
This is ensured for single-threaded usage, however concurrent reading of a section can
potentially view a version of a section in the middle of a post-validation commit.
I'm planning to add a `Lock`-based solution to that, that will ensure middle states can't be seen.

- **Durability** - Changes to a section can be persisted through [auto-saving](#auto-saving).
However, this currently happens as a reaction to the changes, after the commit step.
Errors that occur during the persistence step don't revert the transaction, and can leave
a failed middle-state in the filesystem. I have plans to fix both of these.


### Multi-path changes

The methods `set, delete, set_type, remove_type` - plus their `*_and_wait` wrappers, in case
async observers are registered - allow changing multiple paths at once.

```python
# TODO: add example of plain attribution, __setitem__ and set(**kwargs)
```


## Saving

You can manually save your config by calling `config.save()`:

`def save(self, path: str | PathLike | None = None, *, full: bool = False) -> None`

A `Config` keeps track of which files were used to create it, from its
`ConfigDict.default_config_files` and `--config FILE` args.
When saving, the config will use the passed path if it's provided, otherwise it will save into
the highest-priority (last) file of this list.


### Auto-saving

The choice to auto-save the config lives as a `Config` field itself: `auto_save:bool`
and can be enabled like any other:

- **CLI**  - `--auto-save`
- **Config files** - for example in YAML add `auto_save true`
- **Env** - `AUTO_SAVE=true` inside `.env` or `export AUTO_SAVE=true`.

*TODO: add a section explaining `<ENV_PREFIX>_AUTO_SAVE=true` if you set `ConfigDict(env_prefix=...)`*



## Observers

You can register a callable/awaitable (*observer*) to be called whenever a change occurs within a path.
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
