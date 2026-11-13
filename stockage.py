"""Base SQLite : mémoire de l'outil (emails déjà lus, annonces déjà vues).

C'est elle qui évite les doublons et qui permet de savoir quand relancer.
Le fichier annonces.db se crée tout seul au premier lancement.
"""
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import List

from extraction import Annonce, normaliser_lien

SCHEMA = """
CREATE TABLE IF NOT EXISTS emails_traites (
    cle        TEXT PRIMARY KEY,   -- dossier:uidvalidity:uid
    traite_le  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS annonces (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    empreinte           TEXT UNIQUE NOT NULL,  -- sert à détecter les doublons
    lien                TEXT NOT NULL,
    lien_normalise      TEXT NOT NULL,
    source              TEXT,
    prix_cc             INTEGER,
    charges_incertaines INTEGER DEFAULT 0,
    surface             REAL,
    arrondissement      INTEGER,
    email_contact       TEXT,
    telephone           TEXT,
    retenue             INTEGER NOT NULL,      -- 1 = correspond à vos critères
    raison              TEXT,
    vue_le              TEXT NOT NULL,
    notifiee_le         TEXT,                  -- NULL = notification Telegram pas encore envoyée
    brouillon           TEXT,                  -- 'oui', 'non' ou message d'erreur
    csv_ecrit           INTEGER DEFAULT 0,
    relance_envoyee     INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_lien ON annonces(lien_normalise);
"""


def maintenant() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Stockage:
    def __init__(self, chemin: Path):
        self.db = sqlite3.connect(str(chemin))
        self.db.row_factory = sqlite3.Row  # accès aux colonnes par leur nom
        self.db.executescript(SCHEMA)

    # ---- emails ---------------------------------------------------------
    def email_deja_traite(self, cle: str) -> bool:
        return self.db.execute("SELECT 1 FROM emails_traites WHERE cle=?", (cle,)).fetchone() is not None

    def marquer_email_traite(self, cle: str) -> None:
        self.db.execute("INSERT OR IGNORE INTO emails_traites VALUES (?, ?)", (cle, maintenant()))
        self.db.commit()

    # ---- annonces -------------------------------------------------------
    def deja_vue(self, a: Annonce) -> bool:
        """Doublon si même empreinte (prix+surface+arrondissement) OU même lien."""
        ligne = self.db.execute(
            "SELECT 1 FROM annonces WHERE empreinte=? OR lien_normalise=?",
            (a.empreinte(), normaliser_lien(a.lien)),
        ).fetchone()
        return ligne is not None

    def ajouter(self, a: Annonce, retenue: bool, raison: str) -> None:
        self.db.execute(
            """INSERT OR IGNORE INTO annonces
               (empreinte, lien, lien_normalise, source, prix_cc, charges_incertaines, surface,
                arrondissement, email_contact, telephone, retenue, raison, vue_le)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (a.empreinte(), a.lien, normaliser_lien(a.lien), a.source, a.prix_cc,
             int(a.charges_incertaines), a.surface, a.arrondissement, a.email_contact,
             a.telephone, int(retenue), raison, maintenant()),
        )
        self.db.commit()

    def a_notifier(self) -> List[sqlite3.Row]:
        """Annonces retenues dont la notification n'a pas encore pu être envoyée."""
        return self.db.execute(
            "SELECT * FROM annonces WHERE retenue=1 AND notifiee_le IS NULL ORDER BY id"
        ).fetchall()

    def marquer_brouillon(self, id_: int, etat: str) -> None:
        self.db.execute("UPDATE annonces SET brouillon=? WHERE id=?", (etat, id_))
        self.db.commit()

    def marquer_notifiee(self, id_: int) -> None:
        self.db.execute("UPDATE annonces SET notifiee_le=? WHERE id=?", (maintenant(), id_))
        self.db.commit()

    def a_ecrire_dans_csv(self) -> List[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM annonces WHERE retenue=1 AND notifiee_le IS NOT NULL AND csv_ecrit=0 ORDER BY id"
        ).fetchall()

    def marquer_csv_ecrit(self, id_: int) -> None:
        self.db.execute("UPDATE annonces SET csv_ecrit=1 WHERE id=?", (id_,))
        self.db.commit()

    def a_relancer(self, heures: int) -> List[sqlite3.Row]:
        """Annonces notifiées il y a plus de `heures` heures, pas encore relancées."""
        limite = (datetime.now() - timedelta(hours=heures)).isoformat(timespec="seconds")
        return self.db.execute(
            "SELECT * FROM annonces WHERE retenue=1 AND notifiee_le IS NOT NULL AND notifiee_le <= ? "
            "AND relance_envoyee=0 ORDER BY id",
            (limite,),
        ).fetchall()

    def marquer_relancee(self, id_: int) -> None:
        self.db.execute("UPDATE annonces SET relance_envoyee=1 WHERE id=?", (id_,))
        self.db.commit()
