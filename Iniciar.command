#!/bin/zsh
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then
    python3 -m venv .venv || exit 1
fi
.venv/bin/python app.py
