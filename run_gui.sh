#!/usr/bin/env bash
# Launcher for the Xteink X4 / X4 Pro desktop converter.
# Creates the virtual environment and installs dependencies on first run.
set -e
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"

if [ ! -x "$DIR/.venv/bin/python" ]; then
    echo "First run: creating virtual environment in $DIR/.venv ..."
    python3 -m venv "$DIR/.venv"
    "$DIR/.venv/bin/pip" install --quiet -r "$DIR/requirements.txt"
fi

exec "$DIR/.venv/bin/python" "$DIR/gui_converter.py" "$@"
