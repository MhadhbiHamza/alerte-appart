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


class ConflitTelegram(RuntimeError):
    """Deux exemplaires de l'outil (ex. PC + serveur) lisent les boutons du même bot."""


# Une seule connexion réutilisée (keep-alive) : beaucoup moins de trafic qu'une connexion par appel,
# important sur un serveur gratuit où le trafic sortant est compté.
_session = requests.Session()


def _appel(token: str, methode: str, donnees: dict, timeout: int = 20, fichiers: Optional[dict] = None):
    try:
        url = API.format(token=token, methode=methode)
        if fichiers:  # envoi de fichier : formulaire multipart
            r = _session.post(url, data=donnees, files=fichiers, timeout=timeout)
        else:
            r = _session.post(url, json=donnees, timeout=timeout)
    except requests.RequestException as exc:
        # On ne remonte PAS l'erreur d'origine : elle contient l'adresse complète, donc le token.
        raise TelegramInjoignable(f"Telegram injoignable ({type(exc).__name__})") from None
    if r.status_code == 409:
        raise ConflitTelegram("un autre exemplaire de l'outil tourne déjà avec ce bot (sur votre PC ?)")
    if not r.ok:
        raise RuntimeError(f"Telegram ({methode}) a refusé : {r.status_code} {r.text}")
    return r.json().get("result")


def envoyer_fichier(token: str, chat_id: str, chemin, legende: str = "") -> None:
    """Envoie un fichier (ex. suivi.csv) dans la conversation."""
    with open(chemin, "rb") as f:
        _appel(token, "sendDocument", {"chat_id": chat_id, "caption": legende[:1024]},
               timeout=60, fichiers={"document": (chemin.name, f)})


def _bouton(texte: str, donnee: str) -> dict:
    # Une donnée qui commence par http devient un bouton lien (ouvre la page directement).
    if donnee.startswith("http"):
        return {"text": texte, "url": donnee}
    return {"text": texte, "callback_data": donnee}


def _clavier(boutons: Boutons) -> dict:
    return {"inline_keyboard": [[_bouton(t, d) for t, d in ligne] for ligne in boutons]}


def envoyer(token: str, chat_id: str, texte_html: str,
            boutons: Optional[Boutons] = None, apercu: Optional[str] = None, silencieux: bool = False,
            repondre_a: Optional[int] = None) -> int:
    """Envoie un message (HTML de Telegram : <b>, <a>, <pre>…) et renvoie son numéro.

    apercu : adresse d'une image (ou de l'annonce) affichée en grand au-dessus du texte.
    C'est Telegram qui génère l'aperçu : l'outil lui-même ne visite aucun site.
    """
    donnees = {"chat_id": chat_id, "text": texte_html, "parse_mode": "HTML",
               "disable_notification": silencieux}  # silencieux : arrive sans son
    if apercu:
        donnees["link_preview_options"] = {"url": apercu, "prefer_large_media": True, "show_above_text": True}
    else:
        donnees["link_preview_options"] = {"is_disabled": True}
    if boutons:
        donnees["reply_markup"] = _clavier(boutons)
    if repondre_a:  # affiché comme une réponse à ce message (ex. rapport d'agent sous l'annonce)
        donnees["reply_parameters"] = {"message_id": repondre_a, "allow_sending_without_reply": True}
    return _appel(token, "sendMessage", donnees)["message_id"]


def envoyer_photos(token: str, chat_id: str, photos: List[str], legende: str = "",
                   silencieux: bool = False) -> List[int]:
    """Envoie les photos de l'annonce (1 photo, ou un album de 2 à 10 photos).

    Ce sont les adresses d'images présentes dans l'email d'alerte : c'est Telegram
    qui les télécharge et les affiche. Renvoie les numéros des messages (pour les supprimer).
    """
    if len(photos) == 1:
        r = _appel(token, "sendPhoto", {"chat_id": chat_id, "photo": photos[0], "caption": legende[:1024],
                                        "disable_notification": silencieux})
        return [r["message_id"]]
    if photos:
        album = [{"type": "photo", "media": url} for url in photos[:10]]
        album[0]["caption"] = legende[:1024]
        return [m["message_id"] for m in _appel(token, "sendMediaGroup", {
            "chat_id": chat_id, "media": album, "disable_notification": silencieux})]
    return []


def demander_reponse(token: str, chat_id: str, texte_html: str, exemple: str = "") -> int:
    """Envoie une question à laquelle Telegram propose directement de répondre."""
    return _appel(token, "sendMessage", {
        "chat_id": chat_id, "text": texte_html, "parse_mode": "HTML",
        "reply_markup": {"force_reply": True, "input_field_placeholder": exemple[:64]},
    })["message_id"]


def modifier_texte(token: str, chat_id: str, message_id: int, texte_html: str, boutons: Boutons) -> None:
    """Remplace le texte d'un message (ex. carte compacte <-> détails), en gardant des boutons."""
    _appel(token, "editMessageText", {
        "chat_id": chat_id, "message_id": message_id, "text": texte_html, "parse_mode": "HTML",
        "link_preview_options": {"is_disabled": True}, "reply_markup": _clavier(boutons)})


def definir_commandes(token: str, commandes: List[Tuple[str, str]]) -> None:
    """Affiche les commandes (/pause, /stats…) dans le menu du bot."""
    _appel(token, "setMyCommands", {"commands": [{"command": c, "description": d} for c, d in commandes]})


def supprimer_message(token: str, chat_id: str, message_id: int) -> None:
    """Supprime un message du bot (possible pendant 48 h après son envoi)."""
    _appel(token, "deleteMessage", {"chat_id": chat_id, "message_id": message_id})


def mises_a_jour(token: str, offset: int, attente: int) -> list:
    """Attend jusqu'à `attente` secondes vos appuis sur les boutons (« long polling »)."""
    return _appel(token, "getUpdates",
                  {"offset": offset, "timeout": attente, "allowed_updates": ["callback_query", "message"]},
                  timeout=attente + 15) or []


def repondre_clic(token: str, id_clic: str, texte: str = "", alerte: bool = False) -> bool:
    """Affiche une courte réponse à l'appui (alerte=True : fenêtre à valider).

    Renvoie False si Telegram refuse parce que l'appui est trop ancien (l'outil était en veille ou
    occupé quand vous avez appuyé) : l'appelant envoie alors la réponse en message normal.
    """
    try:
        _appel(token, "answerCallbackQuery", {"callback_query_id": id_clic, "text": texte[:200],
                                               "show_alert": alerte})
    except RuntimeError as exc:
        if "query is too old" in str(exc) or "query ID is invalid" in str(exc):
            return False
        raise
    return True


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
