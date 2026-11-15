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
import threading
import time
from logging.handlers import RotatingFileHandler
from datetime import datetime

import extraction
import gmail
import agents
import bot
import ia
import modele
import recherche
import resumes
import suivi
import telegram
from config import DOSSIER_PROJET, Config, charger_config, verifier
from stockage import Stockage

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
            retenue, raison = extraction.est_eligible(a, cfg.prix_max, cfg.zones, cfg.prix_max_coloc,
                                                    a.source.lower() in cfg.sources_toutes_zones)
            db.ajouter(a, retenue, raison)
            log.info("  %s %s%s € / %s m² / %s -> %s", "✅" if retenue else "❌",
                     "coloc " if a.colocation else "", a.prix_cc, a.surface, a.ville, raison)
        db.marquer_email_traite(cle)


# Cartes Telegram, boutons et réponses : voir bot.py
from bot import (  # noqa: E402,F401  (réexportés pour les tests)
    analyser, boutons_annonce, destinataire, donnee_bouton, envoyer_dossier, jeton, message_telegram,
    notifier_annonces, photos_annonce, preparer_candidature, traiter_boutons, traiter_clic, traiter_message,
    _date,
)


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
            "verdict": a["verdict"] or "",
            "note": f"{a['note']:g}/5".replace(".", ",") if a["note"] else "",
            "annonce": ("🧪 ESSAI – " if a["essai"] else "")
                       + ("⭐ " if a["ville"] in cfg.priorites else "")
                       + ("Coloc – " if a["colocation"] else "")
                       + f"{v['ville']} – {v['surface']} m² – {v['prix']} € CC",
            "lien": a["lien"],
            "contact": v["contact"],
            "candidature": a["candidature"] or "",
            "statut": a["statut"] or ("mise de côté 🔴" if a["differee"] else "nouveau"),
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
    if db.lire_etat("pause") == "1":  # /pause dans Telegram : on ne lit plus les alertes
        return
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
    nouvel_email, arret = threading.Event(), threading.Event()
    if not une_fois:
        try:  # menu des commandes dans Telegram (/pause, /stats…)
            telegram.definir_commandes(cfg.telegram_token, bot.COMMANDES)
        except Exception as exc:
            log.debug("Menu des commandes non défini (%s)", exc)
        if cfg.imap_idle:  # réaction dès qu'un email arrive, en plus de la lecture toutes les 2 min
            threading.Thread(target=gmail.surveiller, daemon=True, name="imap-idle",
                             args=(cfg.gmail_adresse, cfg.gmail_mot_de_passe, cfg.dossier_imap,
                                   nouvel_email, arret)).start()
        if cfg.agents_actifs:  # agents : évaluation, recherche du contact, canal (en arrière-plan)
            agents.Travailleur(cfg, arret).start()
            log.info("🤖 Agents actifs (%s, recherche %s).", cfg.llm_modele,
                     "Tavily" if cfg.tavily_cle else "désactivée : TAVILY_CLE absente")
    try:
        _boucle(cfg, db, une_fois, prochain_cycle, reseau, nouvel_email)
    finally:
        arret.set()


def _boucle(cfg: Config, db: Stockage, une_fois: bool, prochain_cycle: float, reseau: EtatReseau,
            nouvel_email: threading.Event) -> None:
    while True:
        if time.time() >= prochain_cycle or nouvel_email.is_set():
            nouvel_email.clear()
            try:
                cycle(cfg, db)
                reseau.ok()
            except ERREURS_RESEAU as exc:
                reseau.erreur(exc)
            except Exception:
                # Une coupure réseau ne doit pas arrêter l'outil : on réessaie au cycle suivant.
                log.exception("Erreur pendant le cycle, nouvel essai dans %d s", cfg.intervalle_secondes)
            prochain_cycle = time.time() + cfg.intervalle_secondes
        try:  # rapports terminés par les agents
            bot.envoyer_rapports(cfg, db)
        except ERREURS_RESEAU as exc:
            reseau.erreur(exc)
        except Exception:
            log.exception("Erreur en envoyant un rapport d'agent")
        try:  # bilan du matin et résumé du soir, à l'heure prévue
            resumes.envoyer_programmes(cfg, db)
        except ERREURS_RESEAU as exc:
            reseau.erreur(exc)
        except Exception:
            log.exception("Erreur en préparant un message programmé")
        # Entre deux lectures d'emails, on écoute les boutons Telegram (réaction en ~1 s).
        # Avec la surveillance IMAP, on revient au moins toutes les 10 s voir si un email est arrivé.
        maxi = 10 if cfg.imap_idle else 25
        attente = 0 if une_fois else int(min(maxi, max(1, prochain_cycle - time.time())))
        try:
            traiter_boutons(cfg, db, attente)
            reseau.ok()
        except ERREURS_RESEAU as exc:
            reseau.erreur(exc)
            if not une_fois:
                time.sleep(10)
        except telegram.ConflitTelegram as exc:
            # Deux exemplaires lisent le même bot : chacun ne reçoit qu'une partie des appuis.
            log.error("⚠️  %s : arrêtez l'autre exemplaire. Nouvel essai dans 60 s.", exc)
            if not une_fois:
                time.sleep(60)
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
        print(f"✅ {cfg.email_reponse} sera en copie (Cc) de chaque candidature, et recevra les réponses.")
    print(f"✅ Expéditeur affiché : {cfg.nom_expediteur + ' <' + cfg.gmail_adresse + '>' if cfg.nom_expediteur else cfg.gmail_adresse}"
          + ("" if cfg.nom_expediteur else "   (astuce : NOM_EXPEDITEUR=Prénom Nom dans .env)"))
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
    if not cfg.llm_cle:
        print("ℹ️  Agents désactivés (LLM_CLE absente du .env).")
    else:
        try:
            ia.discuter(cfg, [{"role": "user", "content": "Réponds seulement : OK"}])
            print(f"✅ IA joignable ({cfg.llm_modele}).")
        except ia.IAIndisponible as exc:
            print(f"❌ IA : {exc}")
        if not cfg.tavily_cle:
            print("⚠️  Recherche web désactivée (TAVILY_CLE absente) : les agents ne chercheront pas les contacts.")
        else:
            try:
                recherche.chercher(cfg, None, "agence immobilière Paris contact", n=1)
                print("✅ Recherche web (Tavily) joignable.")
            except recherche.RechercheIndisponible as exc:
                print(f"❌ Recherche : {exc}")


