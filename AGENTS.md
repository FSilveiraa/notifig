# AGENTS.md

Guidance for coding agents working on **notifig**, a reactive, strictly typed
configuration library built on Pydantic and pydantic-settings. A config tree
validates every write, and tells observers what changed.

## Commands

The `justfile` is the single source of truth: CI runs `just ci` and nothing else.

```bash
pip install -e .[dev]      # pytest, pytest-cov, ruff, mypy
just format                # ruff format notifig/ tests/
just check                 # ruff check + mypy notifig/ (both always run)
just test [pytest args]    # pytest with coverage, e.g. `just test tests/test_save.py -k patch`
just ci                    # format --check, check, test: run this before finishing
```

Change a recipe instead of editing `.github/workflows/ci.yml` when you want CI
to run something different.

## Layout

```
notifig/
  __init__.py          public API; everything users import is re-exported in __all__
  config.py            Config: the root (BaseSettings + ConfigSection), sources stack, save, auto-save
  meta.py              ConfigDict: SettingsConfigDict + default_config_files, merge_lists_* options
  changes.py           ConfigChange events: Created / Updated / Deleted (frozen dataclasses)
  exceptions.py        PathNotFound, NotASection, EntryRequiresData, AsyncObserversNotCalled, FlagIsReserved
  sources.py           File loading/merging (anyconfig), atomic save_config, ConfigFileSource
  utils.py             typing/pydantic helpers (declared_type, unwrap_optional, nested_include, ...)
  section/static.py    ConfigSection: paths, walk, set/unset, observers, notification routing
  section/dynamic.py   DynamicSection[T]: runtime entries in model_extra, set_type/remove_type, _unresolved
  annotations/         Choice, CLIFlags, Delimited, Link, Path/AbsolutePath/PathPattern
tests/
  conftest.py          make_settings(), write_files(), set_argv(); the shared tree shapes
  test_pydantic_invariants.py   the dependency-upgrade gate (see below)
  version_floors.txt   minimum dependency pins, used by the CI "floor" job
```

`ignore/` and build artifacts are local-only and untracked. Leave them alone.

## Architecture

- **Sections.** `ConfigSection` is a `BaseModel` with `validate_assignment=True`,
  `arbitrary_types_allowed=True` and `extra="forbid"`. A `model_validator(mode="after")`
  named `_bind` stamps each subsection's `_parent` and `_name`, so binding happens on
  construction *and* on reassignment. Never add a separate post-construction wiring step.
- **Paths.** A dot always means "one level down": `section["api.url"]`,
  `section.get("api.url")`, `set(**{"api.url": ...})`. Dynamic entry names may not
  contain dots, and `InvalidEntryName` is the only place that message is built.
  `walk`/`walk_schema` accept `*` (one level) and `**` (everything below, last segment only).
  Path resolution lives in `leaf_parent_name_and_schema`, so reuse it instead of writing another.
- **Writes and notifications.** Every writer goes through `_set` / `_delete` and then
  `_notify`. `_observers_for` is pure: it computes who would hear a change, both up the
  ancestor chain and down to the change's owner, rewriting each change's path relative to
  every observing section. Observers are isolated: a sync failure is collected into
  `ExceptionGroup("Config observers failed", ...)` and does not stop the others.
- **The write contract** (`tests/test_write_contract.py`):
  - `set()`, `unset()`, `set_type()` and `remove_type()` are sync. They apply the change,
    run the sync observers, and **return** the async observers' coroutines.
  - `set_and_wait()` / `set_type_and_wait()` are the only async surface.
  - Plain assignment, `[]=` and `del` raise `AsyncObserversNotCalled` **before applying**
    when an async observer would be reached.
  - A write that changes nothing notifies nobody, so it never raises.
- **Dynamic sections.** `DynamicSection[T]` keeps its entries in `__pydantic_extra__`.
  When `T` is a model, blocks are instantiated eagerly in `_instantiate_entries`.
  Otherwise they wait as raw data in `_unresolved` until `set_type()` is called.
  `force_preserve_extra` rebuilds `T` as `T_Preserving` with `extra="allow"`, and has to
  do it in `__init_subclass__`, while pydantic is still building the class.
