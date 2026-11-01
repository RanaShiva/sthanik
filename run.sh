#!/usr/bin/env bash
# Start Sthanik from a source checkout: the UI + backend on http://127.0.0.1:7860 (the backend launches/swaps the llama.cpp server for
# whichever model a tool needs). Set up once with:  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
# Extra arguments go to the launcher, e.g.  ./run.sh --no-browser   ./run.sh --setup
cd "$(dirname "$0")"
exec .venv/bin/python app/launcher.py "$@"
