#!/bin/sh
# Radar Affitti: controllo automatico ogni 30 minuti su questo computer (Linux o Mac)
cd "$(dirname "$0")/.." && exec python3 radar.py --installa-avvio
