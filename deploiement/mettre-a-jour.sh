#!/usr/bin/env bash
# Récupère la dernière version du code sur GitHub et relance l'outil.
#   bash ~/alerte-appart/deploiement/mettre-a-jour.sh
set -euo pipefail
DOSSIER="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$DOSSIER"
git pull --ff-only
.venv/bin/pip install -q -r requirements.txt
.venv/bin/python -m unittest discover tests -q 2>&1 | tail -1
sudo systemctl restart alerte-appart
sleep 3
systemctl --no-pager --lines=5 status alerte-appart || true
