#!/usr/bin/env bash
# Installe l'outil sur un serveur Linux (Ubuntu / Debian) et le fait tourner en permanence.
#
# Utilisation, une fois connecté au serveur :
#   git clone https://github.com/<vous>/alerte-appart.git
#   bash alerte-appart/deploiement/installer.sh
#
# Ce que fait le script :
#   1. installe Python, git et le fuseau horaire de Paris ;
#   2. crée un environnement Python dans le dossier du projet et installe les dépendances ;
#   3. crée le service « alerte-appart » : il démarre avec le serveur et redémarre tout seul
#      en cas de plantage (30 s plus tard) ;
#   4. le lance si le fichier .env est déjà là.
# On peut le relancer sans risque (il ne casse rien de ce qui existe).
set -euo pipefail

DOSSIER="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UTILISATEUR="$(id -un)"
SERVICE=/etc/systemd/system/alerte-appart.service

echo "==> Installation des paquets (Python, git, fuseau horaire)"
sudo apt-get update -y -q
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q git python3 python3-venv tzdata
sudo timedatectl set-timezone Europe/Paris 2>/dev/null || true

echo "==> Environnement Python dans $DOSSIER/.venv"
cd "$DOSSIER"
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

echo "==> Vérification du code (tests automatiques)"
.venv/bin/python -m unittest discover tests -q 2>&1 | tail -1

echo "==> Service permanent : $SERVICE"
sudo tee "$SERVICE" >/dev/null <<SERVICE_FIN
[Unit]
Description=Alerte appartement (alertes email -> Telegram)
After=network-online.target
Wants=network-online.target

[Service]
User=$UTILISATEUR
WorkingDirectory=$DOSSIER
Environment=TZ=Europe/Paris
Environment=PYTHONUNBUFFERED=1
ExecStart=$DOSSIER/.venv/bin/python main.py
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
SERVICE_FIN
sudo systemctl daemon-reload
sudo systemctl enable -q alerte-appart

if [ -f "$DOSSIER/.env" ]; then
    chmod 600 "$DOSSIER/.env"
    sudo systemctl restart alerte-appart
    echo "==> Lancé. Voir ce qu'il fait : journalctl -u alerte-appart -f"
else
    echo
    echo "==> Presque fini : il manque le fichier .env (vos identifiants)."
    echo "    Copiez-le depuis votre PC (voir DEPLOIEMENT.md), puis lancez :"
    echo "    sudo systemctl start alerte-appart"
fi
