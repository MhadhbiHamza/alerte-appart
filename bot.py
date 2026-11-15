"""Tout ce qui se passe dans Telegram : cartes d'annonces, boutons, réponses et commandes.

Séparé de main.py (qui garde la boucle et la lecture des emails) pour rester lisible.
"""
import hashlib
import json
import logging
import re
import time
from datetime import datetime

import analyse
import extraction
import gmail
import modele
import suivi
import telegram
from config import Config
from stockage import Stockage, maintenant

log = logging.getLogger("alerte-appart")


# --------------------------------------------------------------------------
# Étapes 4-5 : message de candidature et notification Telegram avec boutons
# --------------------------------------------------------------------------

def rapport_agent(annonce) -> dict:
    try:
        return json.loads(annonce["agent_json"] or "{}")
    except ValueError:
        return {}


def preparer_candidature(cfg: Config, annonce):
    """Renvoie (objet, message) remplis à partir de templates.md.

    Si un agent a écrit une accroche propre à l'annonce, elle remplace {accroche} dans le modèle,
    ou se place juste après la première ligne (« Bonjour, »).
    """
    valeurs = modele.valeurs_annonce(annonce)
    valeurs["mon_email"] = cfg.email_reponse or cfg.gmail_adresse  # pour votre signature
    accroche = rapport_agent(annonce).get("accroche", "").strip()
    valeurs["accroche"] = accroche
    objet, message = modele.charger(cfg.fichier_modele, colocation=bool(annonce["colocation"]))
    if accroche and "{accroche}" not in message:
        premiere, _, reste = message.partition("\n")
        message = f"{premiere}\n\n{accroche}\n{reste}"
    objet = ("[ESSAI] " if annonce["essai"] else "") + modele.remplir(objet, valeurs)
    return objet, re.sub(r"\n{3,}", "\n\n", modele.remplir(message, valeurs))


def destinataire(cfg: Config, annonce) -> str:
    """À qui envoyer la candidature : l'annonceur, ou VOUS-MÊME pour une annonce d'essai."""
    if annonce["essai"]:
        return cfg.email_reponse or cfg.gmail_adresse
    return annonce["email_contact"] or ""


def jeton(annonce) -> str:
    """Signature propre à une annonce, ajoutée à ses boutons.

    Si annonces.db est supprimée, les numéros repartent de 1 : sans cette signature, un bouton
    d'un ANCIEN message pourrait agir sur une NOUVELLE annonce portant le même numéro.
    """
    return hashlib.sha1(f"{annonce['empreinte']}|{annonce['vue_le']}".encode()).hexdigest()[:8]


def donnee_bouton(annonce, action: str) -> str:
    return f"{action}:{annonce['id']}:{jeton(annonce)}"


def boutons_annonce(annonce, details: bool = False) -> telegram.Boutons:
    """Clavier sous chaque annonce. `details` : la carte affiche-t-elle déjà le détail ?"""
    d = lambda action: donnee_bouton(annonce, action)  # noqa: E731
    lignes = [[("✅ Postuler (envoyer le dossier)", d("postuler"))],
              [("🔼 Réduire", d("reduire")) if details else ("📊 Détails", d("details")),
               ("📋 Message", d("message"))]]
    if annonce["email_contact"] or annonce["essai"]:
        lignes.append([("📝 Brouillon Gmail", d("brouillon")), ("❌ Pas intéressé", d("refuser"))])
    else:  # pas d'email dans l'alerte : « Postuler » vous demandera l'email de l'agence
        lignes.append([("🌐 Déjà postulé sur le site", d("site")), ("❌ Pas intéressé", d("refuser"))])
    if (annonce["lien"] or "").startswith("http"):
        lignes.append([("🔗 Ouvrir l'annonce", annonce["lien"])])
    return lignes


def analyser(cfg: Config, db: Stockage, annonce) -> analyse.Analyse:
    return analyse.analyser(
        annonce, db.prix_m2_zone(annonce["ville"], bool(annonce["colocation"]), annonce["id"]),
        prioritaire=annonce["ville"] in cfg.priorites, revenu_net=cfg.revenu_net_mensuel or None,
        prix_max=cfg.prix_max_coloc if annonce["colocation"] else cfg.prix_max,
        trajet_ok=(annonce["source"] or "").lower() in cfg.sources_toutes_zones)


