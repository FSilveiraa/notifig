## Living state tree

Settings should exist across a mutable state tree.
Each field expresses a need by a project component and should be declared once,
in a single register that [expresses its restrictions and behavior](#declaration-is-behavior),
ideally [structured after the project it serves](#scoped-ownership).

Perhaps the best illustration of this idea comes from `mobx-state-tree`'s [Project Philosophy](https://mobx-state-tree.js.org/intro/philosophy).
It's a completely separate project in a different language and supporting its own features,
which prevents long quotes, but the conceptual connection matters more:

>Central in MST is the concept of a living tree. The tree consists of mutable, but strictly protected
>objects enriched with runtime type information. In other words, each tree has a shape (type information)
>and state (data).


## Declaration is behavior

Validation, serialization, value choices, CLI flags, should follow from writing the field itself.
For example, this statement creates a `colors` field that only accepts certain values and can be
set both with comma-separated values (`--color "blue,red""`) or explicit lists: 

```python
from enum import Enum
from typing import Annotated
from pydantic import Field
from notifig import Choice, Delimited, CLIFlags


class Color(Enum)
    RED   = "#FF0000"
    GREEN = "#00FF00"
    BLUE  = "#0000FF"

COLORS = {name.lower(): color for name, color in Color.__members__.items()}

# Color type with restricted values
ColorType = Annotated[Color, Choice(COLORS)]
# List that can parse/serialize delimited color names (comma by default)
ColorList = Annotated[list[ColorType], Delimited()]
# List of colors with CLI flags
colors: Annotated[
    ColorList,
    CLIFlags(["color", "c"]),  # creates --color/-c CLI flags
] = Field(
    default="green",  # default value
    # info string for --help and CLI errors
    description="Available colors",
)

# Usage
#   YAML: `colors: green,blue`
#   CLI:  `--colors "blue,red"`
```


## Transactional changes

Changes are atomic and almost fully transactional, I am looking into ensuring full
ACID compliance.

- **Atomicity** - Any validation error fails the entire change batch, and the original
state is retained.

- **Consistency** - Values that would break validation are rejected before applying.
Notifig doesn't roll back upon errors, it never commits them.

- **Isolation** - Sections cannot expose a half-done state.
This is ensured for single-threaded usage, however concurrent reading of a section can
potentially view a version of a section in the middle of a post-validation commit.
I'm planning to add a `Lock`-based solution to that, that will ensure middle states can't be seen.

- **Durability** - Changes to a section can be persisted through [auto-saving](./features.md#auto-saving).
However, this currently happens as a reaction to the changes, after the commit step.
Errors that occur during the persistence step don't revert the transaction, and can leave
a failed middle-state in the filesystem. I have plans to fix both of these.

**TODO**: a lot of this is already in [features.Changing](./features.md#changing)
Find the design that works best without re-writing everything. Either split with
design principle (what we want to assure) vs feature (what we do assure), or if they're
too similar write the idea mostly here and reference it in `Changing`.


## Scoped ownership

Notifig was created under the idea that the shape of a config should naturally emerge from,
and structurally match, the shape of the project it is applied to.

Your project is (hopefully) structured into components that are delegated to clear tasks.
Your server needs to know which hosts to accept and which port to listen on.
Your database needs a connection URL and a schema type.
Your logger needs a level and a file path.

Scoped ownership is the design principle that these sections can express their config
needs through their own section, preventing accidental access.
Your logger can't change your server config's URL because it doesn't know it exists.

*This is the single design principle that applies to usage instead of library design,
and it's optional, but highly recommended.
Notifig was built to support a wide range of use-cases, including a single state tree shared
across every component, but was designed with a distributed scoped structure in mind*


## Reactive persistent state

`Config` should be a live representation of the filesystem state, and any changes to
it should immediately inform interested parties.

Adding, changing or deleting an entry should able to trigger behavior instead of relying on periodic checks.
An observer is a callable or coroutine registered to a path, and it's called whenever it is altered.
A simple example where changing a UI's theme causes it to redraw:

```python
from typing import Annotated
from pydantic import Field
from notifig import Config, ConfigSection, ConfigChange, Choice

class UIConfig(ConfigSection):
    theme: Annotated[str, Choice(["dark", "light"])] = Field(default="light")

class Settings(Config):
    ui: UIConfig = Field(default_factory=UIConfig)

config = Settings()

# sync observer that logs every change
@config.on_change()
def print_changes(section: ConfigSection, changes: tuple[ConfigChange]):
    for change in changes:
        print(f"Change of type {change.__class__.__name__} on {change.path}")

# Simple assignment works for sync observers
config.ui.theme = "dark"
config.ui.theme = "light"
        
# async observer that redraws UI theme
@config.on_change("ui.theme")
async def redraw_theme(section: ConfigSection, changes: tuple[ConfigChange]):
    await interface.update_theme(section.ui.theme)

# Async observers require `await` to be called
await config.ui.set_and_wait(theme="dark")
```