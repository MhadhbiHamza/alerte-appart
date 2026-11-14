"""Remplissage du modèle de candidature (templates.md).

Le fichier est relu à chaque annonce : vous pouvez le modifier pendant que
l'outil tourne. Les variables s'écrivent entre accolades : {prix}, {lien}…
"""
import re
from pathlib import Path
from typing import Dict, Tuple

OBJET_PAR_DEFAUT = "Candidature pour l'appartement {surface} m² – {ville}"


def charger(chemin: Path, colocation: bool = False) -> Tuple[str, str]:
    """Lit templates.md et renvoie (objet, message).

    Format attendu : une section « ## Objet » puis une section « ## Message ».
    Pour une colocation, les sections « ## Objet colocation » et
    « ## Message colocation » sont utilisées si elles existent.
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
    def section(nom: str) -> str:
        if colocation and sections.get(nom + " colocation"):
            nom += " colocation"
        return "\n".join(sections.get(nom, [])).strip()

    objet = section("objet") or OBJET_PAR_DEFAUT
    message = section("message")
    if not message:
        raise ValueError(f"Section « ## Message » introuvable ou vide dans {chemin}")
    return objet, message


def remplir(texte: str, valeurs: Dict[str, str]) -> str:
    """Remplace {variable} par sa valeur. Une variable inconnue est laissée telle quelle."""
    return re.sub(r"\{(\w+)\}", lambda m: str(valeurs.get(m.group(1), m.group(0))), texte)


def valeurs_annonce(annonce) -> Dict[str, str]:
    """Prépare les variables utilisables dans templates.md à partir d'une annonce."""
    surface = annonce["surface"]
    ville = annonce["ville"] or "?"
    return {
        "prix": str(annonce["prix_cc"] or "?"),
        "surface": (f"{surface:g}" if surface else "?"),
        "ville": ville,
        "arrondissement": ville,  # ancien nom de variable, gardé pour compatibilité
        "quartier": annonce["quartier"] or "",
        "lien": annonce["lien"],
        "source": annonce["source"] or "",
        "contact": annonce["email_contact"] or annonce["telephone"] or "",
    }