def _entete(cfg: Config, annonce, resume: analyse.Analyse) -> list:
    """Lignes communes aux deux cartes : bandeaux, verdict, lieu / surface / prix."""
    e = telegram.echapper
    v = modele.valeurs_annonce(annonce)
    lignes = []
    if annonce["essai"]:
        lignes.append(f"🧪 <b>ESSAI</b> — « Postuler » et « Brouillon » envoient à "
                      f"<b>{e(destinataire(cfg, annonce))}</b>, jamais à l'annonceur.")
    if resume.alertes_arnaque:
        lignes.append("🚩 <b>Arnaque possible</b> : " + e(", ".join(resume.alertes_arnaque)))
    if annonce["ville"] in cfg.priorites:
        lignes.append("⭐ <b>ZONE PRIORITAIRE</b>")
    type_ = "👥 Colocation" if annonce["colocation"] else "🏠 Location"
    lignes += [
        f"<b>{resume.verdict} · {resume.note_globale:g}/5</b> — {type_} · {e(v['source'])}".replace(".", ","),
        f"📍 <b>{e(v['ville'])}</b> · {e(v['surface'])} m² · <b>{e(v['prix'])} € CC</b>",
    ]
    if v["quartier"]:
        lignes.append(f"📌 Quartier : {e(v['quartier'])}")
    return lignes


def carte_compacte(cfg: Config, annonce, resume: analyse.Analyse) -> str:
    """L'essentiel en 6 lignes : verdict, lieu, prix, 3 points clés. Le reste est derrière « 📊 Détails »."""
    e = telegram.echapper
    lignes = _entete(cfg, annonce, resume)
    icones = {"Emplacement": "📍", "Prix": "💶", "Logement": "🏢", "Frais": "🧾", "Transports": "🚇"}
    notes = " · ".join(f"{icones.get(critere, critere)}{n}" for critere, n in resume.notes.items() if n)
    if notes:  # ex. « 📍5 · 💶3 · 🏢4 · 🧾3 · 🚇5 » (notes sur 5, détail avec « 📊 Détails »)
        lignes.append(notes + " <i>/5</i>")
    for point in resume.pour[:2]:
        lignes.append(f"✅ {e(point)}")
    for point in resume.contre[:2]:
        lignes.append(f"⚠️ {e(point)}")
    if annonce["email_contact"]:
        lignes.append(f"✉️ {e(annonce['email_contact'])}")
    if annonce["telephone"]:
        lignes.append(f"📞 {e(annonce['telephone'])}")
    return "\n".join(lignes)


def carte_detaillee(cfg: Config, annonce, resume: analyse.Analyse) -> str:
    """Toute l'analyse : notes en étoiles, prix au m², frais, budget, logement, transports…"""
    e = telegram.echapper
    lignes = _entete(cfg, annonce, resume)
    for critere, note in resume.notes.items():
        precision = f" <i>({resume.repere_prix})</i>" if critere == "Prix" and resume.repere_prix else ""
        lignes.append(f"<code>{critere:<12}</code>{analyse.etoiles(note)} {note}/5{precision}" if note
                      else f"<code>{critere:<12}</code>– (pas assez de données)")
    lignes.append("")
    lignes += [e(l) for l in resume.lignes]
    if resume.pour:
        lignes.append("✅ <b>Pour</b> : " + e(", ".join(resume.pour)))
    if resume.contre:
        lignes.append("⚠️ <b>Contre</b> : " + e(", ".join(resume.contre)))
    if annonce["email_contact"]:
        lignes.append(f"✉️ {e(annonce['email_contact'])}")
    if annonce["telephone"]:
        lignes.append(f"📞 {e(annonce['telephone'])}")
    if annonce["email_contact"] or annonce["essai"]:
        if cfg.dossier_pdf.is_file():
            lignes.append(f"\n<i>« Postuler » envoie tout de suite votre message et votre dossier PDF à "
                          f"{e(destinataire(cfg, annonce))}.</i>")
        else:
            lignes.append(f"\n⚠️ Dossier PDF introuvable ({e(cfg.dossier_pdf.name)}) : « Postuler » "
                          "n'enverra rien tant qu'il manque.")
    else:
        lignes.append("\n<i>Pas d'email dans l'alerte : « Postuler » vous demandera l'email de l'agence, "
                      "puis enverra votre dossier.</i>")
    texte = "\n".join(lignes)
    return texte[:telegram.LIMITE - 10]


