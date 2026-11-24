<picture>
  <img src="docs/logo.svg" alt="notifig" width="720">
</picture>


Notifig merges config files, CLI and env into a living, reactive, strictly-typed
and persistable state tree for project settings


## Design

- [**Living state tree**](./docs/principles.md#living-state-tree) -
Each setting exists in a single source of truth,
distributed across a structured state register.

- [**Declaration is behavior**](./docs/principles.md#declaration-is-behavior) -
A field's type derives its accepted values and usage.

- [**Transactional changes**](./docs/principles.md#transactional-changes) -
Changes to the config are applied atomically.
Inconsistent states aren't possible.

- [**Scoped ownership**](./docs/principles.md#scoped-ownership) -
Config structure naturally emerges from project structure.
Each component can declare their own section.

- [**Reactive persistent state**](./docs/principles.md#reactive-persistent-state) -
Changes within the config should immediately inform registered observers,
and persist to file through optional auto-saving.


## Install

```bash
pip install notifig
```

Requires Python ≥ 3.13.


## Example

```python
# TODO: add clear concise example that shows most of notifig's features
#  Leave dynamics out, they can get too complicated
```

***TODO**: Ensure all documentation code examples are copy-past runnable, then add link to toy project*


## Features

- [**Multi-path transactions**](./docs/features.md#changing) - change multiple fields atomically,
retaining the original state upon failure.
- [**Aware auto-saving**](./docs/features.md#saving) - a config can save [manually](./docs/features.md#saving)
or through [auto-saving](./docs/features.md#auto-saving), only exporting explicitly set fields.
- [**Observers**](./docs/features.md#observers) - register a callable or awaitable to react to config changes.
- [**Custom annotations**](./docs/annotations.md#annotations) - Notifig introduces several Pydantic
annotations that express behavior from types:
  - [CLIFlags](./docs/annotations.md#cli-flags) - add shortcut CLI flags for a field.
  - [Choice](./docs/annotations.md#choice) - restricts options for a field, mapping names to values.
  - [Delimited](./docs/annotations.md#delimited) - parses and serializes a string like `"a|b|c"`
into a typed `list[T]`.
  - [Link](./docs/annotations.md#link) - one field mirrors another by path reference.
  - [Path](./docs/annotations.md#paths) - annotations for filesystem paths, absolute paths and glob patterns.
- [**Dynamic entries**](./docs/features.md#sections) - `DynamicSection` can add, remove and re-type new entries at runtime.
- **File formats** - supports loading and saving from a wide range of file formats: JSON, YAML, TOML, INI and .env .


## Built on

- [Pydantic](https://pydantic.dev/) - Type foundation.
- [Pydantic Settings](https://pydantic.dev/docs/validation/latest/concepts/pydantic_settings/) -
Loading data from CLI+files+env into a validated model.
- [anyconfig](https://github.com/ssato/python-anyconfig) - File loading and serialization
with wide format support.
