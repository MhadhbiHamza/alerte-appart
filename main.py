"""Alerte appartement Paris — programme principal.

Utilisation :
    python main.py                    # tourne en continu (emails toutes les 2 min, boutons en direct)
    python main.py --une-fois         # un seul passage puis s'arrête
    python main.py --test             # teste la connexion Gmail et Telegram
    python main.py --analyser         # montre ce qui est extrait des derniers emails (n'envoie rien)
    python main.py --trouver-chat-id  # affiche votre TELEGRAM_CHAT_ID

Sous chaque annonce, Telegram affiche des boutons :
    ✅ Postuler        -> envoie IMMÉDIATEMENT l'email de candidature + votre dossier PDF à l'agence
                          (ou, si l'annonce n'a pas d'email, note « postulé » dans le suivi)
    📝 Brouillon Gmail -> prépare l'email dans vos brouillons, pour le relire avant de l'envoyer
    ❌ Pas intéressé   -> classe l'annonce (plus de rappel de relance)

Garanties :
  - aucun site n'est consulté (pas de scraping) : seuls vos emails sont lus ;
  - aucun email n'est envoyé sans que vous ayez appuyé sur « ✅ Postuler »,
    et jamais deux fois pour la même annonce.
"""
import argparse
import imaplib
import logging
import socket
import time
from datetime import datetime

import extraction
import gmail
import modele
import suivi
import telegram
from config import Config, charger_config, verifier
from stockage import Stockage, maintenant

log = logging.getLogger("alerte-appart")

# Erreurs « réseau » : Internet coupé, Wi-Fi perdu, veille, VPN… On les signale en une ligne
# (sans le long détail technique) et l'outil réessaie tout seul.
ERREURS_RESEAU = (OSError, socket.timeout, imaplib.IMAP4.abort)


class EtatReseau:
    """Affiche « connexion perdue » une seule fois, puis « connexion rétablie »."""

    def __init__(self):
        self.coupe = False

    def erreur(self, exc: Exception) -> None:
        if not self.coupe:
            log.warning("🔌 Connexion Internet perdue (%s). L'outil réessaie tout seul…", exc)
        self.coupe = True

    def ok(self) -> None:
        if self.coupe:
            log.info("✅ Connexion rétablie.")
        self.coupe = False


# --------------------------------------------------------------------------
# Étape 1-3 : lire les emails, extraire, filtrer, stocker
# --------------------------------------------------------------------------

def traiter_nouveaux_emails(cfg: Config, db: Stockage, imap) -> None:
    for cle, msg in gmail.lire_emails(imap, cfg.dossier_imap, cfg.expediteurs,
                                      cfg.jours_historique, db.email_deja_traite):
        annonces = extraction.extraire_annonces(msg, a_exclure={cfg.gmail_adresse.lower()})
        log.info("Email « %s » : %d annonce(s) trouvée(s)", msg.get("Subject", ""), len(annonces))
        for a in annonces:
            if db.deja_vue(a):
                log.info("  doublon ignoré : %s", a.lien[:80])
                continue
            retenue, raison = extraction.est_eligible(a, cfg.prix_max, cfg.zones, cfg.prix_max_coloc)
            db.ajouter(a, retenue, raison)
            log.info("  %s %s%s € / %s m² / %s -> %s", "✅" if retenue else "❌",
                     "coloc " if a.colocation else "", a.prix_cc, a.surface, a.ville, raison)
        db.marquer_email_traite(cle)


# --------------------------------------------------------------------------
# Étapes 4-5 : message de candidature et notification Telegram avec boutons
# --------------------------------------------------------------------------

def preparer_candidature(cfg: Config, annonce):
    """Renvoie (objet, message) remplis à partir de templates.md."""
    valeurs = modele.valeurs_annonce(annonce)
    valeurs["mon_email"] = cfg.email_reponse or cfg.gmail_adresse  # pour votre signature
    objet, message = modele.charger(cfg.fichier_modele, colocation=bool(annonce["colocation"]))
    return modele.remplir(objet, valeurs), modele.remplir(message, valeurs)


def boutons_annonce(annonce) -> telegram.Boutons:
    id_ = annonce["id"]
    if annonce["email_contact"]:
        return [[("✅ Postuler (envoyer le dossier)", f"postuler:{id_}")],
                [("📝 Brouillon Gmail", f"brouillon:{id_}"), ("❌ Pas intéressé", f"refuser:{id_}")]]
    return [[("✅ J'ai postulé sur le site", f"postuler:{id_}"), ("❌ Pas intéressé", f"refuser:{id_}")]]


