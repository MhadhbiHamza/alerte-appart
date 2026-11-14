"""Zones de recherche : arrondissements de Paris et communes de proche banlieue.

Pour chaque annonce, on détermine sa « zone » (ex. « Paris 13e » ou
« Ivry-sur-Seine ») :
  1. d'abord par le code postal (le plus fiable) : 75013, 94200… ;
  2. sinon par le nom de la ville ou « Paris 13e » ; s'il y en a plusieurs,
     on garde celui qui apparaît en premier (en général la ligne de
     localisation, avant la description).

Les quartiers ne sont pas filtrés (les emails d'alerte les indiquent rarement) :
s'ils sont cités, ils sont simplement affichés dans la notification.

Pour ajouter une commune : ajoutez une ligne dans COMMUNES puis son nom dans
ZONES du fichier .env.
"""
import re
import unicodedata
from typing import Dict, List, Optional, Tuple

# libellé -> (codes postaux, nom tel qu'écrit dans les annonces (sans accents, minuscules))
# Les (?!...) évitent les homonymes : Clichy-sous-Bois, Asnières-sur-Oise, Vitry-le-François…
COMMUNES: Dict[str, Tuple[List[str], str]] = {
    "Ivry-sur-Seine": (["94200"], r"ivry(?![\s-]+(?:la|le|en)\b)"),
    "Maisons-Alfort": (["94700"], r"maisons[\s-]+alfort"),
    "Clamart": (["92140"], r"clamart"),
    "Asnières-sur-Seine": (["92600"], r"asnieres(?![\s-]+(?:sur[\s-]+oise|les)\b)"),
    "Bois-Colombes": (["92270"], r"bois[\s-]+colombes"),
    "Vitry-sur-Seine": (["94400"], r"vitry(?![\s-]+(?:le|aux|en|sur[\s-]+orne)\b)"),
    "Saint-Denis": (["93200", "93210"], r"(?:saint|st)[\s-]+denis"),
    "Montreuil": (["93100"], r"montreuil(?![\s-]+(?:sur|aux|le|la|en|juigne)\b)"),
    "Meudon": (["92190", "92360"], r"meudon"),
    "Vincennes": (["94300"], r"vincennes"),
    "Charenton-le-Pont": (["94220"], r"charenton(?:[\s-]+le[\s-]+pont)?"),
    "Montrouge": (["92120"], r"montrouge"),
    "Clichy": (["92110"], r"clichy(?![\s-]+(?:sous|la)\b)"),
    "Boulogne-Billancourt": (["92100"], r"boulogne[\s-]+billancourt"),
    "Issy-les-Moulineaux": (["92130"], r"issy(?:[\s-]+les[\s-]+moulineaux)?"),
}

# Quartiers que vous avez cités (affichés s'ils apparaissent dans l'annonce).
# Les mots trop courants (« centre-ville », « gare », « mairie »…) sont volontairement absents.
QUARTIERS: Dict[str, List[str]] = {
    "Paris 15e": ["Convention", "Vaugirard", "Saint-Lambert"],
    "Paris 14e": ["Alésia", "Pernety", "Mouton-Duvernet"],
    "Paris 12e": ["Daumesnil", "Picpus", "Bel-Air"],
    "Paris 13e": ["Butte-aux-Cailles", "Maison-Blanche"],
    "Paris 11e": ["Charonne", "Voltaire", "Saint-Ambroise"],
    "Ivry-sur-Seine": ["Mairie d'Ivry"],
    "Maisons-Alfort": ["École Vétérinaire", "Charentonneau"],
    "Asnières-sur-Seine": ["Bécon"],
    "Bois-Colombes": ["Les Vallées"],
    "Vitry-sur-Seine": ["Gare de Vitry"],
    "Saint-Denis": ["Basilique", "République"],
    "Montreuil": ["Bas-Montreuil", "Croix-de-Chavaux"],
    "Meudon": ["Bellevue", "Val-Fleury"],
    "Vincennes": ["Château de Vincennes", "Bérault"],
    "Charenton-le-Pont": ["Liberté", "Charenton-Écoles"],
    "Montrouge": ["Barbara", "Jean-Jaurès"],
    "Boulogne-Billancourt": ["Marcel Sembat"],
    "Issy-les-Moulineaux": ["Corentin Celton", "Mairie d'Issy"],
}

