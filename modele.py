"""Remplissage du modèle de candidature (templates.md).

Le fichier est relu à chaque annonce : vous pouvez le modifier pendant que
l'outil tourne. Les variables s'écrivent entre accolades : {prix}, {lien}…
"""
import re
from pathlib import Path
from typing import Dict, Tuple

OBJET_PAR_DEFAUT = "Candidature pour l'appartement {surface} m² – Paris {arrondissement}"


def charger(chemin: Path) -> Tuple[str, str]:
    """Lit templates.md et renvoie (objet, message).

    Format attendu : une section « ## Objet » puis une section « ## Message ».
    Les commentaires <!-- ... --> sont ignorés.
    """
    texte = chemin.read_text(encoding="utf-8")
    texte = re.sub(r"<!--.*?-->", "", texte, flags=re.S)
    sections, courante = {}, None
    for ligne in texte.splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", ligne)
        if m:
            courante = m.group(1).strip().lower()
            sections[courante] = []
        elif courante:
            sections[courante].append(ligne)
    objet = "\n".join(sections.get("objet", [])).strip() or OBJET_PAR_DEFAUT
    message = "\n".join(sections.get("message", [])).strip()
    if not message:
        raise ValueError(f"Section « ## Message » introuvable ou vide dans {chemin}")
    return objet, message


def remplir(texte: str, valeurs: Dict[str, str]) -> str:
    """Remplace {variable} par sa valeur. Une variable inconnue est laissée telle quelle."""
    return re.sub(r"\{(\w+)\}", lambda m: str(valeurs.get(m.group(1), m.group(0))), texte)


def valeurs_annonce(annonce) -> Dict[str, str]:
    """Prépare les variables utilisables dans templates.md à partir d'une annonce."""
    arr = annonce["arrondissement"]
    surface = annonce["surface"]
    return {
        "prix": str(annonce["prix_cc"] or "?"),
        "surface": (f"{surface:g}" if surface else "?"),
        "arrondissement": ("1er" if arr == 1 else f"{arr}e") if arr else "?",
        "lien": annonce["lien"],
        "source": annonce["source"] or "",
        "contact": annonce["email_contact"] or annonce["telephone"] or "",
    }
