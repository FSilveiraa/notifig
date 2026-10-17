# Run `just --list` to see all available recipes.

# Paths for lint/format/check: the package and its tests.
src := "notifig/ tests/"

# Auto-format code (pass extra flags, e.g. `just format --check`)
format *flags:
    ruff format {{src}} {{flags}}

# Static checks: ruff lint + mypy (both always run; recipe fails if either did)
check *flags:
    #!/usr/bin/env bash
    fail=0
    ruff check {{src}} {{flags}} || fail=1
    mypy notifig/ || fail=1
    exit $fail

# Run the test suite with coverage (pass extra flags/paths, e.g. `just test tests/test_smoke.py`)
test *flags:
    pytest --cov=notifig --cov-report=term-missing {{flags}}

# Run the full CI suite: format, static checks (lint + mypy), test
ci: format check test
