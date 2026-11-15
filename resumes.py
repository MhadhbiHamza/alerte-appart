"""Messages de synthèse envoyés par le bot : bilan du matin, résumé du soir, /stats, /suivi.

Chaque message programmé part une seule fois par jour (la date d'envoi est retenue
dans la base), dans les 3 heures qui suivent l'heure choisie : si le PC était éteint
à 21 h mais rallumé à 22 h, le résumé part quand même ; à 2 h du matin, non.
"""
import logging
from datetime import date, datetime, timedelta

import ia
import telegram
from config import Config
from stockage import Stockage

log = logging.getLogger("alerte-appart")
FENETRE_HEURES = 3


def _iso(d: datetime) -> str:
    return d.isoformat(timespec="seconds")


def _debut_du_jour(maintenant: datetime) -> datetime:
    return maintenant.replace(hour=0, minute=0, second=0, microsecond=0)


def _ligne_annonce(a) -> str:
    e = telegram.echapper
    note = f"{a['note']:g}/5".replace(".", ",") if a["note"] else "?"
    surface = f"{a['surface']:g} m²" if a["surface"] else "? m²"
    return (f"{(a['verdict'] or '')[:1]} {note} · <b>{e(a['ville'] or '?')}</b> · {a['prix_cc']} € · {surface} · "
            f'<a href="{e(a["lien"], True)}">voir</a>')


def texte_stats(db: Stockage, maintenant: datetime, cfg: Config = None) -> str:
    semaine, total = db.compter(_iso(maintenant - timedelta(days=7))), db.compter("")
    lignes = ["📊 <b>Statistiques</b>", "<code>              7 jours   total</code>"]
    for cle, nom in (("vues", "Annonces vues"), ("gardees", "Gardées"), ("vertes", "🟢 À saisir"),
                     ("jaunes", "🟡 Correctes"), ("rouges", "🔴 Bof"), ("envoyes", "Dossiers envoyés"),
                     ("postules", "Postulé site")):
        lignes.append(f"<code>{nom:<14}{semaine[cle]:>7}{total[cle]:>8}</code>")
    lignes.append(f"<code>{'🤖 Rapports':<14}{db.nb_rapports(_iso(maintenant - timedelta(days=7))):>7}"
                  f"{db.nb_rapports(''):>8}</code>")
    if cfg is not None and cfg.agents_actifs:
        lignes.append(f"\nAujourd'hui : IA {ia.utilise_aujourdhui(db, 'quota_llm')}/{cfg.llm_max_jour} · "
                      f"recherches {ia.utilise_aujourdhui(db, 'quota_recherche')}/{cfg.recherches_max_jour}")
    return "\n".join(lignes)


def texte_suivi(db: Stockage) -> str:
    en_cours = db.en_cours()
    if not en_cours:
        return "📁 Aucune candidature en cours pour l'instant."
    lignes = [f"📁 <b>Candidatures en cours</b> ({len(en_cours)})"]
    for a in en_cours[:25]:
        quand = (a["envoye_le"] or a["notifiee_le"] or "")[:10]
        lignes.append(f"• {telegram.echapper(a['statut'])} le {quand} — {_ligne_annonce(a)}")
    return "\n".join(lignes)


def bilan_matin(cfg: Config, db: Stockage, maintenant: datetime) -> None:
    c = db.compter(_iso(maintenant - timedelta(days=1)))
    texte = (f"☀️ <b>Je tourne toujours</b> ({maintenant:%d/%m %H:%M})\n"
             f"Depuis hier : {c['emails']} email(s) lu(s), {c['vues']} annonce(s), {c['gardees']} gardée(s), "
             f"{c['envoyes']} dossier(s) envoyé(s).")
    if db.lire_etat("pause") == "1":
        texte += "\n⏸ L'outil est en pause : /reprise pour relancer la lecture des emails."
    telegram.envoyer(cfg.telegram_token, cfg.telegram_chat_id, texte, silencieux=True)


def resume_soir(cfg: Config, db: Stockage, maintenant: datetime) -> None:
    from bot import donnee_bouton  # import ici pour éviter une boucle d'imports (bot -> resumes)

    debut = _iso(_debut_du_jour(maintenant))
    c = db.compter(debut)
    lignes = [f"🌙 <b>Résumé du {maintenant:%d/%m}</b>",
              f"Annonces du jour : 🟢 {c['vertes']} · 🟡 {c['jaunes']} · 🔴 {c['rouges']} — "
              f"dossiers envoyés : {c['envoyes']}"]
    boutons = []
    rouges = db.differees(debut)
    if rouges:
        lignes.append(f"\n🔴 <b>Mises de côté</b> ({len(rouges)}) :")
        for a in rouges[:10]:
            lignes.append(f"• n°{a['id']} {_ligne_annonce(a)}")
            boutons.append([(f"👀 Voir n°{a['id']} · {a['ville'] or '?'} · {a['prix_cc']} €",
                             donnee_bouton(a, "afficher"))])
    attente = db.sans_decision(_iso(maintenant - timedelta(hours=24)))
    if attente:
        lignes.append(f"\n⏳ <b>Sans réponse depuis plus de 24 h</b> ({len(attente)}) :")
        lignes += [f"• {_ligne_annonce(a)}" for a in attente[:10]]
    if not rouges and not attente and not any(c[k] for k in ("vertes", "jaunes", "rouges", "envoyes")):
        lignes.append("Rien de neuf aujourd'hui.")
    telegram.envoyer(cfg.telegram_token, cfg.telegram_chat_id, "\n".join(lignes)[:telegram.LIMITE],
                     boutons=boutons or None, silencieux=True)


def _a_faire(db: Stockage, cle: str, heure, maintenant: datetime) -> bool:
    """Vrai une fois par jour, entre `heure` et `heure + FENETRE_HEURES`."""
    if heure is None or not (heure <= maintenant.hour < heure + FENETRE_HEURES):
        return False
    return db.lire_etat(cle) != date.isoformat(maintenant.date())


def envoyer_programmes(cfg: Config, db: Stockage, maintenant: datetime = None) -> None:
    """Appelée à chaque tour de boucle : envoie le bilan du matin et le résumé du soir à l'heure."""
    maintenant = maintenant or datetime.now()
    for cle, heure, fonction in (("bilan_matin", cfg.heure_bilan_matin, bilan_matin),
                                 ("resume_soir", cfg.heure_resume_soir, resume_soir)):
        if _a_faire(db, cle, heure, maintenant):
            # On note la date AVANT l'envoi : en cas d'erreur, pas de répétition toutes les 10 s.
            db.ecrire_etat(cle, date.isoformat(maintenant.date()))
            fonction(cfg, db, maintenant)
            log.info("Message programmé envoyé : %s", cle)