def message_telegram(cfg: Config, annonce, message: str) -> str:
    e = telegram.echapper
    v = modele.valeurs_annonce(annonce)
    lignes = []
    if annonce["ville"] in cfg.priorites:
        lignes.append("⭐ <b>ZONE PRIORITAIRE</b>")
    type_ = "👥 Colocation" if annonce["colocation"] else "🏠 Nouvelle annonce"
    lignes += [
        f"<b>{type_}</b> ({e(v['source'])})",
        f"📍 {e(v['ville'])} · {e(v['surface'])} m² · <b>{e(v['prix'])} € CC</b>",
    ]
    if v["quartier"]:
        lignes.append(f"📌 Quartier repéré : {e(v['quartier'])}")
    if annonce["charges_incertaines"]:
        lignes.append("⚠️ Prix indiqué hors charges, montant des charges inconnu : à vérifier.")
    lignes.append(f'🔗 <a href="{e(annonce["lien"], True)}">Voir l\'annonce</a>')
    if annonce["email_contact"]:
        lignes.append(f"✉️ {e(annonce['email_contact'])}")
    if annonce["telephone"]:
        lignes.append(f"📞 {e(annonce['telephone'])}")
    if annonce["email_contact"]:
        if cfg.dossier_pdf.is_file():
            lignes.append("\n<i>« Postuler » envoie tout de suite le message ci-dessous et votre dossier PDF "
                          "à cette adresse.</i>")
        else:
            lignes.append(f"\n⚠️ Dossier PDF introuvable ({e(cfg.dossier_pdf.name)}) : « Postuler » "
                          "n'enverra rien tant qu'il manque.")
    else:
        lignes.append("\n<i>Pas d'email : copiez le message dans le formulaire de l'annonce.</i>")
    entete = "\n".join(lignes) + "\n\n<b>Message prêt à copier</b> (appuyez dessus pour le copier) :\n"

    # On coupe le message si besoin pour respecter la limite de Telegram.
    place = telegram.LIMITE - len(entete) - 30
    corps = e(message)
    if len(corps) > place:
        corps = corps[:place] + "…"
    return entete + f"<pre>{corps}</pre>"


def notifier_annonces(cfg: Config, db: Stockage) -> None:
    for annonce in db.a_notifier():
        _, message = preparer_candidature(cfg, annonce)
        message_id = telegram.envoyer(
            cfg.telegram_token, cfg.telegram_chat_id, message_telegram(cfg, annonce, message),
            boutons=boutons_annonce(annonce),
            # Aperçu : la photo de l'email d'alerte si on l'a, sinon l'annonce elle-même.
            apercu=annonce["photo"] or annonce["lien"],
        )
        db.maj(annonce["id"], notifiee_le=maintenant(), message_id=message_id)
        log.info("Notification envoyée pour l'annonce #%d", annonce["id"])


# --------------------------------------------------------------------------
# Étape 6 : vos réponses avec les boutons Telegram
# --------------------------------------------------------------------------

def traiter_boutons(cfg: Config, db: Stockage, attente: int) -> None:
    """Attend (au plus `attente` s) vos appuis sur les boutons et les applique."""
    offset = int(db.lire_etat("telegram_offset", "0"))
    for maj in telegram.mises_a_jour(cfg.telegram_token, offset, attente):
        # On note d'abord la position : un appui n'est jamais traité deux fois.
        db.ecrire_etat("telegram_offset", maj["update_id"] + 1)
        clic = maj.get("callback_query")
        if not clic:
            continue
        try:
            traiter_clic(cfg, db, clic)
        except Exception as exc:
            log.exception("Erreur en traitant un bouton")
            telegram.repondre_clic(cfg.telegram_token, clic["id"], f"⚠️ Erreur : {exc}"[:200], alerte=True)


