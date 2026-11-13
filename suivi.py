"""Fichier CSV de suivi des candidatures (à ouvrir avec Excel / LibreOffice).

Séparateur « ; » et encodage UTF-8 avec BOM : Excel en français l'ouvre
directement avec les bonnes colonnes et les accents.

Vous pouvez modifier vous-même la colonne « statut » (ex. : contacté,
visite prévue, refusé…). Si le statut vaut refusé / abandonné / loué / ignoré
/ dossier accepté, l'outil ne vous enverra pas de rappel de relance.

⚠️ Sous Windows, Excel verrouille le fichier tant qu'il est ouvert : l'outil
réessaiera simplement au cycle suivant.
"""
import csv
import unicodedata
from pathlib import Path
from typing import Dict, List

COLONNES = ["id", "date", "source", "annonce", "lien", "contact", "brouillon_gmail", "statut", "relance_le"]
STATUTS_TERMINES = {"refuse", "abandonne", "loue", "ignore", "dossier accepte", "pas interesse"}


def _sans_accents(s: str) -> str:
    s = unicodedata.normalize("NFD", s or "")
    return "".join(c for c in s if unicodedata.category(c) != "Mn").strip().lower()


def _lire(chemin: Path) -> List[Dict[str, str]]:
    if not chemin.exists():
        return []
    with chemin.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f, delimiter=";"))


def _ecrire(chemin: Path, lignes: List[Dict[str, str]]) -> None:
    with chemin.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLONNES, delimiter=";", extrasaction="ignore")
        w.writeheader()
        w.writerows(lignes)


def ajouter(chemin: Path, ligne: Dict[str, str]) -> None:
    """Ajoute une ligne (sans doublon : on vérifie l'id)."""
    lignes = _lire(chemin)
    if any(l.get("id") == str(ligne["id"]) for l in lignes):
        return
    lignes.append({k: str(v) for k, v in ligne.items()})
    _ecrire(chemin, lignes)


def statut(chemin: Path, id_: int) -> str:
    for l in _lire(chemin):
        if l.get("id") == str(id_):
            return l.get("statut", "")
    return ""


def est_termine(statut_: str) -> bool:
    return _sans_accents(statut_) in STATUTS_TERMINES


def noter_relance(chemin: Path, id_: int, date_: str) -> None:
    """Remplit la colonne relance_le et passe le statut « nouveau » à « à relancer »."""
    lignes = _lire(chemin)
    for l in lignes:
        if l.get("id") == str(id_):
            l["relance_le"] = date_
            if _sans_accents(l.get("statut", "")) in ("", "nouveau"):
                l["statut"] = "à relancer"
    _ecrire(chemin, lignes)