def message_telegram(cfg: Config, annonce, message: str, resume: analyse.Analyse) -> str:
    """Carte détaillée suivie du message de candidature (utilisé par les rappels et les tests)."""
    entete = carte_detaillee(cfg, annonce, resume) + "\n\n<b>Message prêt à copier</b> :\n"
    place = telegram.LIMITE - len(entete) - 30
    corps = telegram.echapper(message)
    return entete + f"<pre>{corps[:place] + '…' if len(corps) > place else corps}</pre>"


def photos_annonce(annonce) -> list:
    try:
        photos = json.loads(annonce["photos"] or "[]")
    except ValueError:
        photos = []
    return photos or ([annonce["photo"]] if annonce["photo"] else [])


def envoyer_carte(cfg: Config, db: Stockage, annonce, resume: analyse.Analyse, silencieux: bool = False) -> int:
    """Envoie l'album photos puis la carte compacte avec ses boutons. Renvoie le n° du message."""
    photos, album_ok, album_ids = photos_annonce(annonce), False, []
    if photos:
        try:
            album_ids = telegram.envoyer_photos(
                cfg.telegram_token, cfg.telegram_chat_id, photos,
                f"{resume.verdict} {resume.note_globale:g}/5 · {annonce['ville']} · {annonce['prix_cc']} € CC",
                silencieux=silencieux)
            album_ok = True
        except RuntimeError as exc:  # Telegram n'arrive pas à lire les photos : on continue sans
            log.warning("Photos non affichables par Telegram (%s)", exc)
    # Sans album, Telegram affiche l'aperçu de la page de l'annonce.
    message_id = telegram.envoyer(cfg.telegram_token, cfg.telegram_chat_id, carte_compacte(cfg, annonce, resume),
                                  boutons=boutons_annonce(annonce), apercu=None if album_ok else annonce["lien"],
                                  silencieux=silencieux)
    db.maj(annonce["id"], message_id=message_id, album_ids=json.dumps(album_ids), differee=0)
    return message_id


def notifier_annonces(cfg: Config, db: Stockage) -> None:
    """🟢 avec son, 🟡 en silence, 🔴 mises de côté pour le résumé du soir (réglage ROUGES)."""
    for annonce in db.a_notifier():
        resume = analyser(cfg, db, annonce)
        rouge = resume.verdict.startswith("🔴")
        db.maj(annonce["id"], notifiee_le=maintenant(), verdict=resume.verdict, note=resume.note_globale)
        if rouge and cfg.rouges in ("soir", "jamais") and not annonce["essai"]:
            db.maj(annonce["id"], differee=1)
            log.info("Annonce #%d mise de côté (%s %s/5)", annonce["id"], resume.verdict, resume.note_globale)
            continue
        envoyer_carte(cfg, db, db.annonce(annonce["id"]), resume, silencieux=not resume.verdict.startswith("🟢"))
        mettre_en_file_agent(cfg, db, db.annonce(annonce["id"]), resume)
        log.info("Notification envoyée pour l'annonce #%d (%s)", annonce["id"], resume.verdict)


NIVEAUX = {"🟢": "vert", "🟡": "jaune", "🔴": "rouge"}


def mettre_en_file_agent(cfg: Config, db: Stockage, annonce, resume) -> None:
    """Confie l'annonce aux agents si elle est du bon niveau (AGENTS_NIVEAUX) et pas déjà traitée."""
    if not cfg.agents_actifs or annonce["agent_etat"]:
        return
    if annonce["essai"] or NIVEAUX.get(resume.verdict[:1], "") in cfg.agents_niveaux:
        db.maj(annonce["id"], agent_etat="en_attente")


