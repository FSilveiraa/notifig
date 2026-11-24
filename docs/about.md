## Glossary

I use these terms a lot across the documentation, so it's good to clarify them:

- **Config** - a tree of nested `ConfigSection` rooted in a `Config`.
- **Section** - a `ConfigSection`, node in the config tree, containing entries.
- **Entry** - A section field, accessible through `section.<name>`, `section["<name>"]`.
Can be either a declared field or a dynamic entry.
- **Declared field** - a declared field of a section, written in code, known at declaration time.
- **Dynamic entry** - a non-declared section entry that can live as pure data or as an instance.
- **Observer** - a callable or awaitable registered to react to changes on a path relative to section. 


## FAQ

### What is notifig?

A Python library that lets you define a configuration tree that matches a project structure.
It instantiates it from your environment files and variables, and react to its atomic changes.

### Why should I use notifig?

Notifig supports virtually any feature you could expect from a config library,
and introduces several you might not have considered.
It naturally fits into most project structures, with the same convenience of the `dict`
or `dataclass` you're probably relying on right now, while adding meaningful guarantees
and behavior.

At the time of writing, no competitor Python library offers everything that Notifig provides,
and few get close.
Support for batched changes in Pydantic models is uncommon, especially combined with full atomicity.
Notifig offers path semantics on top of this, plus reactive observers, dynamic typing, and a
comprehensive test suite that aims to stay upwards of 95% coverage. 

### What is Notifig great at?

- Defining a config that matches your project structure
- Instantiating it by merging and validating your config files, CLI and env
- Maintaining a living, mutable and exportable state for your project 
- Replacing handlers and bookkeeping with type annotations
- Providing path semantics for getting and altering entries on nested structures
- Applying multiple changes in one atomic transaction at runtime
- Ensuring you can react to those changes in a sync or async observer

### What is notifig not great at?

Notifig is not a very performant library.
Serious effort went into ensuring atomicity for multi-path transactions, which requires instantiating
a nested Pydantic `ConfigSection` for any change and validating assignment success.
Cursory testing showed that altering a single value on a Ryzen 7 takes about ~50–80 µs,
~30x-50x times longer than pure Pydantic.
This is still completely acceptable for the huge majority of use-cases, but Notifig should not
be used as a hot cache.

### Did you use AI to create Notifig?

Yes, in fact Notifig emerged because I needed a proper Pydantic-based config for my
[Solveig](https://github.com/FSilveiraa/solveig) project for a CLI agent.


I object to having code or comments in my project that I can't understand or defend as optimal
design, so I mostly use AI to discuss and iterate design choices.
Almost all the codebase and documentation was written by me on a keyboard,
and I make it clear on commit messages when code is fully or partially AI-generated,
along with which model family I used.

**The notable exception are tests. I ensure our testing guidelines and Pytest fixtures make sense,
then mostly delegate test-writing to AI.**

Along Notifig's development I used CLI agents like Claude Code, Hermes, as well as Solveig.
I mostly use models from Anthropic, Mistral, DeepSeek, Google and MoonshotAI,