def traiter_clic(cfg: Config, db: Stockage, clic: dict) -> None:
    token = cfg.telegram_token
    chat = str(clic.get("message", {}).get("chat", {}).get("id"))
    if chat != str(cfg.telegram_chat_id):  # sécurité : seuls VOS appuis comptent
        telegram.repondre_clic(token, clic["id"], "Non autorisé.")
        return
    action, _, id_texte = clic.get("data", "").partition(":")
    if action == "rien" or not id_texte.isdigit():
        telegram.repondre_clic(token, clic["id"])
        return
    annonce = db.annonce(int(id_texte))
    if annonce is None:
        telegram.repondre_clic(token, clic["id"], "Annonce inconnue.")
        return
    if annonce["envoye_le"]:  # protection contre le double envoi
        telegram.repondre_clic(token, clic["id"], f"Dossier déjà envoyé le {_date(annonce['envoye_le'])}.")
        return

    if action == "refuser":
        _decision(cfg, db, annonce, clic, "pas intéressé", "", "❌ Pas intéressé", "Annonce classée.")

    elif action == "brouillon":
        if not annonce["email_contact"]:
            telegram.repondre_clic(token, clic["id"], "Pas d'email pour cette annonce.")
            return
        objet, message = preparer_candidature(cfg, annonce)
        pdf = cfg.dossier_pdf if cfg.dossier_pdf.is_file() else None
        imap = gmail.connexion(cfg.gmail_adresse, cfg.gmail_mot_de_passe)
        try:
            gmail.creer_brouillon(imap, cfg.gmail_adresse, annonce["email_contact"], objet, message, pdf,
                                  cfg.email_reponse)
        finally:
            imap.logout()
        _decision(cfg, db, annonce, clic, "brouillon créé", "brouillon Gmail",
                  "📝 Brouillon créé dans Gmail", "Brouillon créé : relisez-le dans Gmail puis envoyez-le.")

    elif action == "postuler" and annonce["email_contact"]:
        if not cfg.dossier_pdf.is_file():
            telegram.repondre_clic(token, clic["id"], f"⚠️ Dossier PDF introuvable ({cfg.dossier_pdf.name}) : "
                                   "rien n'a été envoyé.", alerte=True)
            return
        objet, message = preparer_candidature(cfg, annonce)
        gmail.envoyer_email(cfg.gmail_adresse, cfg.gmail_mot_de_passe, annonce["email_contact"],
                            objet, message, cfg.dossier_pdf, cfg.email_reponse)
        quand = maintenant()
        db.maj(annonce["id"], envoye_le=quand)
        log.info("📨 Dossier envoyé à %s (annonce #%d)", annonce["email_contact"], annonce["id"])
        _decision(cfg, db, annonce, clic, "dossier envoyé", f"email envoyé le {_date(quand)}",
                  f"✅ Dossier envoyé le {_date(quand)}", f"Dossier envoyé à {annonce['email_contact']} ✅")

    elif action == "postuler":
        _decision(cfg, db, annonce, clic, "postulé", "sur le site",
                  "✅ Postulé sur le site", "Noté : candidature faite sur le site.")


def _decision(cfg: Config, db: Stockage, annonce, clic: dict, statut: str, candidature: str,
              texte_bouton: str, reponse: str) -> None:
    """Enregistre la décision (base + suivi.csv) et remplace les boutons du message."""
    db.maj(annonce["id"], statut=statut, candidature=candidature)
    if annonce["csv_ecrit"]:
        try:
            suivi.maj(cfg.fichier_csv, annonce["id"], statut=statut, candidature=candidature)
        except PermissionError:
            log.warning("suivi.csv est verrouillé (ouvert dans Excel ?) : statut non mis à jour dans le CSV")
    message = clic.get("message", {})
    if message.get("message_id"):
        telegram.modifier_boutons(cfg.telegram_token, cfg.telegram_chat_id, message["message_id"],
                                  [[(texte_bouton, "rien")]])
    telegram.repondre_clic(cfg.telegram_token, clic["id"], reponse)


def _date(iso: str) -> str:
    return datetime.fromisoformat(iso).strftime("%d/%m à %H:%M")


# --------------------------------------------------------------------------
# Étape 7 : fichier de suivi CSV et rappels de relance
# --------------------------------------------------------------------------

def mettre_a_jour_csv(cfg: Config, db: Stockage) -> None:
    for a in db.a_ecrire_dans_csv():
        v = modele.valeurs_annonce(a)
        ligne = {
            "id": a["id"],
            "date": a["notifiee_le"].replace("T", " "),
            "source": a["source"],
            "annonce": ("⭐ " if a["ville"] in cfg.priorites else "")
                       + ("Coloc – " if a["colocation"] else "")
                       + f"{v['ville']} – {v['surface']} m² – {v['prix']} € CC",
            "lien": a["lien"],
            "contact": v["contact"],
            "candidature": a["candidature"] or "",
            "statut": a["statut"] or "nouveau",
            "relance_le": "",
        }
        try:
            suivi.ajouter(cfg.fichier_csv, ligne)
            db.marquer_csv_ecrit(a["id"])
        except PermissionError:
            log.warning("suivi.csv est verrouillé (ouvert dans Excel ?) : nouvel essai au prochain cycle")
            return