# --------------------------------------------------------------------------
# Étape 6 : vos réponses avec les boutons Telegram
# --------------------------------------------------------------------------

def traiter_boutons(cfg: Config, db: Stockage, attente: int) -> None:
    """Attend (au plus `attente` s) vos appuis sur les boutons / vos réponses, et les applique."""
    offset = int(db.lire_etat("telegram_offset", "0"))
    for maj in telegram.mises_a_jour(cfg.telegram_token, offset, attente):
        # On note d'abord la position : un appui n'est jamais traité deux fois.
        db.ecrire_etat("telegram_offset", maj["update_id"] + 1)
        clic, message = maj.get("callback_query"), maj.get("message")
        try:
            if clic:
                traiter_clic(cfg, db, clic)
            elif message:
                traiter_message(cfg, db, message)
        except Exception as exc:
            log.exception("Erreur en traitant un bouton ou un message Telegram")
            if clic:
                telegram.repondre_clic(cfg.telegram_token, clic["id"], f"⚠️ Erreur : {exc}"[:200], alerte=True)
            else:
                telegram.envoyer(cfg.telegram_token, cfg.telegram_chat_id,
                                 telegram.echapper(f"⚠️ Erreur : {exc}"[:500]))


def traiter_clic(cfg: Config, db: Stockage, clic: dict) -> None:
    token = cfg.telegram_token
    chat = str(clic.get("message", {}).get("chat", {}).get("id"))
    if chat != str(cfg.telegram_chat_id):  # sécurité : seuls VOS appuis comptent
        telegram.repondre_clic(token, clic["id"], "Non autorisé.")
        return
    action, id_texte, signature = (clic.get("data", "").split(":") + ["", ""])[:3]
    if action == "rien" or not id_texte.isdigit():
        telegram.repondre_clic(token, clic["id"])
        return
    annonce = db.annonce(int(id_texte))
    if annonce is None or signature != jeton(annonce):
        telegram.repondre_clic(token, clic["id"], "Bouton périmé (ancien message) : rien n'a été fait. "
                               "Utilisez la notification la plus récente.", alerte=True)
        return
    message_clique = clic.get("message", {}).get("message_id")

    # --- Affichage : possibles à tout moment, même après l'envoi du dossier ---
    if action in ("details", "reduire"):
        resume = analyser(cfg, db, annonce)
        details = action == "details"
        texte = carte_detaillee(cfg, annonce, resume) if details else carte_compacte(cfg, annonce, resume)
        clavier = boutons_annonce(annonce, details=details)
        if annonce["envoye_le"] or annonce["statut"]:
            clavier = [[("🔼 Réduire", donnee_bouton(annonce, "reduire")) if details
                        else ("📊 Détails", donnee_bouton(annonce, "details"))]]
        telegram.modifier_texte(token, cfg.telegram_chat_id, message_clique, texte, clavier)
        telegram.repondre_clic(token, clic["id"])
        return
    if action == "script":
        script = rapport_agent(annonce).get("script_appel") or "Pas de script pour cette annonce."
        telegram.envoyer(token, cfg.telegram_chat_id,
                         f"📞 <b>Script d'appel</b> ({telegram.echapper(annonce['ville'] or '')}) :\n"
                         f"<pre>{telegram.echapper(script)}</pre>\n<i>[PRÉSENTATION] : votre situation "
                         "(CDI, revenus, garant), comme dans votre message.</i>", silencieux=True)
        telegram.repondre_clic(token, clic["id"])
        return
    if action == "message":
        _, message = preparer_candidature(cfg, annonce)
        telegram.envoyer(token, cfg.telegram_chat_id,
                         f"📋 Message pour {telegram.echapper(annonce['ville'] or '')} · {annonce['prix_cc']} € "
                         f"(appuyez dessus pour le copier) :\n<pre>{telegram.echapper(message)[:3800]}</pre>",
                         silencieux=True)
        telegram.repondre_clic(token, clic["id"])
        return
    if action == "afficher":  # depuis le résumé du soir : la 🔴 mise de côté
        resume = analyser(cfg, db, annonce)
        envoyer_carte(cfg, db, annonce, resume, silencieux=True)
        mettre_en_file_agent(cfg, db, db.annonce(annonce["id"]), resume)
        telegram.repondre_clic(token, clic["id"], "Annonce affichée.")
        return

    if annonce["envoye_le"]:  # protection contre le double envoi
        telegram.repondre_clic(token, clic["id"], f"Dossier déjà envoyé le {_date(annonce['envoye_le'])}.")
        return

    # --- Annonce suspecte : « Postuler » demande un 2e appui dans les 60 s ---
    if action in ("postuler", "valider") and not annonce["essai"]:
        alertes = analyser(cfg, db, annonce).alertes_arnaque
        cle = f"confirmation:{annonce['id']}"
        if alertes and time.time() - float(db.lire_etat(cle, "0") or 0) > 60:
            db.ecrire_etat(cle, f"{time.time():.0f}")
            telegram.repondre_clic(token, clic["id"], "🚩 Annonce suspecte : " + ", ".join(alertes)
                                   + ". Appuyez encore sur Postuler dans les 60 s pour confirmer.", alerte=True)
            return

    if action == "valider":  # l'agent a trouvé un email : vous validez, le dossier part
        email = rapport_agent(annonce).get("contact", {}).get("email", "")
        if not email and not annonce["essai"]:
            telegram.repondre_clic(token, clic["id"], "Pas d'email trouvé par l'agent.")
            return
        if email:
            db.maj(annonce["id"], email_contact=email)
        ok, reponse = envoyer_dossier(cfg, db, db.annonce(annonce["id"]),
                                      [message_clique, annonce["message_id"]])
        telegram.repondre_clic(token, clic["id"], reponse, alerte=not ok)
        return

    if action == "appele":
        _enregistrer(cfg, db, annonce, "appelé", "appel téléphonique")
        _remplacer_boutons(cfg, [message_clique, annonce["message_id"]], "📞 Appelé")
        telegram.repondre_clic(token, clic["id"], "Noté : vous avez appelé.")
        return

    if action == "refuser":
        _enregistrer(cfg, db, annonce, "pas intéressé", "")
        supprimees = _supprimer_messages(cfg, annonce, message_clique)
        if not supprimees:  # trop ancien pour être supprimé (> 48 h) : on remplace les boutons
            _remplacer_boutons(cfg, [message_clique], "❌ Pas intéressé")
        telegram.repondre_clic(token, clic["id"], "Annonce supprimée." if supprimees else "Annonce classée.")

    elif action == "site":
        _enregistrer(cfg, db, annonce, "postulé", "sur le site")
        _remplacer_boutons(cfg, [message_clique], "🌐 Postulé sur le site")
        telegram.repondre_clic(token, clic["id"], "Noté : candidature faite sur le site.")

    elif action == "brouillon":
        if not destinataire(cfg, annonce):
            telegram.repondre_clic(token, clic["id"], "Pas d'email pour cette annonce.")
            return
        objet, message = preparer_candidature(cfg, annonce)
        pdf = cfg.dossier_pdf if cfg.dossier_pdf.is_file() else None
        imap = gmail.connexion(cfg.gmail_adresse, cfg.gmail_mot_de_passe)
        try:
            gmail.creer_brouillon(imap, cfg.gmail_adresse, destinataire(cfg, annonce), objet, message, pdf,
                                  cfg.email_reponse, cfg.nom_expediteur)
        finally:
            imap.logout()
        _enregistrer(cfg, db, annonce, "brouillon créé", "brouillon Gmail")
        _remplacer_boutons(cfg, [message_clique], "📝 Brouillon créé dans Gmail")
        telegram.repondre_clic(token, clic["id"], "Brouillon créé : relisez-le dans Gmail puis envoyez-le.")

    elif action == "postuler" and destinataire(cfg, annonce):
        ok, reponse = envoyer_dossier(cfg, db, annonce, [message_clique])
        telegram.repondre_clic(token, clic["id"], reponse, alerte=not ok)

    elif action == "postuler":
        # Pas d'email dans l'alerte : on vous le demande. Votre réponse déclenche l'envoi.
        demande = telegram.demander_reponse(
            token, cfg.telegram_chat_id,
            f"✉️ <b>Email de l'agence ?</b>\n{telegram.echapper(annonce['ville'] or '')} · "
            f"{annonce['prix_cc']} € CC\nRépondez à ce message avec l'email de l'annonceur "
            "(visible sur l'annonce) : votre dossier partira aussitôt.",
            "agence@exemple.fr")
        db.ecrire_etat(f"demande_email:{demande}", annonce["id"])
        db.ecrire_etat("derniere_demande_email", f"{annonce['id']}:{demande}:{time.time():.0f}")
        telegram.repondre_clic(token, clic["id"], "Répondez avec l'email de l'agence.")


