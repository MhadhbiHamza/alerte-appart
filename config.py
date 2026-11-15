"""Lecture de la configuration depuis le fichier .env.

Toutes les valeurs personnelles (identifiants, critères de recherche) sont
dans .env : le code n'a jamais besoin d'être modifié pour les changer.
"""
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

import zones

# Dossier où se trouve ce fichier : tous les chemins relatifs partent d'ici.
DOSSIER_PROJET = Path(__file__).resolve().parent


@dataclass
class Config:
    gmail_adresse: str
    gmail_mot_de_passe: str
    dossier_imap: str
    telegram_token: str
    telegram_chat_id: str
    prix_max: int
    zones: set
    priorites: set
    expediteurs: list
    dossier_pdf: Path
    intervalle_secondes: int
    relance_heures: int
    jours_historique: int
    fichier_modele: Path
    fichier_csv: Path
    fichier_db: Path
    prix_max_coloc: int = 600
    email_reponse: str = ""
    nom_expediteur: str = ""
    revenu_net_mensuel: int = 0
    sources_toutes_zones: set = frozenset()
    rouges: str = "soir"            # 🔴 : soir (résumé du soir) | direct | jamais
    heure_resume_soir: Optional[int] = 21
    heure_bilan_matin: Optional[int] = 8
    imap_idle: bool = True
    # Agents (IA gratuite compatible OpenAI + recherche Tavily). Sans clés : désactivés.
    llm_url: str = "https://api.groq.com/openai/v1"
    llm_modele: str = "openai/gpt-oss-120b"
    llm_cle: str = ""
    tavily_cle: str = ""
    agents: bool = True
    agents_niveaux: frozenset = frozenset({"vert", "jaune"})
    llm_max_jour: int = 300
    recherches_max_jour: int = 30

    @property
    def agents_actifs(self) -> bool:
        return self.agents and bool(self.llm_cle)


def _chemin(valeur: str) -> Path:
    """Transforme un chemin relatif en chemin absolu (relatif au projet)."""
    p = Path(valeur).expanduser()
    return p if p.is_absolute() else DOSSIER_PROJET / p


def _heure(valeur: str) -> Optional[int]:
    """« 21 » -> 21 ; vide -> None (fonction désactivée)."""
    valeur = (valeur or "").strip().lower().rstrip("h")
    return int(valeur) if valeur.isdigit() and 0 <= int(valeur) <= 23 else None


def _liste(valeur: str) -> list:
    """'11, 14 ,15' -> ['11', '14', '15']"""
    return [x.strip() for x in valeur.split(",") if x.strip()]


ZONES_PAR_DEFAUT = (
    "Paris, "  # = les 20 arrondissements
    "Ivry-sur-Seine, Maisons-Alfort, Clamart, Asnières-sur-Seine, Bois-Colombes, "
    "Vitry-sur-Seine, Saint-Denis, Montreuil, Meudon, "
    "Vincennes, Charenton-le-Pont, Montrouge, Clichy, Boulogne-Billancourt, Issy-les-Moulineaux"
)
PRIORITES_PAR_DEFAUT = "Ivry-sur-Seine, Maisons-Alfort, Paris 13e, Paris 14e, Asnières-sur-Seine"


def _zones(valeur: str, variable: str) -> set:
    """« Paris 13, asnieres » -> {"Paris 13e", "Asnières-sur-Seine"} (erreur claire si inconnu)."""
    resultat, inconnues = set(), []
    for nom in _liste(valeur):
        if zones.simplifier(nom).strip() in ("paris", "tout paris", "paris intramuros"):
            resultat.update(zones.libelle_paris(n) for n in range(1, 21))  # tout Paris
            continue
        zone = zones.normaliser_zone(nom)
        if zone:
            resultat.add(zone)
        else:
            inconnues.append(nom)
    if inconnues:
        raise SystemExit(
            f"{variable} dans .env : zone(s) inconnue(s) : {', '.join(inconnues)}\n"
            "Écrivez « Paris » (tout Paris), « Paris 13e » pour un arrondissement, ou une de ces communes : "
            + ", ".join(zones.COMMUNES) + "\n(pour une autre commune, ajoutez-la dans zones.py)"
        )
    return resultat