def envoyer_relances(cfg: Config, db: Stockage) -> None:
    for a in db.a_relancer(cfg.relance_heures):
        if not a["csv_ecrit"]:
            continue  # on attend que la ligne existe dans le CSV
        try:
            statut = suivi.statut(cfg.fichier_csv, a["id"])
            if suivi.est_termine(statut):
                db.marquer_relancee(a["id"])  # candidature close : pas de rappel
                continue
            v = modele.valeurs_annonce(a)
            e = telegram.echapper
            if a["envoye_le"]:
                quoi = f"Dossier envoyé le {_date(a['envoye_le'])} : relancez l'agence."
            elif a["statut"]:
                quoi = f"Statut : {e(a['statut'])}. Pensez à relancer."
            else:
                quoi = "Vous n'avez pas encore répondu à cette annonce."
            texte = (
                f"⏰ <b>Rappel</b> (annonce reçue il y a {cfg.relance_heures} h)\n"
                f"📍 {e(v['ville'])} · {e(v['surface'])} m² · {e(v['prix'])} € CC\n"
                f'🔗 <a href="{e(a["lien"], True)}">Voir l\'annonce</a>\n'
                + (f"Contact : {e(v['contact'])}\n" if v["contact"] else "")
                + quoi
            )
            # Sans décision, on remet les boutons pour pouvoir répondre depuis le rappel.
            boutons = None if (a["statut"] or a["envoye_le"]) else boutons_annonce(a)
            telegram.envoyer(cfg.telegram_token, cfg.telegram_chat_id, texte, boutons=boutons)
            suivi.noter_relance(cfg.fichier_csv, a["id"], datetime.now().strftime("%Y-%m-%d %H:%M"))
            db.marquer_relancee(a["id"])
        except PermissionError:
            log.warning("suivi.csv est verrouillé : relance reportée au prochain cycle")
            return


# --------------------------------------------------------------------------
# Boucle principale et commandes
# --------------------------------------------------------------------------

def cycle(cfg: Config, db: Stockage) -> None:
    imap = gmail.connexion(cfg.gmail_adresse, cfg.gmail_mot_de_passe)
    try:
        traiter_nouveaux_emails(cfg, db, imap)
    finally:
        try:
            imap.logout()
        except Exception:
            pass
    notifier_annonces(cfg, db)
    mettre_a_jour_csv(cfg, db)
    envoyer_relances(cfg, db)


def boucle(cfg: Config, db: Stockage, une_fois: bool) -> None:
    log.info("Démarrage. Critères : ≤ %d € CC (coloc ≤ %d €), %d zones (priorités : %s). Ctrl+C pour arrêter.",
             cfg.prix_max, cfg.prix_max_coloc, len(cfg.zones), ", ".join(sorted(cfg.priorites)))
    prochain_cycle = 0.0
    reseau = EtatReseau()
    while True:
        if time.time() >= prochain_cycle:
            try:
                cycle(cfg, db)
                reseau.ok()
            except ERREURS_RESEAU as exc:
                reseau.erreur(exc)
            except Exception:
                # Une coupure réseau ne doit pas arrêter l'outil : on réessaie au cycle suivant.
                log.exception("Erreur pendant le cycle, nouvel essai dans %d s", cfg.intervalle_secondes)
            prochain_cycle = time.time() + cfg.intervalle_secondes
        # Entre deux lectures d'emails, on écoute les boutons Telegram (réaction en ~1 s).
        attente = 0 if une_fois else int(min(25, max(1, prochain_cycle - time.time())))
        try:
            traiter_boutons(cfg, db, attente)
            reseau.ok()
        except ERREURS_RESEAU as exc:
            reseau.erreur(exc)
            if not une_fois:
                time.sleep(10)
        except Exception:
            log.exception("Erreur de lecture des boutons Telegram, nouvel essai dans 10 s")
            if not une_fois:
                time.sleep(10)
        if une_fois:
            return