COMMANDES = [
    ("pause", "Arrêter la lecture des alertes"),
    ("reprise", "Reprendre la lecture des alertes"),
    ("stats", "Chiffres des 7 derniers jours et au total"),
    ("suivi", "Candidatures en cours"),
    ("csv", "Recevoir le fichier de suivi (Excel)"),
    ("aide", "Liste des commandes et des boutons"),
]

AIDE = """🤖 <b>Commandes</b>
/pause — arrêter la lecture des alertes (les emails attendront)
/reprise — reprendre
/stats — chiffres des 7 derniers jours et au total
/suivi — candidatures en cours
/csv — recevoir le fichier de suivi (Excel)

<b>Boutons</b> : ✅ Postuler envoie votre dossier (ou vous demande l'email de l'agence) ·
📊 Détails / 🔼 Réduire · 📋 Message à copier · 📝 Brouillon · 🌐 Déjà postulé ·
❌ Pas intéressé (supprime l'annonce) · 🔗 Ouvrir l'annonce.
🟢 arrivent avec son, 🟡 en silence, 🔴 dans le résumé du soir."""


def traiter_commande(cfg: Config, db: Stockage, texte: str) -> None:
    import resumes  # import ici pour éviter une boucle d'imports

    commande = texte.split()[0].split("@")[0].lower().lstrip("/")
    if commande == "pause":
        db.ecrire_etat("pause", "1")
        reponse = "⏸ En pause : je ne lis plus les alertes. Les boutons marchent toujours. /reprise pour relancer."
    elif commande == "reprise":
        db.ecrire_etat("pause", "")
        reponse = "▶️ C'est reparti : je lis les alertes reçues pendant la pause."
    elif commande == "stats":
        reponse = resumes.texte_stats(db, datetime.now(), cfg)
    elif commande == "suivi":
        reponse = resumes.texte_suivi(db)
    elif commande == "csv":
        if not cfg.fichier_csv.exists():
            reponse = "Le fichier de suivi n'existe pas encore (aucune annonce notifiée)."
        else:
            telegram.envoyer_fichier(cfg.telegram_token, cfg.telegram_chat_id, cfg.fichier_csv,
                                     "📁 Fichier de suivi (à ouvrir avec Excel)")
            return
    else:  # /aide, /start, ou commande inconnue
        reponse = AIDE
    telegram.envoyer(cfg.telegram_token, cfg.telegram_chat_id, reponse)


