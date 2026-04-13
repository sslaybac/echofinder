# Contributing to Echofinder

## Prerequisites

- **Python 3.11 or later**
- **uv** — [installation instructions](https://docs.astral.sh/uv/getting-started/installation/)
- **libmagic** (Linux only) — `sudo dnf install file-libs` (Alma/RHEL/Fedora) or
  `sudo apt install libmagic1` (Debian/Ubuntu)

## Setup

```bash
git clone <repository-url>
cd echofinder
uv sync
```

`uv sync` installs all Python dependencies (including dev dependencies) into an isolated
virtual environment. No manual `pip install` is needed.

## Running the Application

```bash
uv run python -m echofinder
```

Always use `uv run` — never invoke `python` directly. This ensures the correct
interpreter and virtual environment are used regardless of what is active in the shell.

## Running Tests

```bash
uv run pytest
```

For verbose output:

```bash
uv run pytest -v
```

To run a single test file:

```bash
uv run pytest tests/test_hash_cache.py -v
```

The test suite is Qt-free: no display server or running application is required. Tests
that cover modules importing PyQt6 stub the Qt dependency before import.

## Project Structure

```
echofinder/
├── models/      # Data layer: FileNode, HashCache, scanner, session state
├── services/    # Business logic: hashing engine, file type resolver,
│                #   file operations, polling engine, preview loader
└── ui/          # All PyQt6 widgets and UI components
```

Model-View separation is a hard requirement. Do not put UI logic in the data layer or
data logic in widgets.

## Package Management

Use `uv` exclusively for all package management:

| Task                  | Command                   |
|-----------------------|---------------------------|
| Add a dependency      | `uv add <package>`        |
| Add a dev dependency  | `uv add --dev <package>`  |
| Remove a dependency   | `uv remove <package>`     |
| Sync the environment  | `uv sync`                 |

Never use `pip` directly.

## Development Model

Echofinder is built in discrete stages, each documented in `CLAUDE.md`. Every stage has
a defined scope, completion criteria, and a list of explicitly rejected alternatives.
Before adding a feature, check `CLAUDE.md` — if a similar approach was evaluated and
rejected in a prior stage, the rationale is recorded there.

The `planning/` directory contains design documents and staging context used during
development. It is excluded from the repository (`.gitignore`) and is not required to
run or contribute to the project.

## Code Style

- Standard Python formatting conventions (PEP 8).
- Google-style docstrings on all public classes and methods.
- No external linter configuration is enforced; keep diffs clean and focused.
