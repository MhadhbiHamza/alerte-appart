"""Échanges avec Telegram (API officielle des bots, via HTTPS).

- envoyer()           : envoie un message, avec boutons et aperçu photo ;
- mises_a_jour()      : récupère vos appuis sur les boutons ;
- repondre_clic()     : petit message qui s'affiche quand vous appuyez ;
- modifier_boutons()  : remplace les boutons d'un message (ex. « ✅ Dossier envoyé »).
"""
import html
from typing import List, Optional, Tuple

import requests

API = "https://api.telegram.org/bot{token}/{methode}"
LIMITE = 4096  # taille maximale d'un message Telegram

Boutons = List[List[Tuple[str, str]]]  # lignes de (texte du bouton, donnée renvoyée à l'appui)


class TelegramInjoignable(ConnectionError):
    """Pas de connexion vers Telegram (Internet coupé, Wi-Fi, VPN, pare-feu…)."""


def _appel(token: str, methode: str, donnees: dict, timeout: int = 20):
    try:
        r = requests.post(API.format(token=token, methode=methode), json=donnees, timeout=timeout)
    except requests.RequestException as exc:
        # On ne remonte PAS l'erreur d'origine : elle contient l'adresse complète, donc le token.
        raise TelegramInjoignable(f"Telegram injoignable ({type(exc).__name__})") from None
    if not r.ok:
        raise RuntimeError(f"Telegram ({methode}) a refusé : {r.status_code} {r.text}")
    return r.json().get("result")


def _clavier(boutons: Boutons) -> dict:
    return {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in ligne] for ligne in boutons]}


def envoyer(token: str, chat_id: str, texte_html: str,
            boutons: Optional[Boutons] = None, apercu: Optional[str] = None) -> int:
    """Envoie un message (HTML de Telegram : <b>, <a>, <pre>…) et renvoie son numéro.

    apercu : adresse d'une image (ou de l'annonce) affichée en grand au-dessus du texte.
    C'est Telegram qui génère l'aperçu : l'outil lui-même ne visite aucun site.
    """
    donnees = {"chat_id": chat_id, "text": texte_html, "parse_mode": "HTML"}
    if apercu:
        donnees["link_preview_options"] = {"url": apercu, "prefer_large_media": True, "show_above_text": True}
    else:
        donnees["link_preview_options"] = {"is_disabled": True}
    if boutons:
        donnees["reply_markup"] = _clavier(boutons)
    return _appel(token, "sendMessage", donnees)["message_id"]


def mises_a_jour(token: str, offset: int, attente: int) -> list:
    """Attend jusqu'à `attente` secondes vos appuis sur les boutons (« long polling »)."""
    return _appel(token, "getUpdates",
                  {"offset": offset, "timeout": attente, "allowed_updates": ["callback_query", "message"]},
                  timeout=attente + 15) or []


def repondre_clic(token: str, id_clic: str, texte: str = "", alerte: bool = False) -> None:
    """Affiche une courte réponse à l'appui (alerte=True : fenêtre à valider)."""
    _appel(token, "answerCallbackQuery", {"callback_query_id": id_clic, "text": texte[:200], "show_alert": alerte})


def modifier_boutons(token: str, chat_id: str, message_id: int, boutons: Boutons) -> None:
    _appel(token, "editMessageReplyMarkup",
           {"chat_id": chat_id, "message_id": message_id, "reply_markup": _clavier(boutons)})


def trouver_chat_id(token: str) -> list:
    """Liste les conversations récentes du bot (pour trouver votre TELEGRAM_CHAT_ID)."""
    chats = {}
    for maj in _appel(token, "getUpdates", {}) or []:
        chat = (maj.get("message") or maj.get("channel_post") or {}).get("chat")
        if chat:
            chats[chat["id"]] = chat.get("first_name") or chat.get("title") or ""
    return list(chats.items())


def echapper(texte, attribut: bool = False) -> str:
    """Protège les caractères spéciaux (<, >, &) pour le mode HTML de Telegram.

    attribut=True protège aussi les guillemets (pour un lien href="...").
    """
    texte = html.escape(str(texte), quote=False)
    return texte.replace('"', "&quot;") if attribut else texte