def traiter_message(cfg: Config, db: Stockage, message: dict) -> None:
    """Vos commandes (/pause, /stats…) et votre réponse à « Email de l'agence ? »."""
    if str(message.get("chat", {}).get("id")) != str(cfg.telegram_chat_id):
        return  # message d'un inconnu : ignoré
    if (message.get("text") or "").startswith("/"):
        traiter_commande(cfg, db, message["text"])
        return
    repond_a = (message.get("reply_to_message") or {}).get("message_id")
    id_texte = db.lire_etat(f"demande_email:{repond_a}") if repond_a else ""
    if not id_texte:
        # Réponse tapée sans « répondre » : on prend la dernière demande, si elle date de moins de 2 h.
        derniere = db.lire_etat("derniere_demande_email").split(":")
        if len(derniere) == 3 and time.time() - float(derniere[2]) < 7200:
            id_texte, repond_a = derniere[0], int(derniere[1])
    if not id_texte:
        return  # message sans rapport avec une candidature
    adresse = extraction.EMAIL_RE.search(message.get("text") or "")
    if not adresse:
        telegram.envoyer(cfg.telegram_token, cfg.telegram_chat_id,
                         "Je n'ai pas trouvé d'adresse email dans votre réponse. Répondez avec l'email "
                         "de l'agence, par exemple agence@exemple.fr.")
        return
    annonce = db.annonce(int(id_texte))
    if annonce is None:
        return
    if annonce["envoye_le"]:
        telegram.envoyer(cfg.telegram_token, cfg.telegram_chat_id,
                         f"Dossier déjà envoyé le {_date(annonce['envoye_le'])} pour cette annonce.")
        return
    db.maj(annonce["id"], email_contact=adresse.group(0).lower())
    _, reponse = envoyer_dossier(cfg, db, db.annonce(annonce["id"]), [annonce["message_id"]])
    db.ecrire_etat(f"demande_email:{repond_a}", "")
    db.ecrire_etat("derniere_demande_email", "")
    telegram.envoyer(cfg.telegram_token, cfg.telegram_chat_id, telegram.echapper(reponse))


