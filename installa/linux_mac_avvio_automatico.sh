#!/bin/sh
# Radar Affitti: controllo automatico (intervallo_minuti in config.toml) su questo computer (Linux o Mac). Serve uv.
cd "$(dirname "$0")/.." || exit 1
UV=$(command -v uv || echo "$HOME/.local/bin/uv")
if [ ! -x "$UV" ]; then
    echo "Manca uv, il programma che prepara Python e le librerie."
    echo "Installalo con:  curl -LsSf https://astral.sh/uv/install.sh | sh"
    echo "Poi apri un terminale nuovo e rilancia questo file."
    exit 1
fi
exec "$UV" run python radar.py --installa-avvio