def charger_config() -> Config:
    """Charge .env puis construit l'objet Config (avec valeurs par défaut)."""
    load_dotenv(DOSSIER_PROJET / ".env")
    g = os.getenv
    return Config(
        gmail_adresse=g("GMAIL_ADRESSE", "").strip(),
        # Google affiche le mot de passe d'application avec des espaces : on les retire.
        gmail_mot_de_passe=g("GMAIL_MOT_DE_PASSE_APPLICATION", "").replace(" ", ""),
        dossier_imap=g("DOSSIER_IMAP", "INBOX").strip(),
        telegram_token=g("TELEGRAM_BOT_TOKEN", "").strip(),
        telegram_chat_id=g("TELEGRAM_CHAT_ID", "").strip(),
        prix_max=int(g("PRIX_MAX_CC", "900")),
        zones=_zones(g("ZONES", ZONES_PAR_DEFAUT), "ZONES"),
        priorites=_zones(g("PRIORITES", PRIORITES_PAR_DEFAUT), "PRIORITES"),
        expediteurs=_liste(g("EXPEDITEURS", "jinka,seloger,pap")),
        dossier_pdf=_chemin(g("DOSSIER_PDF", "dossier.pdf")),
        intervalle_secondes=int(g("INTERVALLE_SECONDES", "120")),
        relance_heures=int(g("RELANCE_HEURES", "48")),
        jours_historique=int(g("JOURS_HISTORIQUE", "2")),
        fichier_modele=_chemin(g("FICHIER_MODELE", "templates.md")),
        fichier_csv=_chemin(g("FICHIER_SUIVI", "suivi.csv")),
        fichier_db=_chemin(g("FICHIER_DB", "annonces.db")),
        prix_max_coloc=int(g("PRIX_MAX_COLOC", "600")),
        email_reponse=g("EMAIL_REPONSE", "").strip(),
        nom_expediteur=g("NOM_EXPEDITEUR", "").strip(),
        revenu_net_mensuel=int(g("REVENU_NET_MENSUEL", "0") or 0),
        # Sites dont vous gardez TOUTES les annonces, quel que soit le lieu (ex. alerte « 30 min de… »)
        sources_toutes_zones={s.lower() for s in _liste(g("SOURCES_TOUTES_ZONES", "seloger,jinka"))},
        rouges=g("ROUGES", "soir").strip().lower() or "soir",
        heure_resume_soir=_heure(g("HEURE_RESUME_SOIR", "21")),
        heure_bilan_matin=_heure(g("HEURE_BILAN_MATIN", "8")),
        imap_idle=g("IMAP_IDLE", "1").strip() not in ("0", "non", "false", ""),
        llm_url=(g("LLM_URL", "") or "https://api.groq.com/openai/v1").strip().rstrip("/"),
        llm_modele=(g("LLM_MODELE", "") or "openai/gpt-oss-120b").strip(),
        llm_cle=g("LLM_CLE", "").strip(),
        tavily_cle=g("TAVILY_CLE", "").strip(),
        agents=g("AGENTS", "1").strip() not in ("0", "non", "false"),
        agents_niveaux=frozenset(x.lower() for x in _liste(g("AGENTS_NIVEAUX", "vert,jaune"))),
        llm_max_jour=int(g("LLM_MAX_JOUR", "300") or 300),
        recherches_max_jour=int(g("RECHERCHES_MAX_JOUR", "30") or 30),
    )


def verifier(cfg: Config, *champs: str) -> None:
    """Arrête le programme avec un message clair si un identifiant manque."""
    noms_env = {
        "gmail_adresse": "GMAIL_ADRESSE",
        "gmail_mot_de_passe": "GMAIL_MOT_DE_PASSE_APPLICATION",
        "telegram_token": "TELEGRAM_BOT_TOKEN",
        "telegram_chat_id": "TELEGRAM_CHAT_ID",
    }
    manquants = [noms_env[c] for c in champs if not getattr(cfg, c)]
    if manquants:
        raise SystemExit(
            "Il manque dans le fichier .env : " + ", ".join(manquants)
            + "\n(copiez .env.example en .env et remplissez-le, voir README.md)"
        )
