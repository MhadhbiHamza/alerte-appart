"""Extraction des annonces contenues dans un email d'alerte.

Un email d'alerte (Jinka, SeLoger, PAP) contient souvent PLUSIEURS annonces.
Principe utilisé, valable quel que soit le site :
  1. on part de chaque lien de l'email ;
  2. on « remonte » dans le HTML tant que le bloc ne contient qu'une seule
     annonce (une seule surface « xx m² ») : on obtient la « carte » de l'annonce ;
  3. dans le texte de cette carte, on cherche prix, surface, ville
     (voir zones.py) et contact avec des expressions régulières.

Le format des emails peut changer : utilisez `python main.py --analyser`
pour vérifier ce qui est extrait de vos vrais emails.
"""
import re
import unicodedata
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import parseaddr
from typing import Iterator, List, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup

import zones

# --------------------------------------------------------------------------
# Expressions régulières
# --------------------------------------------------------------------------

# Prix : "850 €", "1 050 €", "1.050€", "850,00 €", "850 euros".
# Le (?<!\d) évite de prendre "75011 850" pour un seul nombre.
PRIX_RE = re.compile(
    r"(?<![\d,.])(\d{1,2}[ .]\d{3}|\d{3,5})(?:,\d{1,2})?\s*(?:€|eur\b|euros?\b)", re.I
)
# Surface : "32 m²", "32m2", "32,5 m²" (le ² devient 2 après normalisation).
SURFACE_RE = re.compile(r"(?<![\d,.])(\d{1,3}(?:[.,]\d{1,2})?)\s*m(?:²|2\b)", re.I)

# Charges : "800 € + 50 € de charges" ou "Charges : 50 €"
CHARGES_PLUS_RE = re.compile(
    r"\+\s*(\d{1,4})(?:,\d{1,2})?\s*(?:€|eur\w*)?\s*(?:de\s+)?(?:charges|provisions?)", re.I
)
CHARGES_RE = re.compile(
    r"(?:charges|provisions?\s+(?:pour|sur)\s+charges)\s*(?:mensuelles\s*)?(?::|de|=)?\s*"
    r"(\d{1,4})(?:,\d{1,2})?\s*(?:€|eur)",
    re.I,
)
HC_RE = re.compile(r"\bhc\b|hors[\s-]+charges", re.I)
CC_RE = re.compile(r"\bcc\b|charges?\s+compris(?:es?)?|tout\s+compris", re.I)

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[a-z]{2,}", re.I)
TELEPHONE_RE = re.compile(
    r"(?<![\d+])(?:(?:\+|00)33[\s.\-]?(?:\(0\)[\s.\-]?)?|0)[1-9](?:[\s.\-]?\d{2}){4}(?!\d)"
)
URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.I)

# Emails à ne jamais considérer comme « contact de l'agence ».
DOMAINES_IGNORES = ("jinka", "seloger", "pap.fr", "pap.com", "google", "bienici", "leboncoin", "logic-immo")
PREFIXES_IGNORES = ("noreply", "no-reply", "no_reply", "nepasrepondre", "ne-pas-repondre",
                    "ne_pas_repondre", "newsletter", "notification", "alerte", "alert")

# Liens qui ne sont pas des annonces (désinscription, réseaux sociaux…).
LIEN_IGNORE_RE = re.compile(
    r"unsubscribe|desinscri|désinscri|desabonn|désabonn|apps\.apple\.com|play\.google\.com|"
    r"facebook\.com|instagram\.com|twitter\.com|linkedin\.com|youtube\.com|tiktok\.com",
    re.I,
)
TEXTE_LIEN_IGNORE_RE = re.compile(
    r"d[ée]sinscri|d[ée]sabonn|modifier|g[ée]rer|param[èe]tr|pr[ée]f[ée]rences|mon compte|"
    r"aide|confidentialit|mentions l|voir (?:les|toutes|tous)|toutes les annonces",
    re.I,
)
# Un bloc qui parle de « budget max » est le rappel des critères de l'alerte, pas une annonce.
BLOC_IGNORE_RE = re.compile(r"\bbudget\b|\bmax(?:imum)?\b|\bmin(?:imum)?\b", re.I)


# --------------------------------------------------------------------------
# Modèle de données
# --------------------------------------------------------------------------