def commande_analyser(cfg: Config) -> None:
    """Lecture seule : affiche ce que l'outil comprend des emails récents."""
    verifier(cfg, "gmail_adresse", "gmail_mot_de_passe")
    imap = gmail.connexion(cfg.gmail_adresse, cfg.gmail_mot_de_passe)
    try:
        for _, msg in gmail.lire_emails(imap, cfg.dossier_imap, cfg.expediteurs, cfg.jours_historique):
            print(f"\n=== {msg.get('Date')} | {msg.get('From')} | {msg.get('Subject')}")
            for a in extraction.extraire_annonces(msg, a_exclure={cfg.gmail_adresse.lower()}):
                ok, raison = extraction.est_eligible(a, cfg.prix_max, cfg.zones, cfg.prix_max_coloc,
                                                    a.source.lower() in cfg.sources_toutes_zones)
                print(f"  {'✅' if ok else '❌'} {'COLOC ' if a.colocation else ''}{a.prix_cc} € | "
                      f"{a.surface} m² | {a.ville} | {a.email_contact or a.telephone or '-'} | {raison}\n"
                      f"     {a.lien[:100]}\n     photos : {len(a.photos)}")
                if a.ville is None or a.surface is None or a.prix_cc is None:
                    # Extraction incomplète : on montre le texte lu, pour pouvoir corriger.
                    print(f"     ⚠️ texte lu : {a.texte[:400]}")
    finally:
        imap.logout()


PORT_VERROU = 47821  # port local réservé pour empêcher deux lancements en même temps


def verrouiller_instance() -> socket.socket:
    """Une seule instance à la fois : deux outils en parallèle se disputeraient Telegram."""
    verrou = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        verrou.bind(("127.0.0.1", PORT_VERROU))
    except OSError:
        verrou.close()
        raise SystemExit("⚠️  L'outil tourne déjà (dans une autre fenêtre ou au démarrage de Windows). "
                         "Fermez-le d'abord, ou laissez-le tourner.")
    return verrou


def configurer_logs() -> None:
    """Journal à l'écran + dans logs/alerte-appart.log (1 Mo × 3 fichiers, les plus anciens sont effacés)."""
    format_ = logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%d/%m %H:%M:%S")
    dossier = DOSSIER_PROJET / "logs"
    dossier.mkdir(exist_ok=True)
    fichier = RotatingFileHandler(dossier / "alerte-appart.log", maxBytes=1_000_000, backupCount=3,
                                  encoding="utf-8")
    ecran = logging.StreamHandler()
    for h in (fichier, ecran):
        h.setFormatter(format_)
    racine = logging.getLogger()
    racine.setLevel(logging.INFO)
    racine.handlers = [ecran, fichier]


def main() -> None:
    configurer_logs()
    ap = argparse.ArgumentParser(description="Alertes appartement Paris -> Telegram")
    ap.add_argument("--une-fois", action="store_true", help="un seul passage puis arrêt")
    ap.add_argument("--test", action="store_true", help="tester Gmail + Telegram")
    ap.add_argument("--analyser", action="store_true", help="afficher l'extraction des emails récents")
    ap.add_argument("--trouver-chat-id", action="store_true", help="afficher votre identifiant Telegram")
    ap.add_argument("--essai", type=int, nargs="?", const=3, metavar="N",
                    help="notifier les N dernières annonces écartées, pour essayer (envois vers vous-même)")
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
    verrou = verrouiller_instance()  # noqa: F841 (gardé ouvert tant que l'outil tourne)
    db = Stockage(cfg.fichier_db)
    if args.essai:
        ids = db.passer_en_essai(args.essai)
        if ids:
            log.info("🧪 Essai : %d annonce(s) écartée(s) vont être notifiées (n° %s). Les envois partiront "
                     "vers %s, jamais vers les annonceurs.", len(ids), ", ".join(map(str, ids)),
                     cfg.email_reponse or cfg.gmail_adresse)
        else:
            log.info("🧪 Essai : aucune annonce écartée en base. Lancez d'abord python main.py une fois.")
    try:
        boucle(cfg, db, args.une_fois)
    except KeyboardInterrupt:
        log.info("Arrêt demandé, à bientôt !")


if __name__ == "__main__":
    main()
