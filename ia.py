"""Client d'IA générique, compatible avec l'API « OpenAI » (Groq, Gemini, OpenRouter, Mistral…).

Par défaut : Groq, gratuit (https://console.groq.com → API Keys), modèle openai/gpt-oss-120b.
Pour changer de plateforme, il suffit de modifier LLM_URL, LLM_MODELE et LLM_CLE dans .env.

Ce module n'envoie que ce qu'on lui donne : les agents (agents.py) ne lui passent jamais
vos données personnelles (salaire, modèle de message, emails, dossier).
"""
import json
import logging
import time
from datetime import date
from typing import List, Optional

import requests

log = logging.getLogger("alerte-appart")
_session = requests.Session()


class IAIndisponible(RuntimeError):
    """Pas de clé, quota du jour atteint, ou service injoignable."""


def _compter(db, cle: str, maximum: int) -> None:
    """Compte un appel du jour ; refuse au-delà de `maximum` (quota journalier)."""
    if db is None:
        return
    jour, n = (db.lire_etat(cle, "") or ":0").split(":")
    n = int(n) if jour == date.today().isoformat() else 0
    if n >= maximum:
        raise IAIndisponible(f"quota du jour atteint ({maximum})")
    db.ecrire_etat(cle, f"{date.today().isoformat()}:{n + 1}")


def utilise_aujourdhui(db, cle: str) -> int:
    jour, _, n = (db.lire_etat(cle, "") or ":0").partition(":")
    return int(n or 0) if jour == date.today().isoformat() else 0


def discuter(cfg, messages: List[dict], outils: Optional[list] = None, format_json: bool = False,
             db=None, essais: int = 3) -> dict:
    """Envoie une conversation et renvoie le message de réponse ({"content": …, "tool_calls": …})."""
    if not cfg.llm_cle:
        raise IAIndisponible("LLM_CLE absente du fichier .env")
    _compter(db, "quota_llm", cfg.llm_max_jour)
    donnees = {"model": cfg.llm_modele, "messages": messages, "temperature": 0.2}
    if outils:
        donnees["tools"] = outils
        donnees["tool_choice"] = "auto"
    if format_json:
        donnees["response_format"] = {"type": "json_object"}
    for essai in range(essais):
        try:
            r = _session.post(f"{cfg.llm_url}/chat/completions", json=donnees, timeout=60,
                              headers={"Authorization": f"Bearer {cfg.llm_cle}"})
        except requests.RequestException as exc:
            # Pas d'erreur d'origine : elle pourrait contenir des en-têtes (donc la clé).
            raise IAIndisponible(f"service d'IA injoignable ({type(exc).__name__})") from None
        if r.status_code == 429 and essai < essais - 1:  # trop de requêtes : on patiente
            attente = min(float(r.headers.get("retry-after", 0) or 0) or 5 * (essai + 1), 60)
            log.info("IA : limite atteinte, nouvel essai dans %.0f s", attente)
            time.sleep(attente)
            continue
        if r.status_code == 404 and "model" in r.text.lower():
            # Les plateformes retirent régulièrement des modèles : on dit lesquels sont disponibles.
            proposes = ", ".join(modeles_disponibles(cfg)[:8]) or "voir la console de votre plateforme"
            raise IAIndisponible(f"le modèle « {cfg.llm_modele} » n'existe plus ou n'est pas accessible. "
                                 f"Changez LLM_MODELE dans .env. Modèles disponibles : {proposes}")
        if not r.ok:
            raise IAIndisponible(f"le service d'IA a refusé : {r.status_code} {r.text[:200]}")
        return r.json()["choices"][0]["message"]
    raise IAIndisponible("limite de requêtes atteinte")


# Ordre de préférence pour suggérer un remplaçant (grands modèles qui savent utiliser des outils).
PREFERES = ("gpt-oss-120b", "qwen", "llama-4", "llama", "gpt-oss", "gemini", "mistral")


def modeles_disponibles(cfg) -> List[str]:
    """Modèles accessibles avec votre clé, les plus adaptés aux agents en premier."""
    try:
        r = _session.get(f"{cfg.llm_url}/models", timeout=20, headers={"Authorization": f"Bearer {cfg.llm_cle}"})
        ids = [m.get("id", "") for m in r.json().get("data", [])] if r.ok else []
    except (requests.RequestException, ValueError):
        return []
    rang = lambda nom: next((i for i, p in enumerate(PREFERES) if p in nom.lower()), len(PREFERES))  # noqa: E731
    exclus = ("whisper", "tts", "guard", "embed", "vision", "audio", "orpheus", "playai")
    return sorted((i for i in ids if i and not any(x in i.lower() for x in exclus)), key=lambda n: (rang(n), n))


def demander_json(cfg, systeme: str, utilisateur: str, db=None) -> dict:
    """Pose une question et attend un objet JSON en réponse (vide si illisible)."""
    reponse = discuter(cfg, [{"role": "system", "content": systeme}, {"role": "user", "content": utilisateur}],
                       format_json=True, db=db)
    texte = (reponse.get("content") or "").strip()
    if texte.startswith("```"):
        texte = texte.strip("`").removeprefix("json").strip()
    try:
        resultat = json.loads(texte)
        return resultat if isinstance(resultat, dict) else {}
    except ValueError:
        log.warning("IA : réponse JSON illisible (%s…)", texte[:80])
        return {}