@dataclass
class Annonce:
    source: str
    lien: str
    texte: str  # texte brut du bloc (utile pour comprendre une erreur d'extraction)
    prix_cc: Optional[int] = None
    charges_incertaines: bool = False  # True si prix HC sans montant de charges
    surface: Optional[float] = None
    ville: Optional[str] = None      # « Paris 13e », « Ivry-sur-Seine »…
    quartier: Optional[str] = None   # quartier de votre liste, s'il est cité
    email_contact: Optional[str] = None
    telephone: Optional[str] = None

    def empreinte(self) -> str:
        """Identifiant servant à repérer les doublons.

        La même annonce reçue de Jinka ET de SeLoger a des liens différents,
        mais la même ville / prix / surface : on s'appuie dessus.
        """
        if self.prix_cc and self.surface and self.ville:
            return f"{self.ville}|{self.prix_cc}|{round(self.surface)}"
        return "lien|" + normaliser_lien(self.lien)


# --------------------------------------------------------------------------
# Fonctions d'extraction élémentaires
# --------------------------------------------------------------------------

def normaliser(texte: str) -> str:
    """NFKC : espaces insécables -> espaces, '²' -> '2'. Puis espaces compactés."""
    texte = unicodedata.normalize("NFKC", texte)
    return re.sub(r"[ \t ]+", " ", texte)


def normaliser_lien(lien: str) -> str:
    """Retire les paramètres de suivi (utm_…) et l'ancre d'un lien."""
    p = urlsplit(lien)
    query = urlencode([(k, v) for k, v in parse_qsl(p.query) if not k.lower().startswith("utm_")])
    return urlunsplit((p.scheme, p.netloc.lower(), p.path, query, ""))


def extraire_prix(texte: str) -> Tuple[Optional[int], bool]:
    """Renvoie (prix charges comprises, charges_incertaines)."""
    charges = None
    m = CHARGES_PLUS_RE.search(texte)
    plus = m is not None  # "800 € + 50 € charges" => le 800 est hors charges
    if not m:
        m = CHARGES_RE.search(texte)
    if m:
        charges = int(m.group(1))

    montants = []
    for m in PRIX_RE.finditer(texte):
        valeur = int(re.sub(r"\D", "", m.group(1)))
        if 150 <= valeur <= 20000:  # écarte les montants absurdes pour un loyer
            montants.append(valeur)
    if charges in montants:
        montants.remove(charges)
    if not montants:
        return None, False

    hc = bool(HC_RE.search(texte)) or plus
    cc = bool(CC_RE.search(texte))
    if hc and cc:
        # "800 € HC, soit 850 € CC" : le prix CC est le plus élevé des deux.
        return max(montants[:2]), False
    if hc:
        if charges is not None:
            return montants[0] + charges, False
        return montants[0], True  # HC mais charges inconnues
    # "CC" indiqué, ou rien d'indiqué : on considère le prix comme charges comprises.
    return montants[0], False


def extraire_surface(texte: str) -> Optional[float]:
    m = SURFACE_RE.search(texte)
    return float(m.group(1).replace(",", ".")) if m else None


def extraire_email(texte: str, a_exclure=()) -> Optional[str]:
    for adresse in EMAIL_RE.findall(texte):
        a = adresse.lower()
        local, _, domaine = a.partition("@")
        if a in a_exclure:
            continue
        if any(d in domaine for d in DOMAINES_IGNORES):
            continue
        if any(local.startswith(p) for p in PREFIXES_IGNORES):
            continue
        return a
    return None


def extraire_telephone(texte: str) -> Optional[str]:
    m = TELEPHONE_RE.search(texte)
    return re.sub(r"[\s.\-]", " ", m.group(0)).strip() if m else None


def detecter_source(expediteur: str) -> str:
    """'Jinka <alerte@jinka.fr>' -> 'Jinka'"""
    adresse = parseaddr(expediteur)[1].lower()
    for cle, nom in (("jinka", "Jinka"), ("seloger", "SeLoger"), ("pap", "PAP")):
        if re.search(rf"[@.\-]{cle}[.\-]", adresse):  # @pap.fr, @mail.jinka.fr, @seloger-mail.com
            return nom
    return adresse or "inconnu"


# --------------------------------------------------------------------------
# Découpage de l'email en blocs d'annonces
# --------------------------------------------------------------------------

def _nb_annonces(texte: str) -> int:
    """Nombre approximatif d'annonces dans un texte (= nombre de surfaces)."""
    n = len(SURFACE_RE.findall(texte))
    return n if n else len(PRIX_RE.findall(texte))