def envoyer_dossier(cfg: Config, db: Stockage, annonce, messages: list):
    """Envoie la candidature + le PDF. Renvoie (réussi ?, texte de confirmation)."""
    if not cfg.dossier_pdf.is_file():
        return False, f"⚠️ Dossier PDF introuvable ({cfg.dossier_pdf.name}) : rien n'a été envoyé."
    objet, message = preparer_candidature(cfg, annonce)
    a_qui = destinataire(cfg, annonce)
    gmail.envoyer_email(cfg.gmail_adresse, cfg.gmail_mot_de_passe, a_qui,
                        objet, message, cfg.dossier_pdf, cfg.email_reponse, cfg.nom_expediteur)
    quand = maintenant()
    db.maj(annonce["id"], envoye_le=quand)
    essai = " (essai)" if annonce["essai"] else ""
    log.info("📨 Dossier envoyé à %s (annonce #%d%s)", a_qui, annonce["id"], essai)
    _enregistrer(cfg, db, annonce, "dossier envoyé" + essai, f"email envoyé le {_date(quand)} à {a_qui}")
    _remplacer_boutons(cfg, messages, f"✅ Dossier envoyé le {_date(quand)}{essai}")
    return True, f"Dossier envoyé à {a_qui} ✅{essai}"


def _enregistrer(cfg: Config, db: Stockage, annonce, statut: str, candidature: str) -> None:
    """Enregistre la décision dans la base et dans suivi.csv."""
    db.maj(annonce["id"], statut=statut, candidature=candidature)
    if annonce["csv_ecrit"]:
        try:
            suivi.maj(cfg.fichier_csv, annonce["id"], statut=statut, candidature=candidature)
        except PermissionError:
            log.warning("suivi.csv est verrouillé (ouvert dans Excel ?) : statut non mis à jour dans le CSV")


def _remplacer_boutons(cfg: Config, messages: list, texte_bouton: str) -> None:
    for message_id in dict.fromkeys(m for m in messages if m):
        try:
            telegram.modifier_boutons(cfg.telegram_token, cfg.telegram_chat_id, message_id,
                                      [[(texte_bouton, "rien")]])
        except RuntimeError as exc:  # message supprimé entre-temps, etc.
            log.warning("Boutons non modifiés (%s)", exc)


def _supprimer_messages(cfg: Config, annonce, message_clique) -> bool:
    """Supprime de Telegram le résumé, l'album photos et le message cliqué. True si réussi."""
    try:
        album = json.loads(annonce["album_ids"] or "[]")
    except ValueError:
        album = []
    a_supprimer = dict.fromkeys(m for m in [message_clique, annonce["message_id"], annonce["agent_message_id"],
                                            *album] if m)
    reussi = False
    for message_id in a_supprimer:
        try:
            telegram.supprimer_message(cfg.telegram_token, cfg.telegram_chat_id, message_id)
            reussi = reussi or message_id == message_clique
        except RuntimeError as exc:  # Telegram refuse au-delà de 48 h
            log.warning("Message %s non supprimé (%s)", message_id, exc)
    return reussi


def _date(iso: str) -> str:
    return datetime.fromisoformat(iso).strftime("%d/%m à %H:%M")