- **Sources.** Precedence, highest first: init kwargs, CLI, env, `.env`, config files
  (`ConfigFileSource`). Files given explicitly must exist, while `default_config_files`
  are read only if present. When several files are read, later ones win and dicts merge
  key by key. Lists are replaced unless `merge_lists_in_files` is set.
- **CLI flags.** These come from `CLIFlags` metadata on the fields themselves plus
  `model_config["cli_shortcuts"]`, are converted to kebab-case, and must be unique across
  the whole tree. `-h`/`--help` are reserved.
- **Saving.** `save_config` writes atomically through a temp file and `os.replace`, and
  keeps the original file's permissions. `Config.save()` writes `exclude_unset` unless
  `full=True`. Auto-save patches only the changed paths (`nested_include`) into the last
  config file read and removes deleted paths. Values that came from env or the CLI are
  never written, because they never appear in `notification.py`.

## Design principles (from the README, and enforced in review)

1. **Declaration is behavior.** A field's annotation fully describes its validation,
   serialization, choices and CLI flags. New features should be expressed as
   annotations or metadata, not as parallel registries keyed by field name.
2. **Single source of truth.** Never add a mapping that has to be kept in sync with field names.
3. **Distributed awareness.** Each component owns its own section.
4. **Reactive state.** Changes push to observers. Nothing should rely on polling.

## Conventions

- Python ≥ 3.13. Use modern syntax: PEP 695 generics (`class Link[T]`), `X | None`,
  `typing.override`, `TypeIs`, `ExceptionGroup`. Add `from __future__ import annotations`
  in modules that already follow that pattern.
- Ruff rules: `E W F I B C4 UP`, line length 88 (E501 is ignored). mypy runs on `notifig/` only.
- Comments and docstrings explain **why**, in full sentences. Use `NOTE:` for
  non-obvious constraints, `HACK:`/`TODO:` where they are honest. Match the density
  of the surrounding code, which is high. Don't strip existing reasoning comments.
- Custom annotations hook into pydantic through `__get_pydantic_core_schema__` and
  call `handler(source)` so they compose (for example `list[Annotated[X, Choice(...)]]`
  inside `Delimited()`). JSON-mode serializers use `when_used="json"`.
- Error messages say what happened and what to do instead. Each exception class
  builds its own message.
- `Delimited` currently yields `NoDecode` itself, and `Config.config_files` still lists
  `NoDecode` explicitly. Both are workarounds that are waiting on a pydantic-settings fix
  (see the NOTEs in the code). Keep them until that fix lands.
- Public API changes: export from `notifig/__init__.py` and add to `__all__`.
- Don't bump `version` in `pyproject.toml`: the maintainer decides version bumps.

## Testing

- Build trees with `make_settings()` from `tests/conftest.py`, and add new shared
  shapes there. Tests never share mutable state.
- **Always construct `Config` subclasses with `parse_cli=False`** unless the test is
  about the CLI. In that case use `set_argv(monkeypatch, ...)`, so pytest's own argv is
  never parsed.
- Use `tmp_path` and `write_files()` for file-source tests. INI and `.env` have their own
  dedicated tests because their shapes don't fit `parametrize`.
- Test names are sentences that describe the behavior
  (`test_plain_assignment_refuses_before_applying_when_async_observers_reach`).
- `tests/test_pydantic_invariants.py` asserts the pydantic internals notifig relies on
  (generic metadata, `__pydantic_extra__` typing, `validate_assignment` behavior).
  If it fails after a dependency bump, read the test's tier: **T1** means block the
  bump, **T2** means adapt quietly if the meaning holds.
- Tests must pass against the *installed* package too: the release job runs a copy of
  `tests/` against the sdist, outside the repo.

## Dependencies and releases

- Version ranges in `pyproject.toml` are a support policy, and CI tests both ends:
  - `floor` job: Python 3.13 with `-c tests/version_floors.txt`
  - `recommended` job: Python 3.14 with the latest dependencies
  - `rolling` job: `3.x` with the latest dependencies
- If you raise a floor, update `pyproject.toml`, `tests/version_floors.txt` and the
  comment explaining it, all together.
- Releases: bump `version` in `pyproject.toml`, then push tag `v<version>`. The release
  workflow refuses a tag that doesn't match, smoke-tests the sdist, and publishes to PyPI
  through trusted publishing. Don't tag or push without the maintainer asking.
