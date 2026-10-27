#!/bin/zsh
set -eu
cd "${0:A:h}"
if [[ -x .venv/bin/python ]]; then
  PYTHON=.venv/bin/python
elif [[ -x ../.venv/bin/python ]]; then
  PYTHON=../.venv/bin/python
else
  PYTHON=python3
fi
exec "$PYTHON" -m streamlit run app.py --server.address 127.0.0.1 --server.port 8509
