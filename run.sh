#!/bin/zsh
cd "$(dirname "$0")"
.venv/bin/uvicorn app.main:app --reload --port 8000