CODE_POSTAL_RE = re.compile(r"(?<!\d)(75\d{3}|9[234]\d{3})(?!\d)")
PARIS_RES = [
    re.compile(r"\bparis\s*\(?\s*(\d{1,2})\s*(?:er|ere|e|eme|°)(?![a-z])"),  # Paris 13e, Paris 13ème
    re.compile(r"\b(\d{1,2})\s*(?:er|ere|e|eme)\s+arr"),                     # 13e arrondissement
    re.compile(r"\bparis\s*\(?\s*(\d{1,2})\s*\)?(?=\s*(?:$|[,;\-–—|·•/)]))", re.M),  # Paris 13 -
]
# « rue de Charenton », « Porte d'Ivry », « avenue de Clichy » sont dans Paris : on les ignore.
VOIE_AVANT_RE = re.compile(
    r"(?:porte|rue|avenue|av\.?|bd|boulevard|place|quai|route|chaussee|pont|villa|passage)"
    r"\s+(?:de\s+la\s+|de\s+|d'|du\s+|des\s+)?$"
)
_CP_VERS_COMMUNE = {cp: nom for nom, (cps, _) in COMMUNES.items() for cp in cps}


def simplifier(texte: str) -> str:
    """Minuscules, sans accents, apostrophes droites : « Asnières » -> « asnieres »."""
    texte = unicodedata.normalize("NFKD", texte)
    texte = "".join(c for c in texte if not unicodedata.combining(c))
    return texte.replace("’", "'").lower()


def libelle_paris(n: int) -> str:
    return "Paris 1er" if n == 1 else f"Paris {n}e"


def normaliser_zone(nom: str) -> Optional[str]:
    """Transforme ce que vous écrivez dans .env en libellé officiel.

    « paris 13 », « Paris 13ème », « 75013 » -> « Paris 13e »
    « asnieres-sur-seine », « Asnières » -> « Asnières-sur-Seine »
    """
    s = simplifier(nom).strip()
    m = re.fullmatch(r"(?:paris\s*)?(\d{1,2})\s*(?:e|er|eme)?|750(\d{2})", s)
    if m:
        n = int(m.group(1) or m.group(2))
        return libelle_paris(n) if 1 <= n <= 20 else None
    for libelle, (cps, regex) in COMMUNES.items():
        if s == simplifier(libelle) or s in cps or re.fullmatch(regex + r"(?:[\s-].*)?", s):
            return libelle
    return None


def detecter_zone(texte: str) -> Optional[str]:
    """Renvoie la zone d'une annonce, « autre commune (92300) », ou None si introuvable."""
    s = simplifier(texte)

    # 1. Code postal : c'est lui qui décide s'il est présent.
    for m in CODE_POSTAL_RE.finditer(s):
        cp = m.group(1)
        if cp.startswith("75"):
            n = 16 if cp == "75116" else int(cp[2:])
            if 1 <= n <= 20:
                return libelle_paris(n)
            continue
        return _CP_VERS_COMMUNE.get(cp, f"autre commune ({cp})")

    # 2. Noms : on garde la première mention dans le texte.
    candidats = []
    for regex in PARIS_RES:
        for m in regex.finditer(s):
            if 1 <= int(m.group(1)) <= 20:
                candidats.append((m.start(), libelle_paris(int(m.group(1)))))
                break
    for libelle, (_, regex) in COMMUNES.items():
        for m in re.finditer(r"(?<![a-z])(?:" + regex + r")(?![a-z])", s):
            if not VOIE_AVANT_RE.search(s[max(0, m.start() - 20):m.start()]):
                candidats.append((m.start(), libelle))
                break
    return min(candidats)[1] if candidats else None


def detecter_quartier(texte: str, zone: Optional[str]) -> Optional[str]:
    """Premier quartier de votre liste cité dans l'annonce (pour la zone trouvée)."""
    s = simplifier(texte).replace("-", " ")
    for quartier in QUARTIERS.get(zone or "", []):
        if re.search(r"(?<![a-z])" + re.escape(simplifier(quartier).replace("-", " ")) + r"(?![a-z])", s):
            return quartier
    return None