def _blocs_html(html: str) -> Iterator[Tuple[str, str]]:
    """Renvoie (texte du bloc, lien de l'annonce) pour chaque carte d'annonce."""
    soup = BeautifulSoup(html, "html.parser")
    for balise in soup(["script", "style", "head"]):
        balise.decompose()

    cache = {}

    def texte(el) -> str:
        if id(el) not in cache:
            cache[id(el)] = normaliser(el.get_text(" ", strip=True))
        return cache[id(el)]

    blocs = {}  # id(bloc) -> (bloc, [(distance au prix, ordre, lien)])
    for ordre, a in enumerate(soup.find_all("a", href=True)):
        href = a["href"].strip()
        if not href.lower().startswith("http") or LIEN_IGNORE_RE.search(href):
            continue
        if TEXTE_LIEN_IGNORE_RE.search(texte(a)):
            continue

        bloc, profondeur, distance_prix = a, 0, None
        if PRIX_RE.search(texte(a)):
            distance_prix = 0
        # On remonte tant que le parent ne contient pas une 2e annonce.
        while bloc.parent is not None and bloc.parent.name != "[document]":
            if _nb_annonces(texte(bloc.parent)) > 1:
                break
            bloc = bloc.parent
            profondeur += 1
            if distance_prix is None and PRIX_RE.search(texte(bloc)):
                distance_prix = profondeur
        if distance_prix is None:
            continue  # aucun prix autour de ce lien : ce n'est pas une annonce
        blocs.setdefault(id(bloc), (bloc, []))[1].append((distance_prix, ordre, href))

    for bloc, liens in blocs.values():
        # Le lien retenu est celui le plus « proche » du prix (pas le logo du site).
        liens.sort()
        contacts = " ".join(
            a["href"].split(":", 1)[1].split("?")[0]
            for a in bloc.find_all("a", href=re.compile(r"^(mailto|tel):", re.I))
        )
        yield texte(bloc) + " " + contacts, liens[0][2]


def _blocs_texte(texte: str) -> Iterator[Tuple[str, str]]:
    """Version de secours pour les emails sans HTML : un paragraphe = une annonce."""
    paragraphes = re.split(r"\n\s*\n", normaliser(texte))
    for i, p in enumerate(paragraphes):
        if not PRIX_RE.search(p):
            continue
        urls = URL_RE.findall(p) or (URL_RE.findall(paragraphes[i + 1]) if i + 1 < len(paragraphes) else [])
        urls = [u for u in urls if not LIEN_IGNORE_RE.search(u)]
        if urls:
            yield p, urls[0]


def corps_email(msg: EmailMessage) -> Tuple[Optional[str], Optional[str]]:
    """Renvoie (html, texte) du message (None si absent)."""
    html = texte = None
    for part in msg.walk():
        if part.is_multipart() or part.get_filename():
            continue
        type_ = part.get_content_type()
        if type_ not in ("text/html", "text/plain"):
            continue
        try:
            contenu = part.get_content()
        except Exception:  # encodage exotique : on décode « au mieux »
            contenu = (part.get_payload(decode=True) or b"").decode("utf-8", "replace")
        if type_ == "text/html" and html is None:
            html = contenu
        elif type_ == "text/plain" and texte is None:
            texte = contenu
    return html, texte


def extraire_annonces(msg: EmailMessage, a_exclure=()) -> List[Annonce]:
    """Point d'entrée : transforme un email en liste d'annonces."""
    source = detecter_source(msg.get("From", ""))
    html, texte = corps_email(msg)

    blocs = list(_blocs_html(html)) if html else []
    if not blocs and texte:
        blocs = list(_blocs_texte(texte))

    annonces, liens_vus = [], set()
    for bloc, lien in blocs:
        if BLOC_IGNORE_RE.search(bloc) or lien in liens_vus:
            continue
        liens_vus.add(lien)
        prix, incertain = extraire_prix(bloc)
        ville = zones.detecter_zone(bloc)
        annonces.append(Annonce(
            source=source,
            lien=lien,
            texte=bloc[:500],
            prix_cc=prix,
            charges_incertaines=incertain,
            surface=extraire_surface(bloc),
            ville=ville,
            quartier=zones.detecter_quartier(bloc, ville),
            email_contact=extraire_email(bloc, a_exclure),
            telephone=extraire_telephone(bloc),
        ))
    return annonces


def est_eligible(a: Annonce, prix_max: int, zones_voulues: set) -> Tuple[bool, str]:
    """Applique vos critères. Renvoie (gardée ?, raison)."""
    if a.prix_cc is None:
        return False, "prix introuvable"
    if a.ville is None:
        return False, "ville introuvable"
    if a.prix_cc > prix_max:
        return False, f"trop cher ({a.prix_cc} €)"
    if a.ville not in zones_voulues:
        return False, f"hors zone ({a.ville})"
    return True, "ok"
