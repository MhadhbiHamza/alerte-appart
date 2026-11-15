"""Base SQLite : mémoire de l'outil (emails déjà lus, annonces déjà vues).

C'est elle qui évite les doublons et qui permet de savoir quand relancer.
Le fichier annonces.db se crée tout seul au premier lancement.
"""
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

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
    ville               TEXT,                  -- « Paris 13e », « Ivry-sur-Seine »…
    quartier            TEXT,
    email_contact       TEXT,
    telephone           TEXT,
    retenue             INTEGER NOT NULL,      -- 1 = correspond à vos critères
    raison              TEXT,
    vue_le              TEXT NOT NULL,
    notifiee_le         TEXT,                  -- NULL = notification Telegram pas encore envoyée
    brouillon           TEXT,                  -- 'oui', 'non' ou message d'erreur
    csv_ecrit           INTEGER DEFAULT 0,
    relance_envoyee     INTEGER DEFAULT 0,
    colocation          INTEGER DEFAULT 0,
    photo               TEXT,                  -- photo trouvée dans l'email d'alerte
    message_id          INTEGER,               -- numéro du message Telegram
    statut              TEXT,                  -- décision prise avec les boutons Telegram
    candidature         TEXT,                  -- ex. « email envoyé le … »
    envoye_le           TEXT,                  -- date d'envoi du dossier (évite tout double envoi)
    texte               TEXT,                  -- texte de l'annonce dans l'email (pour l'analyse)
    photos              TEXT,                  -- liste JSON des photos de l'email
    verdict             TEXT,                  -- 🟢 À saisir / 🟡 Correct / 🔴 Bof
    note                REAL,                  -- note globale sur 5
    essai               INTEGER DEFAULT 0,     -- 1 = annonce notifiée pour essayer (envois vers vous)
    album_ids           TEXT,                  -- numéros des messages photos (pour les supprimer)
    differee            INTEGER DEFAULT 0,     -- 1 = 🔴 mise de côté pour le résumé du soir
    agent_etat          TEXT,                  -- agents : en_attente / en_cours / pret / fini / erreur
    agent_json          TEXT,                  -- rapport des agents
    agent_message_id    INTEGER,               -- message Telegram du rapport
    texte_complet       TEXT                   -- description complète collée par vous dans Telegram
);
CREATE TABLE IF NOT EXISTS etat (              -- petites valeurs à retenir entre deux lancements
    cle    TEXT PRIMARY KEY,
    valeur TEXT
);
CREATE INDEX IF NOT EXISTS idx_lien ON annonces(lien_normalise);
"""


# Colonnes ajoutées après la première version (ajoutées automatiquement aux anciennes bases).
NOUVELLES_COLONNES = {
    "ville": "TEXT", "quartier": "TEXT", "colocation": "INTEGER DEFAULT 0", "photo": "TEXT",
    "message_id": "INTEGER", "statut": "TEXT", "candidature": "TEXT", "envoye_le": "TEXT",
    "texte": "TEXT", "photos": "TEXT", "verdict": "TEXT", "note": "REAL", "essai": "INTEGER DEFAULT 0",
    "album_ids": "TEXT", "differee": "INTEGER DEFAULT 0",
    "agent_etat": "TEXT", "agent_json": "TEXT", "agent_message_id": "INTEGER", "texte_complet": "TEXT",
}
CHAMPS_MODIFIABLES = {"notifiee_le", "message_id", "statut", "candidature", "envoye_le", "brouillon", "verdict", "note",
                      "album_ids", "email_contact", "differee", "agent_etat", "agent_json",
                      "agent_message_id", "texte_complet"}


def maintenant() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Stockage:
    def __init__(self, chemin: Path):
        # timeout : le fil des agents écrit aussi dans la base, on attend au lieu d'échouer.
        self.db = sqlite3.connect(str(chemin), timeout=30)
        self.db.row_factory = sqlite3.Row  # accès aux colonnes par leur nom
        self.db.executescript(SCHEMA)
        # Mise à jour d'une base créée par une version précédente de l'outil.
        colonnes = {c[1] for c in self.db.execute("PRAGMA table_info(annonces)")}
        for nom, type_ in NOUVELLES_COLONNES.items():
            if nom not in colonnes:
                self.db.execute(f"ALTER TABLE annonces ADD COLUMN {nom} {type_}")
        self.db.commit()

    # ---- emails ---------------------------------------------------------
    def email_deja_traite(self, cle: str) -> bool:
        return self.db.execute("SELECT 1 FROM emails_traites WHERE cle=?", (cle,)).fetchone() is not None

    def marquer_email_traite(self, cle: str) -> None:
        self.db.execute("INSERT OR IGNORE INTO emails_traites VALUES (?, ?)", (cle, maintenant()))
        self.db.commit()

    # ---- annonces -------------------------------------------------------
    def deja_vue(self, a: Annonce) -> bool:
        """Doublon si même empreinte (ville+prix+surface) OU même lien."""
        ligne = self.db.execute(
            "SELECT 1 FROM annonces WHERE empreinte=? OR lien_normalise=?",
            (a.empreinte(), normaliser_lien(a.lien)),
        ).fetchone()
        return ligne is not None

    def ajouter(self, a: Annonce, retenue: bool, raison: str) -> None:
        self.db.execute(
            """INSERT OR IGNORE INTO annonces
               (empreinte, lien, lien_normalise, source, prix_cc, charges_incertaines, surface,
                ville, quartier, colocation, photo, photos, texte, email_contact, telephone, retenue,
                raison, vue_le)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (a.empreinte(), a.lien, normaliser_lien(a.lien), a.source, a.prix_cc,
             int(a.charges_incertaines), a.surface, a.ville, a.quartier, int(a.colocation), a.photo,
             json.dumps(a.photos), a.texte, a.email_contact,
             a.telephone, int(retenue), raison, maintenant()),
        )
        self.db.commit()

    def a_notifier(self) -> List[sqlite3.Row]:
        """Annonces retenues dont la notification n'a pas encore pu être envoyée."""
        return self.db.execute(
            "SELECT * FROM annonces WHERE retenue=1 AND notifiee_le IS NULL ORDER BY id"
        ).fetchall()

    # ---- chiffres pour /stats, le bilan du matin et le résumé du soir ----
    def compter(self, depuis: str = "") -> dict:
        """Compteurs depuis une date ISO (vide = depuis toujours)."""
        q = lambda sql: self.db.execute(sql, (depuis,)).fetchone()[0]  # noqa: E731
        return {
            "vues": q("SELECT COUNT(*) FROM annonces WHERE vue_le >= ?"),
            "gardees": q("SELECT COUNT(*) FROM annonces WHERE retenue=1 AND essai=0 AND vue_le >= ?"),
            "vertes": q("SELECT COUNT(*) FROM annonces WHERE verdict LIKE '🟢%' AND essai=0 AND notifiee_le >= ?"),
            "jaunes": q("SELECT COUNT(*) FROM annonces WHERE verdict LIKE '🟡%' AND essai=0 AND notifiee_le >= ?"),
            "rouges": q("SELECT COUNT(*) FROM annonces WHERE verdict LIKE '🔴%' AND essai=0 AND notifiee_le >= ?"),
            "envoyes": q("SELECT COUNT(*) FROM annonces WHERE envoye_le IS NOT NULL AND essai=0 AND envoye_le >= ?"),
            "postules": q("SELECT COUNT(*) FROM annonces WHERE statut='postulé' AND essai=0 AND notifiee_le >= ?"),
            "emails": self.db.execute("SELECT COUNT(*) FROM emails_traites WHERE traite_le >= ?",
                                      (depuis,)).fetchone()[0],
        }

    def differees(self, depuis: str) -> List[sqlite3.Row]:
        """🔴 mises de côté depuis une date, pas encore affichées."""
        return self.db.execute("SELECT * FROM annonces WHERE differee=1 AND notifiee_le >= ? AND statut IS NULL "
                               "ORDER BY note DESC, id", (depuis,)).fetchall()

    def sans_decision(self, avant: str) -> List[sqlite3.Row]:
        """Annonces affichées avant une date, sans aucune décision."""
        return self.db.execute(
            "SELECT * FROM annonces WHERE retenue=1 AND differee=0 AND essai=0 AND notifiee_le IS NOT NULL "
            "AND notifiee_le < ? AND statut IS NULL AND envoye_le IS NULL ORDER BY id", (avant,)).fetchall()

    def rapports_prets(self) -> List[sqlite3.Row]:
        return self.db.execute("SELECT * FROM annonces WHERE agent_etat='pret' ORDER BY id").fetchall()

    def nb_rapports(self, depuis: str) -> int:
        return self.db.execute("SELECT COUNT(*) FROM annonces WHERE agent_etat='fini' AND notifiee_le >= ?",
                               (depuis,)).fetchone()[0]

    def en_cours(self) -> List[sqlite3.Row]:
        """Candidatures en cours : dossier envoyé, postulé ou brouillon (hors essais)."""
        return self.db.execute(
            "SELECT * FROM annonces WHERE essai=0 AND statut IN ('dossier envoyé', 'postulé', 'brouillon créé') "
            "ORDER BY COALESCE(envoye_le, notifiee_le) DESC").fetchall()

    def passer_en_essai(self, nombre: int) -> List[int]:
        """Reprend les `nombre` dernières annonces écartées pour les notifier en mode essai."""
        ids = [l[0] for l in self.db.execute(
            "SELECT id FROM annonces WHERE retenue=0 AND prix_cc IS NOT NULL ORDER BY id DESC LIMIT ?",
            (nombre,))]
        self.db.executemany(
            "UPDATE annonces SET retenue=1, essai=1, notifiee_le=NULL, raison='essai' WHERE id=?",
            [(i,) for i in ids])
        self.db.commit()
        return sorted(ids)

    def prix_m2_zone(self, ville: str, colocation: bool, sauf_id: int) -> List[float]:
        """€/m² de toutes les annonces vues dans la zone (gardées ou non), même type."""
        lignes = self.db.execute(
            "SELECT prix_cc * 1.0 / surface FROM annonces WHERE ville=? AND colocation=? AND id<>? "
            "AND prix_cc IS NOT NULL AND surface > 0 AND charges_incertaines=0",
            (ville, int(colocation), sauf_id),
        ).fetchall()
        return [l[0] for l in lignes]

    def annonce(self, id_: int) -> Optional[sqlite3.Row]:
        return self.db.execute("SELECT * FROM annonces WHERE id=?", (id_,)).fetchone()

    def maj(self, id_: int, **champs) -> None:
        """Modifie des colonnes d'une annonce, ex. maj(3, statut="pas intéressé")."""
        inconnus = set(champs) - CHAMPS_MODIFIABLES
        if inconnus:
            raise ValueError(f"Champs non modifiables : {inconnus}")
        affectations = ", ".join(f"{c}=?" for c in champs)
        self.db.execute(f"UPDATE annonces SET {affectations} WHERE id=?", (*champs.values(), id_))
        self.db.commit()

    # ---- petites valeurs persistantes (ex. position de lecture Telegram) ----
    def lire_etat(self, cle: str, defaut: str = "") -> str:
        ligne = self.db.execute("SELECT valeur FROM etat WHERE cle=?", (cle,)).fetchone()
        return ligne[0] if ligne else defaut

    def ecrire_etat(self, cle: str, valeur) -> None:
        self.db.execute("INSERT OR REPLACE INTO etat VALUES (?, ?)", (cle, str(valeur)))
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
            "AND relance_envoyee=0 AND essai=0 AND differee=0 ORDER BY id",
            (limite,),
        ).fetchall()

    def marquer_relancee(self, id_: int) -> None:
        self.db.execute("UPDATE annonces SET relance_envoyee=1 WHERE id=?", (id_,))
        self.db.commit()
