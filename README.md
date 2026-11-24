<picture>
  <img src="docs/logo.svg" alt="notifig" width="720">
</picture>


Notifig merges config files, CLI and env into a living, reactive, strictly-typed
and persistable state tree for project settings


## Design

- [**Living state tree**](./) -
Settings exist across a mutable state register.

- [**Declaration is behavior**](./docs/principles.md#declaration-is-behavior) -
A field's type derives its accepted values and usage.
Illegal states should be impossible.

- [**Transactional changes**](./docs/fea)

- [**Scoped ownership**](./docs/principles.md#scoped-ownership) -
Config structure emerges from project structure,
each component declares their own section.

- [**Reactive state**](./docs/principles.md#reactive-state) -
Changes within the config should immediately inform registered observers.


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

- [**Atomic batches**](./docs/features.md#atomic-writing) - change multiple fields at once, retaining the original state upon failure.
- **[Aware auto-saving](./docs/features.md#saving)** - a config can export [manually](./docs/features.md#saving)
or through [auto-saving](./docs/features.md#auto-saving), and only saves explicitly set fields.
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
