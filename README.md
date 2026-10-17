# Notifig

**Reactive typed configuration for Pydantic — a config tree that tells you what changed.**

Notifig is a configuration library built on pydantic and pydantic-settings. You
declare a config tree of nested sections; the library loads it from layered
files (JSON/YAML/TOML), env vars and the CLI; and — the part nothing else
offers — **every write notifies the observers that care about it**, so the
runtime reacts to configuration changes instead of re-reading them.

## The shape

```python
from notifig import Config, ConfigSection

class ApiConfig(ConfigSection):
    url: str
    model: str | None = None

class Settings(Config):
    api: ApiConfig
```

- **Sections are components.** A section is a `ConfigSection` — a nested model
  with its own fields, its own validation, and its own position in the tree.
  A module that owns a feature declares its own section; the config tree just
  names them.
- **Dynamic sections hold entries that type themselves.** A
  `DynamicSection[EntryType]` instantiates a config block into an entry the
  moment the block arrives, and re-types it with `set_type(name, model)` when
  the model that owns it becomes known — a config file may hold settings for
  something not yet installed, and the entry waits, visible and editable,
  until it arrives.
- **Writes notify.** Every mutation (`set`, `unset`, `set_type`, `remove_type`,
  plain `section[name] = value`) walks the change to every registered observer
  — above and below the changed section, two-way path matching — and runs it.
  Writes are sync and atomic; async observers' coroutines are handed back to
  the caller (`set_and_wait` is the one `async def`), and a plain assignment
  that would reach an async observer refuses loudly *before changing
  anything* (`AsyncObserversNotCalled`).
- **Sources layer.** Files deep-merge (local over global), env vars and CLI
  flags override, and legacy keys are a hard break with an explicit map rather
  than a silent fallback.

```python
settings.api.model = "new-model"        # sync observers run; async ones are returned
settings["api.url"] = "https://..."     # the same, by name

@settings.on_change("api.model")
def model_changed(section, changes):
    print("the model changed:", changes)
```

## Status

Pre-release. The API is real and battle-tested inside its first application,
but the standalone packaging, CI and test suite are being built here now.

## Install

```bash
pip install notifig
```

Requires Python ≥ 3.13.
