# PseudoSnake

PseudoSnake is a tool for identifying pseudo-tested statements and methods in Python packages. A pseudo-tested statement is one that is executed by a test suite but whose removal or mutation does not cause any test to fail — meaning the tests provide no real behavioral coverage for that code.

## Features

- Analyze Python packages from PyPI or local paths for pseudo-tested code
- Report methods and statements that are covered but not meaningfully tested
- Integrates with mutation testing via `cosmic-ray`
- Rich terminal output for clear reporting

## Installation

```bash
pip install pseudosnake
```

Or with `uv`:

```bash
uv tool install pseudosnake
```

## Usage

```bash
pseudosnake --project-dir path-to-project --source-dir source-dir-name --test-command "uv run pytest" --num-test-runs 1
```

## Development Setup

This project uses [`uv`](https://github.com/astral-sh/uv) for dependency management.

```bash
# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone and set up the project
git clone https://github.com/hemanialaparthi/pseudosnake
cd pseudosnake
uv sync --dev

# Run the tool
uv run pseudosnake --help
```

### Testing on a Local Project

To test your local changes on another project, install pseudosnake in **editable** mode as a `uv` tool:

```bash
uv tool uninstall pseudosnake
uv tool install -e .
```

Then run it from the target project directory:

```bash
pseudosnake --project-dir . \
            --source-dir <package-name> \
            --test-command "pytest" \
            --num-test-runs 1
```

> **Note:** `--source-dir` should be the **package directory name** (e.g., `gator` for a project whose source lives in `gator/`), not necessarily `src`.
> **Important:** Before running PseudoSnake, first verify that the target project's test suite runs successfully on its own. If the tests fail independently, PseudoSnake will report a baseline error.

## Running Tasks

```bash
# Run all checks and tests
uv run task all

# Run linting
uv run task lint

# Run type checking
uv run task typecheck

# Run tests
uv run task test

# Run tests with coverage
uv run task test-coverage

# Run mutation testing
uv run task cosmic-ray-init
uv run task cosmic-ray-baseline
uv run task cosmic-ray-exec
uv run task cosmic-ray-report
```

## License

MIT
