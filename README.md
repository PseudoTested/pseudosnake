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
pseudosnake --project-dir path-to-project --source-dir source-dir-name --test-command "uv run pytest" --num-test-runs 1 --experimental-dynamic-coverage
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
