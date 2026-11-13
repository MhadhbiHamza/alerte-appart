"""Envoi des notifications Telegram (API officielle des bots, via HTTPS)."""
import html

import requests

API = "https://api.telegram.org/bot{token}/{methode}"
LIMITE = 4096  # taille maximale d'un message Telegram


def envoyer(token: str, chat_id: str, texte_html: str) -> None:
    """Envoie un message (mise en forme HTML de Telegram : <b>, <a>, <pre>…).

    Le texte doit faire moins de LIMITE caractères (main.py s'en charge).
    """
    r = requests.post(
        API.format(token=token, methode="sendMessage"),
        json={"chat_id": chat_id, "text": texte_html, "parse_mode": "HTML",
              "disable_web_page_preview": True},
        timeout=20,
    )
    if not r.ok:
        raise RuntimeError(f"Telegram a refusé le message : {r.status_code} {r.text}")


def trouver_chat_id(token: str) -> list:
    """Liste les conversations récentes du bot (pour trouver votre TELEGRAM_CHAT_ID)."""
    r = requests.get(API.format(token=token, methode="getUpdates"), timeout=20)
    r.raise_for_status()
    chats = {}
    for maj in r.json().get("result", []):
        chat = (maj.get("message") or maj.get("channel_post") or {}).get("chat")
        if chat:
            nom = chat.get("first_name") or chat.get("title") or ""
            chats[chat["id"]] = nom
    return list(chats.items())


def echapper(texte, attribut: bool = False) -> str:
    """Protège les caractères spéciaux (<, >, &) pour le mode HTML de Telegram.

    attribut=True protège aussi les guillemets (pour un lien href="...").
    """
    texte = html.escape(str(texte), quote=False)
    return texte.replace('"', "&quot;") if attribut else texte
