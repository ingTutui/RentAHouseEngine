#!/bin/sh
# Radar Affitti: controllo automatico ogni 30 minuti su questo computer (Linux o Mac). Serve Python 3.14.
cd "$(dirname "$0")/.." || exit 1
PY=$(command -v python3.14 || command -v python3)
[ -n "$PY" ] || { echo "Python non trovato: serve la versione 3.14 (https://www.python.org/downloads/)"; exit 1; }
exec "$PY" radar.py --installa-avvio
