"""Analyse « en un coup d'œil » d'une annonce : est-ce que ça vaut le coup ?

Tout est calculé à partir du texte de l'email d'alerte (aucun site n'est
consulté) et de repères fixes :
  - prix au m², comparé aux annonces déjà reçues dans la même zone ;
  - frais d'agence maximum autorisés (loi ALUR) et budget d'entrée ;
  - lignes de métro / RER / tram de la zone (repère indicatif, pas l'adresse exacte) ;
  - détails cités dans l'annonce : pièces, meublé, charges, honoraires, DPE, étage…
Le verdict (🟢 / 🟡 / 🔴) additionne des points « pour » et « contre » listés
dans la notification, pour que vous voyiez pourquoi.
"""
import re
import statistics
from dataclasses import dataclass, field
from typing import List, Optional

import zones

# Repère indicatif : principales lignes desservant chaque zone.
TRANSPORTS = {
    "Paris 11e": "M1 · M2 · M3 · M5 · M8 · M9 · M11",
    "Paris 12e": "M1 · M6 · M8 · M14 · RER A · RER D · T3a",
    "Paris 13e": "M5 · M6 · M7 · M14 · RER C · T3a",
    "Paris 14e": "M4 · M6 · M13 · RER B · T3a",
    "Paris 15e": "M6 · M8 · M10 · M12 · M13 · RER C · T2 · T3a",
    "Ivry-sur-Seine": "M7 · RER C",
    "Maisons-Alfort": "M8 · RER D",
    "Clamart": "Transilien N · T6",
    "Asnières-sur-Seine": "M13 · Transilien J et L",
    "Bois-Colombes": "Transilien J et L",
    "Vitry-sur-Seine": "RER C · T9",
    "Saint-Denis": "M13 · M14 · RER B · RER D · T1 · T5 · T8",
    "Montreuil": "M9 · M11",
    "Meudon": "RER C · Transilien N · T2",
    "Vincennes": "M1 · RER A",
    "Charenton-le-Pont": "M8",
    "Montrouge": "M4",
    "Clichy": "M13 · Transilien L",
    "Boulogne-Billancourt": "M9 · M10 · T2",
    "Issy-les-Moulineaux": "M12 · RER C · T2",
}
# Communes où les loyers sont plafonnés (encadrement des loyers).
LOYERS_ENCADRES = {"Saint-Denis", "Montreuil"}  # + tout Paris

# Loi ALUR, zone très tendue : 12 €/m² (visite, dossier, bail) + 3 €/m² (état des lieux).
FRAIS_AGENCE_MAX_M2 = 15
SURFACE_MIN_DECENCE = 9  # m² : surface minimale d'un logement décent

_R = re.IGNORECASE


@dataclass
class Details:
    pieces: Optional[int] = None
    meuble: Optional[bool] = None
    charges: Optional[int] = None
    frais_agence: Optional[int] = None
    dpe: Optional[str] = None
    etage: Optional[int] = None
    ascenseur: Optional[bool] = None
    atouts: List[str] = field(default_factory=list)
    disponible: Optional[str] = None
    transports_cites: List[str] = field(default_factory=list)


