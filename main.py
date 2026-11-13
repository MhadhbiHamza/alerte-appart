"""Alerte appartement Paris — programme principal.

Utilisation :
    python main.py                    # tourne en continu (vérifie toutes les 2 min)
    python main.py --une-fois         # un seul passage puis s'arrête
    python main.py --test             # teste la connexion Gmail et Telegram
    python main.py --analyser         # montre ce qui est extrait des derniers emails (n'envoie rien)
    python main.py --trouver-chat-id  # affiche votre TELEGRAM_CHAT_ID

Rappel des garanties :
  - aucun site n'est consulté (pas de scraping) : seuls vos emails sont lus ;
  - aucun message n'est envoyé à une agence : l'outil crée au mieux un
    BROUILLON Gmail, que vous relisez et envoyez vous-même.
"""
import argparse
import logging
import time
from datetime import datetime

import gmail
import modele
import extraction
import suivi
import telegram
from config import Config, charger_config, verifier
from stockage import Stockage

log = logging.getLogger("alerte-appart")


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
            retenue, raison = extraction.est_eligible(a, cfg.prix_max, cfg.arrondissements)
            db.ajouter(a, retenue, raison)
            log.info("  %s %s € / %s m² / %se -> %s",
                     "✅" if retenue else "❌", a.prix_cc, a.surface, a.arrondissement, raison)
        db.marquer_email_traite(cle)


# --------------------------------------------------------------------------
# Étapes 4-6 : message de candidature, brouillon Gmail, notification Telegram
# --------------------------------------------------------------------------

def message_telegram(annonce, message: str, etat_brouillon: str) -> str:
    e = telegram.echapper
    v = modele.valeurs_annonce(annonce)
    lignes = [
        f"🏠 <b>Nouvelle annonce</b> ({e(v['source'])})",
        f"📍 Paris {e(v['arrondissement'])} · {e(v['surface'])} m² · <b>{e(v['prix'])} € CC</b>",
    ]
    if annonce["charges_incertaines"]:
        lignes.append("⚠️ Prix indiqué hors charges, montant des charges inconnu : à vérifier.")
    lignes.append(f'🔗 <a href="{e(annonce["lien"], True)}">Voir l\'annonce</a>')
    lignes.append(e(annonce["lien"]))
    if annonce["email_contact"]:
        lignes.append(f"✉️ {e(annonce['email_contact'])}")
    if annonce["telephone"]:
        lignes.append(f"📞 {e(annonce['telephone'])}")
    if etat_brouillon == "oui":
        lignes.append("📝 Brouillon Gmail créé avec votre dossier : relisez-le puis envoyez-le vous-même.")
    elif etat_brouillon not in ("non", ""):
        lignes.append(f"⚠️ Brouillon Gmail non créé : {e(etat_brouillon)}")
    entete = "\n".join(lignes) + "\n\n<b>Message prêt à copier</b> (appuyez dessus pour le copier) :\n"

    # On coupe le message si besoin pour respecter la limite de Telegram.
    place = telegram.LIMITE - len(entete) - 30
    corps = e(message)
    if len(corps) > place:
        corps = corps[:place] + "…"
    return entete + f"<pre>{corps}</pre>"


def creer_brouillon_si_possible(cfg: Config, imap, annonce, objet: str, message: str) -> str:
    """Renvoie 'oui', 'non' (pas d'email d'agence) ou le texte de l'erreur."""
    if not annonce["email_contact"]:
        return "non"
    pdf = cfg.dossier_pdf if cfg.dossier_pdf.is_file() else None
    try:
        gmail.creer_brouillon(imap, cfg.gmail_adresse, annonce["email_contact"], objet, message, pdf)
    except Exception as exc:  # on ne bloque pas la notification pour autant
        log.error("Échec du brouillon Gmail : %s", exc)
        return f"erreur ({exc})"
    if pdf is None:
        log.warning("Dossier PDF introuvable (%s) : brouillon créé SANS pièce jointe", cfg.dossier_pdf)
        return "créé sans PDF (fichier introuvable)"
    return "oui"