# --------------------------------------------------------------------------
# Rapports des agents
# --------------------------------------------------------------------------

def texte_rapport(cfg: Config, annonce, r: dict) -> str:
    e = telegram.echapper
    ev, contact = r.get("evaluation", {}), r.get("contact", {})
    reco = {"candidater": "✅ candidater", "a_verifier": "🔎 à vérifier", "passer": "⛔ passer"}
    lignes = [f"🤖 <b>Rapport de l'agent</b> — intérêt {ev.get('interet', '?')}/5 · "
              f"{reco.get(ev.get('recommandation'), '?')}"]
    if ev.get("synthese"):
        lignes.append(e(ev["synthese"]))
    if ev.get("points_forts"):
        lignes.append("👍 " + e(" · ".join(ev["points_forts"])))
    if ev.get("risques"):
        lignes.append("⚠️ " + e(" · ".join(ev["risques"])))
    if ev.get("questions_visite"):
        lignes.append("❓ À demander : " + e(" · ".join(ev["questions_visite"])))
    lignes.append("")
    if contact.get("email") or contact.get("telephone"):
        qui = f" ({e(contact['agence'])})" if contact.get("agence") else ""
        lignes.append(f"📇 <b>Contact trouvé</b>{qui} :")
        if contact.get("email"):
            lignes.append(f"✉️ {e(contact['email'])}")
        if contact.get("telephone"):
            lignes.append(f"📞 {e(contact['telephone'])}")
        if contact.get("source_url", "").startswith("http"):
            lignes.append(f'<a href="{e(contact["source_url"], True)}">source</a> · '
                          f"confiance {round(contact.get('confiance', 0) * 100)} %")
    else:
        lignes.append("📇 Aucun contact trouvé" + (f" ({e(contact['raison'])})" if contact.get("raison") else "."))
    canal = r.get("canal", "formulaire")
    libelle = {"email": "✉️ email", "appel": "📞 appel", "formulaire": "🌐 formulaire du site"}[canal]
    lignes.append(f"\n➡️ <b>Canal conseillé : {libelle}</b>" + (f" — {e(r['raison'])}" if r.get("raison") else ""))
    if canal == "email":
        lignes.append(f"<i>« Valider » envoie votre message (avec l'accroche de l'agent) et votre dossier à "
                      f"{e(destinataire(cfg, annonce) or contact.get('email', ''))}.</i>")
    return "\n".join(lignes)[:telegram.LIMITE]


def boutons_rapport(annonce, r: dict) -> telegram.Boutons:
    d = lambda action: donnee_bouton(annonce, action)  # noqa: E731
    canal, contact = r.get("canal"), r.get("contact", {})
    lien = [[("🔗 Ouvrir l'annonce", annonce["lien"])]] if (annonce["lien"] or "").startswith("http") else []
    if canal == "email":
        return [[(f"✅ Valider l'envoi à {contact.get('email', '')}"[:60], d("valider"))],
                [("👀 Voir le message", d("message")), ("❌ Pas intéressé", d("refuser"))]] + lien
    if canal == "appel":
        return [[("📞 Script d'appel", d("script")), ("✅ J'ai appelé", d("appele"))],
                [("📋 Message", d("message")), ("❌ Pas intéressé", d("refuser"))]] + lien
    return lien + [[("📋 Message", d("message")), ("🌐 Déjà postulé", d("site"))],
                   [("❌ Pas intéressé", d("refuser"))]]


def envoyer_rapports(cfg: Config, db: Stockage) -> None:
    """Envoie, en réponse à chaque carte, les rapports que les agents ont terminés."""
    for annonce in db.rapports_prets():
        r = rapport_agent(annonce)
        if annonce["statut"] or annonce["envoye_le"]:  # décision déjà prise entre-temps
            db.maj(annonce["id"], agent_etat="fini")
            continue
        message_id = telegram.envoyer(cfg.telegram_token, cfg.telegram_chat_id, texte_rapport(cfg, annonce, r),
                                      boutons=boutons_rapport(annonce, r), silencieux=True,
                                      repondre_a=annonce["message_id"])
        db.maj(annonce["id"], agent_etat="fini", agent_message_id=message_id)
