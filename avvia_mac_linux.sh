#!/bin/sh
# Controlla ogni 30 minuti finche' la finestra del Terminale resta aperta (Ctrl+C per fermare)
cd "$(dirname "$0")" && exec python3 radar.py --loop 30