def notifier_annonces(cfg: Config, db: Stockage, imap) -> None:
    for annonce in db.a_notifier():
        valeurs = modele.valeurs_annonce(annonce)
        objet_modele, message_modele = modele.charger(cfg.fichier_modele)
        objet = modele.remplir(objet_modele, valeurs)
        message = modele.remplir(message_modele, valeurs)

        # Le brouillon n'est créé qu'une fois, même si Telegram échoue ensuite.
        etat = annonce["brouillon"]
        if etat is None:
            etat = creer_brouillon_si_possible(cfg, imap, annonce, objet, message)
            db.marquer_brouillon(annonce["id"], etat)

        telegram.envoyer(cfg.telegram_token, cfg.telegram_chat_id, message_telegram(annonce, message, etat))
        db.marquer_notifiee(annonce["id"])
        log.info("Notification envoyée pour l'annonce #%d", annonce["id"])


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
            "annonce": f"Paris {v['arrondissement']} – {v['surface']} m² – {v['prix']} € CC",
            "lien": a["lien"],
            "contact": v["contact"],
            "brouillon_gmail": a["brouillon"] or "",
            "statut": "nouveau",
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
            texte = (
                f"⏰ <b>Pensez à relancer</b> (annonce reçue il y a {cfg.relance_heures} h)\n"
                f"📍 Paris {e(v['arrondissement'])} · {e(v['surface'])} m² · {e(v['prix'])} € CC\n"
                f'🔗 <a href="{e(a["lien"], True)}">Voir l\'annonce</a>\n'
                + (f"Contact : {e(v['contact'])}\n" if v["contact"] else "")
                + f"Statut actuel dans suivi.csv : {e(statut or 'nouveau')}"
            )
            telegram.envoyer(cfg.telegram_token, cfg.telegram_chat_id, texte)
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
        notifier_annonces(cfg, db, imap)
    finally:
        try:
            imap.logout()
        except Exception:
            pass
    mettre_a_jour_csv(cfg, db)
    envoyer_relances(cfg, db)


def boucle(cfg: Config, db: Stockage, une_fois: bool) -> None:
    log.info("Démarrage. Critères : ≤ %d € CC, arrondissements %s. Ctrl+C pour arrêter.",
             cfg.prix_max, sorted(cfg.arrondissements))
    while True:
        try:
            cycle(cfg, db)
        except KeyboardInterrupt:
            raise
        except Exception:
            # Une coupure réseau ne doit pas arrêter l'outil : on réessaie au cycle suivant.
            log.exception("Erreur pendant le cycle, nouvel essai dans %d s", cfg.intervalle_secondes)
        if une_fois:
            return
        time.sleep(cfg.intervalle_secondes)


def commande_test(cfg: Config) -> None:
    verifier(cfg, "gmail_adresse", "gmail_mot_de_passe", "telegram_token", "telegram_chat_id")
    imap = gmail.connexion(cfg.gmail_adresse, cfg.gmail_mot_de_passe)
    print("✅ Connexion Gmail OK. Dossier brouillons :", gmail.dossier_brouillons(imap))
    imap.logout()
    telegram.envoyer(cfg.telegram_token, cfg.telegram_chat_id, "✅ Test réussi : le bot fonctionne.")
    print("✅ Message Telegram envoyé : vérifiez votre téléphone.")
    modele.charger(cfg.fichier_modele)
    print("✅ Modèle", cfg.fichier_modele.name, "lisible.")
    print("✅ Dossier PDF trouvé." if cfg.dossier_pdf.is_file()
          else f"⚠️  Dossier PDF introuvable : {cfg.dossier_pdf}")


def commande_analyser(cfg: Config) -> None:
    """Lecture seule : affiche ce que l'outil comprend des emails récents."""
    verifier(cfg, "gmail_adresse", "gmail_mot_de_passe")
    imap = gmail.connexion(cfg.gmail_adresse, cfg.gmail_mot_de_passe)
    try:
        for _, msg in gmail.lire_emails(imap, cfg.dossier_imap, cfg.expediteurs, cfg.jours_historique):
            print(f"\n=== {msg.get('Date')} | {msg.get('From')} | {msg.get('Subject')}")
            for a in extraction.extraire_annonces(msg, a_exclure={cfg.gmail_adresse.lower()}):
                ok, raison = extraction.est_eligible(a, cfg.prix_max, cfg.arrondissements)
                print(f"  {'✅' if ok else '❌'} {a.prix_cc} € | {a.surface} m² | {a.arrondissement}e | "
                      f"{a.email_contact or a.telephone or '-'} | {raison}\n     {a.lien[:100]}")
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
