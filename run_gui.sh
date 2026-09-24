#!/usr/bin/env bash
# Launcher script for Xteink X4 Pro Desktop GUI Converter
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
"$DIR/.venv/bin/python" "$DIR/gui_converter.py" "$@"
