"""Recherche web (Tavily) et lecture des pages d'agences pour trouver un contact.

- Tavily : 1 000 recherches gratuites par mois (https://app.tavily.com), clé TAVILY_CLE.
- Pages lues : uniquement des sites d'agences, robots.txt respecté, jamais les sites
  d'annonces (SeLoger, PAP, Jinka, Leboncoin, Bien'ici, Logic-Immo…).
"""
import logging
from typing import List
from urllib import robotparser
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup

import ia
from extraction import normaliser

log = logging.getLogger("alerte-appart")
URL_TAVILY = "https://api.tavily.com/search"
AGENT_HTTP = "Mozilla/5.0 (compatible; recherche-contact-agence/1.0)"
TAILLE_MAX = 500_000
# Sites d'annonces : jamais lus automatiquement (conditions d'utilisation).
DOMAINES_INTERDITS = ("seloger.", "pap.fr", "pap.com", "jinka.", "leboncoin.", "bienici.", "logic-immo.",
                      "figaroimmo", "explorimmo", "avendrealouer", "paruvendu", "lacartedescolocs",
                      "appartager", "studapart", "facebook.", "instagram.")
_session = requests.Session()
_session.headers["User-Agent"] = AGENT_HTTP


class RechercheIndisponible(RuntimeError):
    pass


def domaine_interdit(url: str) -> bool:
    hote = urlsplit(url).netloc.lower()
    return any(d in hote for d in DOMAINES_INTERDITS)


def chercher(cfg, db, requete: str, n: int = 5) -> List[dict]:
    """Renvoie [{titre, url, extrait}] (extraits des sites d'annonces exclus)."""
    if not cfg.tavily_cle:
        raise RechercheIndisponible("TAVILY_CLE absente du fichier .env")
    try:
        ia._compter(db, "quota_recherche", cfg.recherches_max_jour)
    except ia.IAIndisponible as exc:
        raise RechercheIndisponible(str(exc)) from None
    try:
        r = _session.post(URL_TAVILY, timeout=30, json={
            "api_key": cfg.tavily_cle, "query": requete[:300], "max_results": n,
            "search_depth": "basic", "include_answer": False})
    except requests.RequestException as exc:
        raise RechercheIndisponible(f"recherche injoignable ({type(exc).__name__})") from None
    if not r.ok:
        raise RechercheIndisponible(f"la recherche a refusé : {r.status_code}")
    resultats = []
    for item in r.json().get("results", []):
        url = item.get("url", "")
        if url and not domaine_interdit(url):
            resultats.append({"titre": item.get("title", ""), "url": url,
                              "extrait": normaliser(item.get("content", ""))[:1500]})
    return resultats


def _autorise_par_robots(url: str) -> bool:
    parties = urlsplit(url)
    lecteur = robotparser.RobotFileParser()
    try:
        r = _session.get(f"{parties.scheme}://{parties.netloc}/robots.txt", timeout=10)
        if r.status_code >= 400:
            return True  # pas de robots.txt : autorisé
        lecteur.parse(r.text.splitlines())
    except requests.RequestException:
        return True
    return lecteur.can_fetch(AGENT_HTTP, url)


def lire_page(url: str) -> str:
    """Texte d'une page d'agence (vide si interdite, refusée par robots.txt ou illisible)."""
    if not url.startswith("http") or domaine_interdit(url):
        return ""
    if not _autorise_par_robots(url):
        log.info("Agent : %s refusé par robots.txt", urlsplit(url).netloc)
        return ""
    try:
        r = _session.get(url, timeout=15, stream=True)
        contenu = r.raw.read(TAILLE_MAX, decode_content=True) if r.ok else b""
    except requests.RequestException:
        return ""
    if "html" not in r.headers.get("content-type", "html"):
        return ""
    soup = BeautifulSoup(contenu, "html.parser")
    for balise in soup(["script", "style", "noscript"]):
        balise.decompose()
    liens = " ".join(a["href"].split(":", 1)[1] for a in soup.find_all("a", href=True)
                     if a["href"].lower().startswith(("mailto:", "tel:")))
    return (normaliser(soup.get_text(" ", strip=True)) + " " + liens)[:20000]