def commande_test(cfg: Config) -> None:
    verifier(cfg, "gmail_adresse", "gmail_mot_de_passe", "telegram_token", "telegram_chat_id")
    imap = gmail.connexion(cfg.gmail_adresse, cfg.gmail_mot_de_passe)
    print("✅ Lecture Gmail OK. Dossier brouillons :", gmail.dossier_brouillons(imap))
    imap.logout()
    gmail.tester_envoi(cfg.gmail_adresse, cfg.gmail_mot_de_passe)
    print("✅ Envoi Gmail possible (rien n'a été envoyé).")
    if cfg.email_reponse:
        print(f"✅ Les agences répondront à {cfg.email_reponse} (copie de chaque candidature envoyée).")
    try:
        telegram.envoyer(cfg.telegram_token, cfg.telegram_chat_id, "✅ Test réussi : le bot fonctionne.")
        print("✅ Message Telegram envoyé : vérifiez votre téléphone.")
    except telegram.TelegramInjoignable as exc:
        print(f"❌ {exc}\n"
              "   Gmail fonctionne, donc Internet marche : c'est l'accès à Telegram qui est bloqué.\n"
              "   Causes habituelles : VPN actif, réseau d'entreprise / d'école / public qui bloque Telegram,\n"
              "   antivirus ou pare-feu. Essayez sans VPN, ou via le partage de connexion de votre téléphone.\n"
              "   Test rapide : ouvrez https://api.telegram.org dans votre navigateur.")
    except RuntimeError as exc:  # Telegram a répondu, mais refuse (token ou chat ID faux)
        print(f"❌ {exc}\n   Vérifiez TELEGRAM_BOT_TOKEN et TELEGRAM_CHAT_ID dans .env (voir README, étape 4).")
    modele.charger(cfg.fichier_modele)
    modele.charger(cfg.fichier_modele, colocation=True)
    print("✅ Modèle", cfg.fichier_modele.name, "lisible.")
    print("✅ Dossier PDF trouvé." if cfg.dossier_pdf.is_file()
          else f"⚠️  Dossier PDF introuvable : {cfg.dossier_pdf} (le bouton Postuler n'enverra rien)")


def commande_analyser(cfg: Config) -> None:
    """Lecture seule : affiche ce que l'outil comprend des emails récents."""
    verifier(cfg, "gmail_adresse", "gmail_mot_de_passe")
    imap = gmail.connexion(cfg.gmail_adresse, cfg.gmail_mot_de_passe)
    try:
        for _, msg in gmail.lire_emails(imap, cfg.dossier_imap, cfg.expediteurs, cfg.jours_historique):
            print(f"\n=== {msg.get('Date')} | {msg.get('From')} | {msg.get('Subject')}")
            for a in extraction.extraire_annonces(msg, a_exclure={cfg.gmail_adresse.lower()}):
                ok, raison = extraction.est_eligible(a, cfg.prix_max, cfg.zones, cfg.prix_max_coloc)
                print(f"  {'✅' if ok else '❌'} {'COLOC ' if a.colocation else ''}{a.prix_cc} € | "
                      f"{a.surface} m² | {a.ville} | {a.email_contact or a.telephone or '-'} | {raison}\n"
                      f"     {a.lien[:100]}\n     photo : {(a.photo or '-')[:100]}")
    finally:
        imap.logout()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%d/%m %H:%M:%S")
    ap = argparse.ArgumentParser(description="Alertes appartement Paris -> Telegram")
    ap.add_argument("--une-fois", action="store_true", help="un seul passage puis arrêt")
    ap.add_argument("--test", action="store_true", help="tester Gmail + Telegram")
    ap.add_argument("--analyser", action="store_true", help="afficher l'extraction des emails récents")
    ap.add_argument("--trouver-chat-id", action="store_true", help="afficher votre identifiant Telegram")
    args = ap.parse_args()
    cfg = charger_config()

    if args.trouver_chat_id:
        verifier(cfg, "telegram_token")
        chats = telegram.trouver_chat_id(cfg.telegram_token)
        if not chats:
            print("Aucun message trouvé. Envoyez d'abord « bonjour » à votre bot sur Telegram, puis relancez.")
        for chat_id, nom in chats:
            print(f"TELEGRAM_CHAT_ID={chat_id}   ({nom})")
        return
    if args.test:
        return commande_test(cfg)
    if args.analyser:
        return commande_analyser(cfg)

    verifier(cfg, "gmail_adresse", "gmail_mot_de_passe", "telegram_token", "telegram_chat_id")
    db = Stockage(cfg.fichier_db)
    try:
        boucle(cfg, db, args.une_fois)
    except KeyboardInterrupt:
        log.info("Arrêt demandé, à bientôt !")


if __name__ == "__main__":
    main()