def extraire_details(texte: str, source: str = "") -> Details:
    """Cherche dans le texte de l'annonce tout ce qui aide à juger l'offre."""
    s = zones.simplifier(texte)
    d = Details()

    if re.search(r"\bstudio\b", s):
        d.pieces = 1
    m = re.search(r"\b(\d)\s*pieces?\b|\b[tf]\s?(\d)\b", s)
    if m:
        d.pieces = int(m.group(1) or m.group(2))

    if re.search(r"\bnon[\s-]+meuble|\bvide\b", s):
        d.meuble = False
    elif re.search(r"\bmeuble", s):
        d.meuble = True

    m = (re.search(r"dont\s+(\d{1,4})\s*(?:€|eur\w*)?\s*(?:de\s+)?(?:charges|provisions?)", s)
         or re.search(r"\+\s*(\d{1,4})\s*(?:€|eur\w*)?\s*(?:de\s+)?(?:charges|provisions?)", s)
         or re.search(r"charges?\s*(?:mensuelles\s*)?(?::|de|=)?\s*(\d{1,4})\s*(?:€|eur)", s))
    if m:
        d.charges = int(m.group(1))

    if source == "PAP" or re.search(r"sans\s+frais\s+d'?agence|pas\s+de\s+frais\s+d'?agence|"
                                    r"\bparticulier\b|frais\s+d'?agence\s*:?\s*0\s*(?:€|eur)", s):
        d.frais_agence = 0
    else:
        m = re.search(r"(?:honoraires|frais\s+d'?agence)[^0-9€]{0,25}?(\d[\d .]{0,6}\d|\d)\s*(?:€|eur)", s)
        if m:
            d.frais_agence = int(re.sub(r"\D", "", m.group(1)))

    m = re.search(r"\b(?:dpe|classe\s+energie|diagnostic\s+energetique)\s*:?\s*([a-g])\b", s)
    if m:
        d.dpe = m.group(1).upper()

    if re.search(r"rez[\s-]+de[\s-]+chaussee|\brdc\b", s):
        d.etage = 0
    else:
        m = re.search(r"\b(\d{1,2})\s*(?:e|er|eme)\s+etage", s)
        if m:
            d.etage = int(m.group(1))
    if re.search(r"sans\s+ascenseur", s):
        d.ascenseur = False
    elif re.search(r"\bascenseur\b", s):
        d.ascenseur = True

    for mot, nom in (("balcon", "balcon"), ("terrasse", "terrasse"), ("jardin", "jardin"),
                     ("parking", "parking"), ("cave", "cave"), ("lumineux", "lumineux"),
                     ("calme", "calme"), ("refait a neuf|renove", "rénové"), (r"lave[\s-]linge", "lave-linge")):
        if re.search(rf"\b(?:{mot})", s) and nom not in d.atouts:
            d.atouts.append(nom)

    m = re.search(r"disponible\s+(?:le\s+|a\s+partir\s+du\s+|des\s+(?:le\s+)?)?"
                  r"(\d{1,2}[/.]\d{1,2}(?:[/.]\d{2,4})?|immediatement|maintenant|de\s+suite|tout\s+de\s+suite)", s)
    if m:
        d.disponible = m.group(1)

    # Stations / lignes citées : « métro Mairie d'Ivry », « RER C », « ligne 14 », « tram T3 »
    nom = r"[A-ZÀ-Ý][\w'’-]*"
    liaison = r"(?:(?:de|du|des|la|le|les)\s|d['’]|l['’])?"
    for m in re.finditer(rf"\b(?:m[ée]tro|station)\s+({nom}(?:[ -]{liaison}{nom}){{0,3}})", texte):
        d.transports_cites.append(m.group(1))
    for m in re.finditer(r"\b(RER\s?[A-E]|ligne\s+\d{1,2}b?|tram(?:way)?\s+T?\d{1,2}[ab]?)\b", texte, _R):
        d.transports_cites.append(re.sub(r"\s+", " ", m.group(1)))
    d.transports_cites = list(dict.fromkeys(d.transports_cites))[:3]
    return d


def frais_agence_max(surface: Optional[float]) -> Optional[int]:
    return round(surface * FRAIS_AGENCE_MAX_M2) if surface else None


# Note « emplacement » de base, d'après vos groupes de villes (les priorités valent 5/5).
EMPLACEMENT = {
    **{zones.libelle_paris(n): 4 for n in range(1, 21)},  # tout Paris
    **{v: 3 for v in ("Ivry-sur-Seine", "Maisons-Alfort", "Clamart", "Asnières-sur-Seine", "Bois-Colombes",
                      "Vitry-sur-Seine", "Saint-Denis", "Montreuil", "Meudon")},
    **{v: 2 for v in ("Vincennes", "Charenton-le-Pont", "Montrouge", "Clichy", "Boulogne-Billancourt",
                      "Issy-les-Moulineaux")},
}
# Note « transports » : métros + RER = 5 ; métro + train = 4 ; une seule ligne de métro = 3 ;
# seulement RER / train / tram = 2.
NOTE_TRANSPORTS = {
    **{zones.libelle_paris(n): 5 for n in range(1, 21)},  # métro dense partout dans Paris
    **{v: 4 for v in ("Saint-Denis", "Ivry-sur-Seine", "Maisons-Alfort", "Asnières-sur-Seine", "Montreuil",
                      "Vincennes", "Boulogne-Billancourt", "Issy-les-Moulineaux")},
    **{v: 3 for v in ("Charenton-le-Pont", "Montrouge", "Clichy")},
    **{v: 2 for v in ("Vitry-sur-Seine", "Meudon", "Clamart", "Bois-Colombes")},
}


