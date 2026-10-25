import sys
from os import PathLike
from typing import Annotated, Any, override

from pydantic import Field
from pydantic_settings import (
    BaseSettings,
    CliSettingsSource,
    NoDecode,
    PydanticBaseSettingsSource,
)

from .annotations import AbsolutePath, Delimited
from .changes import ConfigChange, Deleted
from .meta import ConfigDict
from .section.static import ConfigSection
from .sources import ConfigFileSource, save_config
from .utils import nested_include


class Config(BaseSettings, ConfigSection):
    model_config = ConfigDict(
        cli_avoid_json=True,
        cli_exit_on_error=False,
        cli_kebab_case=True,
        cli_implicit_flags=True,
        cli_enforce_required=False,
        default_config_files=[],
        # NOTE: `.env` is picked up by default, pass `env_file=None` to disable.
        # Reasoning: A `.env` is the one file expected to be picked up by default
        #  on any system.
        env_file=".env",
    )

    # Whether to read `sys.argv`. An internal switch, never saved or edited
    parse_cli: bool = Field(default=True, exclude=True)

    # Whether to auto-save the config on every change
    auto_save: bool = Field(
        default=False,
        description="Automatically save config to the latest file",
    )

    # Config file paths read, sorted lowest -> highest priority.
    # Add flags and defaults for this field using:
    #  `ConfigDict(cli_shortcuts={"config_files":["c", "config"]}, default_config_files=["~/config.json"])`
    # NOTE: Remove NoDecode once pydantic-settings PR lands
    config_files: Annotated[list[AbsolutePath], NoDecode, Delimited()] = Field(
        default_factory=list, exclude=True
    )

    @override
    def model_post_init(self, context: Any) -> None:
        super().model_post_init(context)

        @self.on_change()
        def auto_save(
            section: ConfigSection, changes: tuple[ConfigChange, ...]
        ) -> None:
            """Saves the changed paths to the last config file read, if `auto_save` is on.

            The patch is the changed paths only, dumped through the model so each
            field keeps its own serialisation, so a value that came from the
            environment or the command line is never written: it never changed,
            so it is never in `changes`.
            """
            if not self.auto_save:
                return
            if not self.config_files:
                raise ValueError("Cannot auto-save since no config file was read.")

            created_or_updated = []
            removed = []
            for change in changes:
                if isinstance(change, Deleted):
                    removed.append(change.path)
                else:
                    created_or_updated.append(change.path)
            if not created_or_updated and not removed:
                return

            # Serialize the config with only the specified paths
            data = (
                self.model_dump(
                    mode="json",
                    include=nested_include(created_or_updated),
                )
                if created_or_updated
                else {}
            )
            save_config(
                data=data,
                path=self.config_files[-1],
                remove=removed or None,
                patch=True,
            )

    @override
    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Build the settings-source stack. CLI args flow through `cli_parse_args`;
        `--config` flags are split out for ConfigFileSource."""
        # Parse CLI args list. If `parse_cli` is absent from init_kwargs,
        # then it wasn't passed, use field default
        parse_cli: bool | None = init_settings.init_kwargs.get("parse_cli")
        if parse_cli is None:
            parse_cli = settings_cls.model_fields["parse_cli"].get_default()
        argv = sys.argv[1:] if parse_cli else []

        # A key must match the flag name pydantic-settings generated for the field,
        #  which `cli_kebab_case` makes kebab-case: `max_context` -> `max-context`,
        #  a nested field -> `api.max-context`. Values are used verbatim, with no
        #  case conversion, and a one-character value gets a single dash. Expected:
        #  {"path.to.my-section": ["my-section", "m"]}
        #  Creates: `--my-section` and `-m`
        # Shortcuts from `model_config` (for inherited fields), then from each
        #  field's own `CliFlags`. A value may be one flag or a list of them.
        shortcuts: dict[str, list[str]] = {}
        for path, flags in (cls.model_config.get("cli_shortcuts") or {}).items():
            shortcuts[path.replace("_", "-")] = (
                [flags] if isinstance(flags, str) else list(flags)
            )
        for path, all_cli_flags in cls._cli_flags_from_schema().items():
            shortcuts.setdefault(path.replace("_", "-"), []).extend(
                flag for cli_flags in all_cli_flags for flag in cli_flags.flags
            )
        # A flag can only belong to one field, wherever it was declared
        claimed_by: dict[str, str] = {}
        for path, flags in shortcuts.items():
            for flag in flags:
                if flag in claimed_by:
                    raise ValueError(
                        f"Flag {flag!r} is claimed by both "
                        f"{claimed_by[flag]!r} and {path!r}"
                    )
                claimed_by[flag] = path

        # Precedence high->low: init kwargs, CLI, env, then config files.
        return (
            init_settings,
            CliSettingsSource(
                settings_cls,
                cli_parse_args=argv,
                cli_shortcuts=shortcuts,
            ),
            env_settings,
            dotenv_settings,
            ConfigFileSource(settings_cls),
        )

    def save(self, path: str | PathLike | None = None, *, full: bool = False) -> None:
        """
        Write this config to `path`, or to the last file it was read from.

        With `full=True` the entire config is , otherwise only the
        explicitly set entries are written.
        """
        target = path or (self.config_files[-1] if self.config_files else None)
        if target is None:
            raise ValueError(
                "Can't save config: no path was given and no config file was read"
            )
        save_config(self.model_dump(mode="json", exclude_unset=not full), target)
