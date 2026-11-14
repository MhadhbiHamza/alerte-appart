"""Accès à Gmail : lecture des alertes (IMAP), brouillons (IMAP) et envoi (SMTP).

Important : envoyer_email() n'est appelée QUE lorsque vous appuyez sur
« ✅ Postuler » sous une annonce dans Telegram. L'outil n'envoie jamais rien
de lui-même. L'email envoyé apparaît dans « Messages envoyés » de Gmail.
"""
import email
import imaplib
import re
import smtplib
import time
from datetime import date, timedelta
from email import policy
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path
from typing import Callable, Iterator, Optional, Tuple

SERVEUR_IMAP = "imap.gmail.com"
SERVEUR_SMTP = "smtp.gmail.com"
# Noms des mois en anglais imposés par IMAP (indépendant de la langue de l'ordinateur).
MOIS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _guillemets(nom: str) -> str:
    """Les noms de dossiers comme [Gmail]/Brouillons doivent être entre guillemets."""
    return nom if nom.startswith('"') else f'"{nom}"'


def connexion(adresse: str, mot_de_passe: str) -> imaplib.IMAP4_SSL:
    imap = imaplib.IMAP4_SSL(SERVEUR_IMAP, 993)
    imap.login(adresse, mot_de_passe)
    return imap


def critere_recherche(expediteurs: list, jours: int) -> str:
    """Construit la recherche IMAP : (OR FROM "jinka" FROM "seloger" ...) SINCE 11-Nov-2026

    En IMAP, OR prend 2 arguments : pour 3 expéditeurs on écrit
    OR OR FROM a FROM b FROM c.
    """
    d = date.today() - timedelta(days=jours)
    depuis = f"{d.day:02d}-{MOIS[d.month - 1]}-{d.year}"
    froms = " ".join(f'FROM "{e}"' for e in expediteurs)
    ors = "OR " * (len(expediteurs) - 1)
    return f"({ors}{froms}) SINCE {depuis}"


def lire_emails(
    imap: imaplib.IMAP4_SSL,
    dossier: str,
    expediteurs: list,
    jours: int,
    deja_traite: Optional[Callable[[str], bool]] = None,
) -> Iterator[Tuple[str, EmailMessage]]:
    """Renvoie (clé unique, message) pour chaque email d'alerte non encore traité.

    - readonly=True + BODY.PEEK : les emails ne sont PAS marqués comme lus.
    - La clé « UIDVALIDITY:UID » identifie un email de façon stable dans un dossier.
    """
    typ, _ = imap.select(_guillemets(dossier), readonly=True)
    if typ != "OK":
        raise RuntimeError(f"Dossier IMAP introuvable : {dossier}")
    uidvalidity = (imap.response("UIDVALIDITY")[1] or [b"0"])[0].decode()

    typ, data = imap.uid("SEARCH", None, critere_recherche(expediteurs, jours))
    if typ != "OK":
        raise RuntimeError("La recherche IMAP a échoué")

    for uid in data[0].split():
        cle = f"{dossier}:{uidvalidity}:{uid.decode()}"
        if deja_traite and deja_traite(cle):
            continue
        typ, contenu = imap.uid("FETCH", uid, "(BODY.PEEK[])")
        brut = next((c[1] for c in contenu if isinstance(c, tuple)), None)
        if typ != "OK" or brut is None:
            continue
        yield cle, email.message_from_bytes(brut, policy=policy.default)


def dossier_brouillons(imap: imaplib.IMAP4_SSL) -> str:
    """Trouve le dossier Brouillons quelle que soit la langue de Gmail
    ([Gmail]/Drafts en anglais, [Gmail]/Brouillons en français…)."""
    typ, lignes = imap.list()
    for ligne in lignes or []:
        ligne = ligne.decode(errors="replace")
        if "\\Drafts" in ligne:
            # Format : (\HasNoChildren \Drafts) "/" "[Gmail]/Brouillons"
            m = re.match(r'\((?P<flags>.*?)\) "(?P<sep>.*?)" (?P<nom>.+)', ligne)
            if m:
                return m.group("nom").strip()
    return '"[Gmail]/Drafts"'


def construire_email(expediteur: str, destinataire: str, objet: str, corps: str,
                     piece_jointe: Optional[Path], email_reponse: str = "",
                     nom_expediteur: str = "") -> EmailMessage:
    """Prépare l'email de candidature, avec le dossier PDF en pièce jointe.

    email_reponse (votre adresse perso, facultatif) :
      - en copie (Cc, visible par l'agence) : vous recevez aussi la candidature ;
      - « Répondre à » : les réponses de l'agence arrivent sur cette adresse.
    nom_expediteur (facultatif) : nom affiché à la place de l'adresse Gmail, ex. « Prénom Nom ».
    """
    msg = EmailMessage()
    msg["From"] = formataddr((nom_expediteur, expediteur)) if nom_expediteur else expediteur
    msg["To"] = destinataire
    if email_reponse:
        msg["Cc"] = email_reponse
        msg["Reply-To"] = email_reponse
    msg["Subject"] = objet
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid()
    msg.set_content(corps)

    if piece_jointe and piece_jointe.is_file():
        msg.add_attachment(
            piece_jointe.read_bytes(),
            maintype="application",
            subtype="pdf",
            filename=piece_jointe.name,
        )
    return msg


def creer_brouillon(imap: imaplib.IMAP4_SSL, expediteur: str, destinataire: str, objet: str,
                    corps: str, piece_jointe: Optional[Path], email_reponse: str = "",
                    nom_expediteur: str = "") -> None:
    """Dépose un brouillon (avec le PDF en pièce jointe) dans Gmail. N'envoie rien."""
    msg = construire_email(expediteur, destinataire, objet, corps, piece_jointe, email_reponse,
                           nom_expediteur)
    dossier = _guillemets(dossier_brouillons(imap))
    typ, reponse = imap.append(dossier, "(\\Draft)", imaplib.Time2Internaldate(time.time()), msg.as_bytes())
    if typ != "OK":
        raise RuntimeError(f"Création du brouillon refusée : {reponse}")


def envoyer_email(adresse: str, mot_de_passe: str, destinataire: str, objet: str,
                  corps: str, piece_jointe: Path, email_reponse: str = "",
                  nom_expediteur: str = "") -> None:
    """Envoie la candidature (appelée uniquement après un appui sur « ✅ Postuler »)."""
    msg = construire_email(adresse, destinataire, objet, corps, piece_jointe, email_reponse,
                           nom_expediteur)
    with smtplib.SMTP_SSL(SERVEUR_SMTP, 465, timeout=30) as smtp:
        smtp.login(adresse, mot_de_passe)
        smtp.send_message(msg)


def tester_envoi(adresse: str, mot_de_passe: str) -> None:
    """Vérifie que la connexion d'envoi fonctionne, sans rien envoyer."""
    with smtplib.SMTP_SSL(SERVEUR_SMTP, 465, timeout=30) as smtp:
        smtp.login(adresse, mot_de_passe)
