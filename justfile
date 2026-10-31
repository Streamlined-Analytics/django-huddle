# Show available commands
list:
    @just --list

# Run the tests
test *ARGS:
    uv run pytest {{ARGS}}

# Run the full Django matrix
tox:
    uvx --with tox-uv tox run

# Format, lint, type check and test
qa:
    uv run ruff format .
    uv run ruff check . --fix
    uv run mypy src/ tests/ example/
    uv run pytest

# Run the example project under ASGI (websockets included)
demo:
    uv run python manage.py migrate
    uv run daphne example.asgi:application

# Build the sdist and wheel
build:
    rm -rf dist
    uv build