def _borner(note: float) -> int:
    return int(max(1, min(5, round(note))))


def etoiles(note: int) -> str:
    return "★" * note + "☆" * (5 - note)


@dataclass
class Analyse:
    verdict: str
    lignes: List[str]
    pour: List[str]
    contre: List[str]
    prix_m2: Optional[float] = None
    notes: dict = field(default_factory=dict)   # {"Emplacement": 4, "Prix": 3, …}
    note_globale: float = 0.0
    repere_prix: str = ""                        # « vs votre budget » si pas encore de comparaison


def analyser(annonce, prix_m2_zone: List[float], prioritaire: bool = False,
             revenu_net: Optional[int] = None, prix_max: Optional[int] = None,
             trajet_ok: bool = False) -> Analyse:
    """Construit le résumé et les notes sur 5.

    `annonce` : ligne de la base ; `prix_m2_zone` : €/m² des autres annonces de la même zone
    et du même type (location / colocation) ; `prix_max` : votre budget (repère de prix
    tant qu'il n'y a pas assez d'annonces pour comparer).
    """
    d = extraire_details(annonce["texte"] or "", annonce["source"] or "")
    prix, surface, ville = annonce["prix_cc"], annonce["surface"], annonce["ville"]
    coloc = bool(annonce["colocation"])
    lignes, pour, contre = [], [], []
    alerte_rouge = False  # problème grave : verdict 🔴 quelle que soit la note

    # --- Prix -----------------------------------------------------------------
    prix_m2 = round(prix / surface, 1) if prix and surface else None
    note_prix, repere_prix = None, ""
    if prix_m2:
        ligne = f"💶 {prix_m2:g} €/m²".replace(".", ",")
        if len(prix_m2_zone) >= 5:
            mediane = statistics.median(prix_m2_zone)
            ecart = round((prix_m2 / mediane - 1) * 100)
            ligne += (f" · {abs(ecart)} % {'moins cher' if ecart < 0 else 'plus cher'} que la médiane "
                      f"({mediane:.0f} €/m², {len(prix_m2_zone)} annonces vues ici)")
            note_prix = 5 if ecart <= -15 else 4 if ecart <= -5 else 3 if ecart < 5 else 2 if ecart < 15 else 1
            if ecart <= -5:
                pour.append(f"prix {abs(ecart)} % sous la médiane de la zone")
            elif ecart >= 5:
                contre.append(f"prix {ecart} % au-dessus de la médiane de la zone")
                if ecart >= 10 and (ville in LOYERS_ENCADRES or (ville or "").startswith("Paris")):
                    contre.append("loyers encadrés ici : vérifiez le plafond légal")
        else:
            ligne += f" · comparaison dès 5 annonces vues ici ({len(prix_m2_zone)} pour l'instant)"
        lignes.append(ligne)
    if note_prix is None and prix and prix_max:
        # Pas encore assez d'annonces : on situe le prix par rapport à votre budget.
        r = prix / prix_max
        note_prix = 5 if r <= 0.7 else 4 if r <= 0.8 else 3 if r <= 0.9 else 2
        repere_prix = "vs votre budget"

    # --- Charges, frais d'agence, budget ---------------------------------------
    morceaux = []
    if d.charges is not None:
        morceaux.append(f"charges {d.charges} € incluses")
    elif annonce["charges_incertaines"]:
        morceaux.append("prix HC, charges inconnues")
        contre.append("charges non précisées (prix hors charges)")
        if note_prix:
            note_prix = _borner(note_prix - 1)
    else:
        morceaux.append("charges non détaillées")
    maxi = frais_agence_max(surface)
    if d.frais_agence == 0:
        morceaux.append("0 € de frais d'agence")
        pour.append("sans frais d'agence")
        note_frais = 5
    elif d.frais_agence:
        morceaux.append(f"frais d'agence {d.frais_agence} €")
        if maxi and d.frais_agence > maxi:
            contre.append(f"frais d'agence au-dessus du plafond légal (~{maxi} €)")
            alerte_rouge, note_frais = True, 1
        else:
            note_frais = 4 if maxi and d.frais_agence <= maxi / 2 else 3 if maxi and d.frais_agence <= maxi * 0.8 else 2
    else:
        if maxi:
            morceaux.append(f"frais d'agence ≤ {maxi} € (plafond légal)")
        note_frais = 3  # inconnus
    lignes.append("🧾 " + " · ".join(morceaux))

    if prix:
        loyer_hc = prix - (d.charges or 0)
        depot = loyer_hc * (2 if d.meuble else 1)
        frais = d.frais_agence if d.frais_agence is not None else (maxi or 0)
        lignes.append(f"💰 Entrée ≈ {prix + depot + frais:,} € (1er loyer + dépôt"
                      f"{' 2 mois (meublé)' if d.meuble else ''} + frais)".replace(",", " "))
    if prix and revenu_net:
        ratio = round(prix / revenu_net * 100)
        lignes.append(f"👛 {ratio} % de vos revenus nets")
        if ratio > 33:
            contre.append(f"loyer = {ratio} % de vos revenus (souvent ≤ 33 % demandé)")

    # --- Logement -------------------------------------------------------------
    infos = []
    if d.pieces:
        infos.append("studio" if d.pieces == 1 else f"{d.pieces} pièces")
    if d.meuble is not None:
        infos.append("meublé" if d.meuble else "vide")
    if d.etage is not None:
        infos.append("RDC" if d.etage == 0 else f"{d.etage}e étage")
    if d.ascenseur is not None:
        infos.append("ascenseur" if d.ascenseur else "sans ascenseur")
    if d.dpe:
        infos.append(f"DPE {d.dpe}")
    if d.disponible:
        infos.append(f"dispo {d.disponible}")
    if infos:
        lignes.append("🏢 " + " · ".join(infos))
    if d.atouts:
        lignes.append("✨ " + ", ".join(d.atouts))

    note_logement = 3.0
    if {"balcon", "terrasse", "jardin"} & set(d.atouts):
        note_logement += 1; pour.append("extérieur")
    if {"rénové", "lumineux", "calme"} & set(d.atouts):
        note_logement += 0.5
    if surface:
        grand, petit = (12, 9) if coloc else (30, 15)
        if surface >= grand:
            note_logement += 1
        elif surface < petit:
            note_logement -= 1
    if d.dpe == "G":
        contre.append("DPE G : location interdite depuis 2025")
        alerte_rouge = True; note_logement -= 2
    elif d.dpe == "F":
        contre.append("DPE F : passoire thermique (interdit à la location en 2028)")
        note_logement -= 1
    elif d.dpe in ("A", "B", "C"):
        pour.append(f"bon DPE ({d.dpe})"); note_logement += 1
    if surface and surface < SURFACE_MIN_DECENCE:
        contre.append(f"{surface:g} m² : sous le minimum légal de {SURFACE_MIN_DECENCE} m²")
        alerte_rouge = True; note_logement -= 2
    if d.etage and d.etage >= 4 and d.ascenseur is False:
        contre.append(f"{d.etage}e étage sans ascenseur"); note_logement -= 1

    # --- Emplacement et transports ----------------------------------------------
    defaut = "métro dense (Paris intramuros)" if (ville or "").startswith("Paris") else "lignes : voir le plan"
    ligne = f"🚇 {TRANSPORTS.get(ville, defaut)}"
    if d.transports_cites:
        ligne += " — cité : " + ", ".join(d.transports_cites)
    lignes.append(ligne)
    # Hors de votre liste mais envoyée par une alerte « temps de trajet » : proche, donc 3.
    note_emplacement = 5 if prioritaire else EMPLACEMENT.get(ville, 3 if trajet_ok else 2)
    if annonce["quartier"] and not prioritaire:
        note_emplacement += 1  # un quartier de votre liste est cité
    if prioritaire:
        pour.append("zone prioritaire")
    note_transports = NOTE_TRANSPORTS.get(ville, 2) + (1 if d.transports_cites else 0)

    # --- Notes et verdict ---------------------------------------------------------
    notes = {"Emplacement": _borner(note_emplacement), "Prix": note_prix and _borner(note_prix),
             "Logement": _borner(note_logement), "Frais": _borner(note_frais),
             "Transports": _borner(note_transports)}
    connues = [n for n in notes.values() if n]
    note_globale = round(sum(connues) / len(connues), 1) if connues else 0.0
    if alerte_rouge or note_globale < 3:
        verdict = "🔴 Bof"
    elif note_globale >= 4:
        verdict = "🟢 À saisir"
    else:
        verdict = "🟡 Correct"
    return Analyse(verdict, lignes, pour, contre, prix_m2, notes, note_globale, repere_prix)
