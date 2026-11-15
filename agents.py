"""Agents : trouver le contact de l'annonceur, choisir le canal, rédiger (et, en option, évaluer).

Ils ne travaillent QUE quand vous appuyez sur « ✅ Postuler » pour une annonce sans email
(les offres gratuites d'IA et de recherche sont limitées). Étapes, chacune décidée par l'IA :

  1. Évaluateur  : (désactivé par défaut) intérêt sur 5, synthèse, risques, questions de visite.
  2. Chercheur   : trouve l'email / le téléphone (dans l'annonce, via une recherche web,
                   sur le site de l'agence). Il choisit lui-même ses recherches.
  3. Stratège-rédacteur : choisit le canal (email, appel, formulaire du site) et écrit une
                   accroche propre à l'annonce, et un script d'appel si besoin.

Garanties :
  - aucun envoi : le résultat est un RAPPORT dans Telegram, avec un bouton « ✅ Valider » ;
  - aucune donnée personnelle envoyée à l'IA ou à la recherche (salaire, modèle de message,
    vos emails, votre dossier) : seulement le texte de l'annonce et les infos de l'agence ;
  - un email ou un téléphone n'est retenu que s'il figure MOT POUR MOT dans un texte
    réellement lu (l'IA ne peut pas en inventer un) ;
  - jamais de lecture des sites d'annonces (voir recherche.DOMAINES_INTERDITS).

Les agents tournent dans un fil d'exécution à part (Travailleur) pour ne pas bloquer
les boutons ; les rapports sont envoyés par la boucle principale (bot.envoyer_rapports).
"""
import json
import logging
import re
import threading

import extraction
import ia
import recherche
from config import Config
from stockage import Stockage

log = logging.getLogger("alerte-appart")

MAX_TOURS_CONTACT = 4
MAX_RECHERCHES = 3
MAX_PAGES = 3
CANAUX = ("email", "appel", "formulaire")


# --------------------------------------------------------------------------
# Ce que l'IA voit d'une annonce (rien de personnel)
# --------------------------------------------------------------------------

def texte_complet(annonce) -> str:
    try:
        return annonce["texte_complet"] or ""
    except (IndexError, KeyError):
        return ""


def fiche_annonce(annonce) -> str:
    type_ = "colocation" if annonce["colocation"] else "location (logement entier)"
    complet = texte_complet(annonce)
    texte = (f"Description complète de l'annonce (collée par le candidat) :\n{complet[:4000]}" if complet else
             f"Texte de l'annonce (extrait de l'email d'alerte) :\n{(annonce['texte'] or '')[:1500]}")
    return (f"Site d'origine : {annonce['source']}\nType : {type_}\nVille : {annonce['ville'] or '?'}\n"
            f"Loyer charges comprises : {annonce['prix_cc'] or '?'} €\nSurface : {annonce['surface'] or '?'} m²\n"
            + texte)


# --------------------------------------------------------------------------
# 1. Évaluateur
# --------------------------------------------------------------------------

SYSTEME_EVALUATEUR = """Tu es un expert du marché locatif à Paris et en petite couronne, qui aide un locataire
à décider vite. Réponds UNIQUEMENT par un objet JSON avec ces clés :
"interet" (entier de 1 à 5), "synthese" (1-2 phrases en français), "points_forts" (liste de 3 maximum),
"risques" (liste de 3 maximum), "questions_visite" (liste de 3 questions maximum à poser à l'annonceur),
"recommandation" ("candidater", "a_verifier" ou "passer"). Sois concret et bref. N'invente aucun fait
absent de l'annonce."""


def evaluer(cfg: Config, db: Stockage, annonce, resume) -> dict:
    analyse_regles = (f"Analyse automatique : verdict {resume.verdict}, note {resume.note_globale}/5, "
                      f"notes {resume.notes}. Pour : {', '.join(resume.pour) or '-'}. "
                      f"Contre : {', '.join(resume.contre) or '-'}. "
                      f"Signes d'arnaque : {', '.join(resume.alertes_arnaque) or 'aucun'}.")
    brut = ia.demander_json(cfg, SYSTEME_EVALUATEUR, fiche_annonce(annonce) + "\n\n" + analyse_regles, db=db)
    liste = lambda cle: [str(x)[:200] for x in (brut.get(cle) or []) if x][:3]  # noqa: E731
    try:
        interet = max(1, min(5, int(brut.get("interet", 3))))
    except (TypeError, ValueError):
        interet = 3
    recommandation = brut.get("recommandation") if brut.get("recommandation") in (
        "candidater", "a_verifier", "passer") else "a_verifier"
    # Les règles gardent le dernier mot : jamais « candidater » sur une annonce suspecte ou 🔴.
    if resume.alertes_arnaque:
        recommandation = "passer"
    elif resume.verdict.startswith("🔴") and recommandation == "candidater":
        recommandation = "a_verifier"
    return {"interet": interet, "synthese": str(brut.get("synthese", ""))[:400],
            "points_forts": liste("points_forts"), "risques": liste("risques"),
            "questions_visite": liste("questions_visite"), "recommandation": recommandation}


# --------------------------------------------------------------------------
# 2. Chercheur de contact (boucle avec outils)
# --------------------------------------------------------------------------

SYSTEME_CONTACT = """Tu cherches comment contacter l'annonceur (agence immobilière ou particulier) d'une annonce
de location, pour envoyer une candidature. Tu disposes d'outils :
- chercher_web(requete) : recherche sur le web (évite les sites d'annonces, ils sont bloqués) ;
- lire_page(url) : lit une page d'un site d'agence (page contact, mentions légales…) ;
- terminer(...) : à appeler dès que tu as trouvé, ou si c'est impossible.
Méthode : 1) regarde si l'annonce donne déjà un email ou un téléphone ; 2) sinon repère le nom de l'agence
ou une référence dans le texte et cherche « nom agence + ville + contact » ; 3) lis au plus une ou deux pages
du site de l'agence. N'invente JAMAIS d'email ni de téléphone : recopie-les exactement tels que lus.
Fais au plus 3 recherches. Si aucun nom d'agence n'apparaît, termine vite sans contact."""

OUTILS_CONTACT = [
    {"type": "function", "function": {
        "name": "chercher_web", "description": "Recherche sur le web. Renvoie titres, adresses et extraits.",
        "parameters": {"type": "object", "properties": {"requete": {"type": "string"}}, "required": ["requete"]}}},
    {"type": "function", "function": {
        "name": "lire_page", "description": "Lit le texte d'une page web d'agence (pas les sites d'annonces).",
        "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}}},
    {"type": "function", "function": {
        "name": "terminer", "description": "Donne le résultat final de la recherche de contact.",
        "parameters": {"type": "object", "properties": {
            "email": {"type": "string", "description": "email exact trouvé, ou vide"},
            "telephone": {"type": "string", "description": "téléphone exact trouvé, ou vide"},
            "agence": {"type": "string", "description": "nom de l'agence ou « particulier », ou vide"},
            "source_url": {"type": "string", "description": "page où le contact a été lu, ou vide"},
            "confiance": {"type": "number", "description": "0 à 1"},
            "raison": {"type": "string"}},
            "required": ["email", "telephone", "confiance"]}}},
]


def _chiffres(texte: str) -> str:
    chiffres = re.sub(r"\D", "", texte or "")
    return chiffres[-9:]  # 9 derniers chiffres : identiques en 01 23… et +33 1 23…


def verifier_contact(brut: dict, textes_lus: list, a_exclure=()) -> dict:
    """Garde-fou : on ne garde que ce qui apparaît vraiment dans les textes lus."""
    corpus = "\n".join(textes_lus)
    email = (brut.get("email") or "").strip().lower()
    if email and (email not in corpus.lower() or not extraction.extraire_email(email, a_exclure)):
        log.info("Agent : email « %s » rejeté (absent des pages lues ou non valable)", email)
        email = ""
    telephone = (brut.get("telephone") or "").strip()
    if telephone:
        cible = _chiffres(telephone)
        # On garde le numéro tel qu'il est écrit dans la page (ex. « 06 12 34 56 78 »).
        trouves = {_chiffres(m.group(0)): m.group(0) for m in extraction.TELEPHONE_RE.finditer(corpus)}
        if len(cible) < 9 or cible not in trouves:
            log.info("Agent : téléphone « %s » rejeté (absent des pages lues)", telephone)
            telephone = ""
        else:
            telephone = extraction.extraire_telephone(trouves[cible]) or trouves[cible]
    try:
        confiance = max(0.0, min(1.0, float(brut.get("confiance", 0) or 0)))
    except (TypeError, ValueError):
        confiance = 0.0
    return {"email": email, "telephone": telephone, "agence": str(brut.get("agence", "") or "")[:80],
            "source_url": str(brut.get("source_url", "") or "")[:300],
            "confiance": confiance if (email or telephone) else 0.0, "raison": str(brut.get("raison", ""))[:300]}


def chercher_contact(cfg: Config, db: Stockage, annonce, agence: str = "") -> dict:
    a_exclure = {cfg.gmail_adresse.lower(), (cfg.email_reponse or "").lower()}
    textes_lus = [annonce["texte"] or "", texte_complet(annonce)]
    deja = {"email": annonce["email_contact"] or "", "telephone": annonce["telephone"] or ""}
    if deja["email"]:  # l'annonce donne déjà un email : inutile de chercher
        return verifier_contact({**deja, "agence": "", "source_url": annonce["lien"], "confiance": 0.9,
                                 "raison": "email indiqué dans l'annonce"}, textes_lus + [deja["email"]], a_exclure)
    messages = [{"role": "system", "content": SYSTEME_CONTACT},
                {"role": "user", "content": fiche_annonce(annonce)
                 + (f"\nNom de l'agence : {agence}" if agence else "")
                 + (f"\nTéléphone déjà connu : {deja['telephone']}" if deja["telephone"] else "")}]
    recherches = pages = 0
    resultat = {}
    for _ in range(MAX_TOURS_CONTACT):
        reponse = ia.discuter(cfg, messages, outils=OUTILS_CONTACT, db=db)
        appels = reponse.get("tool_calls") or []
        if not appels:  # le modèle a répondu en texte : on tente d'y lire un JSON
            try:
                resultat = json.loads(reponse.get("content") or "{}")
            except ValueError:
                resultat = {}
            break
        messages.append({"role": "assistant", "content": reponse.get("content") or "", "tool_calls": appels})
        fini = False
        for appel in appels:
            nom = appel.get("function", {}).get("name")
            try:
                args = json.loads(appel.get("function", {}).get("arguments") or "{}")
            except ValueError:
                args = {}
            if nom == "terminer":
                resultat, fini = args, True
                sortie = "ok"
            elif nom == "chercher_web" and recherches < MAX_RECHERCHES:
                recherches += 1
                try:
                    trouves = recherche.chercher(cfg, db, str(args.get("requete", "")))
                except recherche.RechercheIndisponible as exc:
                    trouves, sortie = [], f"recherche indisponible : {exc}"
                else:
                    textes_lus += [t["extrait"] for t in trouves]
                    sortie = json.dumps(trouves, ensure_ascii=False)[:6000] or "aucun résultat"
            elif nom == "lire_page" and pages < MAX_PAGES:
                pages += 1
                texte = recherche.lire_page(str(args.get("url", "")))
                textes_lus.append(texte)
                sortie = texte[:6000] or "page interdite, refusée ou vide"
            else:
                sortie = "limite atteinte : appelle terminer maintenant"
            messages.append({"role": "tool", "tool_call_id": appel.get("id", ""), "content": sortie})
        if fini:
            break
    if not resultat and deja["telephone"]:
        resultat = {"telephone": deja["telephone"], "confiance": 0.8, "raison": "téléphone indiqué dans l'annonce"}
    contact = verifier_contact(resultat, textes_lus, a_exclure)
    if not contact["telephone"] and deja["telephone"]:  # le téléphone de l'annonce reste valable
        contact["telephone"] = deja["telephone"]
        contact["confiance"] = max(contact["confiance"], 0.8)
    contact.update(recherches=recherches, pages=pages)
    return contact


# --------------------------------------------------------------------------
# 0. Vérificateur : lit la description complète (arnaque, agence ou particulier, contact)
# --------------------------------------------------------------------------

SYSTEME_VERIFICATEUR = """Tu vérifies une annonce de location à Paris pour un candidat locataire, avant qu'il postule.
Réponds UNIQUEMENT en JSON avec :
"type_annonceur" : "agence", "particulier" ou "inconnu" ;
"agence" : nom de l'agence s'il apparaît, sinon chaîne vide ;
"email" et "telephone" : recopiés EXACTEMENT s'ils figurent dans le texte, sinon chaîne vide ;
"risque_arnaque" : entier de 0 à 100 ;
"signaux_arnaque" : liste (3 maximum) des éléments suspects RÉELLEMENT présents dans le texte. Signaux
classiques : prix très inférieur au marché, propriétaire à l'étranger, paiement ou caution avant visite,
Western Union / mandat / coupons, clés envoyées par la poste, pas de visite possible, pièces d'identité
demandées avant visite, contact uniquement WhatsApp ou numéro étranger, histoire personnelle pour presser ;
"questions_visite" : 3 questions utiles maximum ;
"synthese" : une phrase.
N'invente rien qui ne soit pas dans le texte."""


def verifier_annonce(cfg: Config, db: Stockage, annonce) -> dict:
    """Analyse la description complète collée par vous. Les règles fixes complètent l'avis de l'IA."""
    import analyse  # import ici : analyse est déjà chargé par bot

    complet = texte_complet(annonce)
    brut = ia.demander_json(cfg, SYSTEME_VERIFICATEUR, fiche_annonce(annonce), db=db) if complet else {}
    try:
        risque_ia = max(0, min(100, int(brut.get("risque_arnaque", 0) or 0)))
    except (TypeError, ValueError):
        risque_ia = 0
    regles = analyse.signaux_arnaque(" ".join([annonce["texte"] or "", complet]), str(brut.get("email", "")),
                                     annonce["prix_cc"], annonce["surface"], annonce["ville"] or "",
                                     bool(annonce["colocation"]))
    signaux_ia = [str(x)[:150] for x in (brut.get("signaux_arnaque") or []) if x][:3] if risque_ia >= 40 else []
    type_annonceur = brut.get("type_annonceur") if brut.get("type_annonceur") in ("agence", "particulier") else ""
    if not type_annonceur:  # sans avis de l'IA : indices simples
        simple = (complet or annonce["texte"] or "").lower()
        type_annonceur = ("particulier" if annonce["source"] == "PAP" or "particulier" in simple else
                          "agence" if re.search(r"\bagence|honoraires|immobili", simple) else "inconnu")
    return {
        "type_annonceur": type_annonceur, "agence": str(brut.get("agence", "") or "")[:80],
        "email": str(brut.get("email", "") or ""), "telephone": str(brut.get("telephone", "") or ""),
        "risque_ia": risque_ia, "signaux": list(dict.fromkeys(regles + signaux_ia)),
        "niveau": analyse.niveau_risque(regles, risque_ia),
        "questions_visite": [str(x)[:200] for x in (brut.get("questions_visite") or []) if x][:3],
        "synthese": str(brut.get("synthese", "") or "")[:300],
    }


# --------------------------------------------------------------------------
# 3. Stratège-rédacteur : canal + accroche + script d'appel
# --------------------------------------------------------------------------

SYSTEME_REDACTEUR = """Tu aides un candidat locataire à contacter un annonceur. Réponds UNIQUEMENT en JSON avec :
"canal" : "email", "appel" ou "formulaire" (choisis parmi les canaux POSSIBLES indiqués ; préfère l'appel
si l'annonce demande de téléphoner ou si c'est un particulier joignable par téléphone, sinon l'email) ;
"raison" : une phrase ;
"accroche" : 2 à 3 phrases en français, au vouvoiement, qui montrent que le candidat a lu CETTE annonce
(atouts précis, quartier, disponibilité…), sans formule de politesse, sans rien inventer sur le candidat
(ni métier, ni salaire, ni nom) ;
"script_appel" : si canal = "appel", 4 à 6 courtes lignes pour l'appel, avec le marqueur [PRÉSENTATION]
à l'endroit où le candidat se présente ; sinon chaîne vide."""


def choisir_et_rediger(cfg: Config, db: Stockage, annonce, verification: dict, contact: dict) -> dict:
    possibles = [c for c, ok in (("email", contact["email"]), ("appel", contact["telephone"]),
                                 ("formulaire", True)) if ok]
    demande = (fiche_annonce(annonce) + f"\n\nAnnonceur : {verification['type_annonceur']}."
               + f"\nCanaux POSSIBLES : {', '.join(possibles)}."
               + f"\nQuestions utiles pour la visite : {'; '.join(verification['questions_visite']) or '-'}")
    brut = ia.demander_json(cfg, SYSTEME_REDACTEUR, demande, db=db)
    canal = brut.get("canal") if brut.get("canal") in possibles else possibles[0]
    script = str(brut.get("script_appel", "") or "")[:1200] if canal == "appel" or contact["telephone"] else ""
    if contact["telephone"] and not script:
        script = "Bonjour, j'appelle au sujet de votre annonce de location.\n[PRÉSENTATION]\n" + "\n".join(
            verification["questions_visite"]) + "\nQuand serait-il possible de visiter ?"
    return {"canal": canal, "raison": str(brut.get("raison", ""))[:300],
            "accroche": str(brut.get("accroche", "") or "")[:600], "script_appel": script}


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def conseil(verification: dict, contact: dict, canal: str) -> str:
    """Ce que l'agent vous conseille de faire, en une phrase."""
    if verification["niveau"] == "rouge":
        return "Ne postulez pas : cette annonce a les signes d'une arnaque."
    prudence = " Restez prudent : ne payez rien et n'envoyez pas vos pièces avant d'avoir visité." \
        if verification["niveau"] == "orange" else ""
    if verification["type_annonceur"] == "particulier":
        if canal == "appel":
            return "Particulier : appelez avec le script, et n'envoyez votre dossier qu'après la visite." + prudence
        if contact["email"]:
            return "Particulier : envoyez d'abord le message seul ; le dossier après la visite." + prudence
    if canal == "email":
        return "Agence : envoyez votre message et votre dossier." + prudence
    if canal == "appel":
        return "Appelez avec le script pour obtenir une visite." + prudence
    return "Contact introuvable : postulez avec le formulaire du site (message prêt à copier)." + prudence


def executer(cfg: Config, db: Stockage, annonce, evaluation_ia: bool = False) -> dict:
    """Vérifie l'annonce, trouve le contact, choisit le canal et rédige. Renvoie le rapport (dict).

    Lancé seulement quand vous appuyez sur « Postuler » (et, si possible, après avoir collé la description).
    """
    import bot  # import ici : bot importe ce module

    resume = bot.analyser(cfg, db, annonce)
    verification = verifier_annonce(cfg, db, annonce)
    # les signaux vus sur l'alerte (ex. prix bien sous la médiane de la zone) comptent aussi
    import analyse
    verification["signaux"] = list(dict.fromkeys(resume.alertes_arnaque + verification["signaux"]))
    ordre = ["vert", "orange", "rouge"]
    verification["niveau"] = max(verification["niveau"], analyse.niveau_risque(resume.alertes_arnaque),
                                 key=ordre.index)
    evaluation = evaluer(cfg, db, annonce, resume) if evaluation_ia else {}

    vide = {"email": "", "telephone": "", "agence": verification["agence"], "source_url": "", "confiance": 0.0,
            "raison": "", "recherches": 0, "pages": 0}
    if verification["niveau"] == "rouge":  # arnaque probable : on ne cherche pas, on ne rédige pas
        contact = {**vide, "raison": "annonce suspecte : pas de recherche de contact"}
        return {"evaluation": evaluation, "verification": verification, "contact": contact, "canal": "aucun",
                "raison": "", "accroche": "", "script_appel": "", "conseil": conseil(verification, contact, "aucun")}

    complet = texte_complet(annonce)
    a_exclure = {cfg.gmail_adresse.lower(), (cfg.email_reponse or "").lower()}
    # 1. contact écrit dans l'annonce (description collée ou alerte), vérifié mot pour mot
    textes = [annonce["texte"] or "", complet, annonce["email_contact"] or ""]
    contact = verifier_contact({
        "email": verification["email"] or extraction.extraire_email(complet, a_exclure) or annonce["email_contact"] or "",
        "telephone": verification["telephone"] or extraction.extraire_telephone(complet) or annonce["telephone"] or "",
        "agence": verification["agence"], "source_url": annonce["lien"], "confiance": 0.9,
        "raison": "indiqué dans l'annonce"}, textes, a_exclure)
    contact.update(recherches=0, pages=0)
    # 2. pas d'email : recherche web, pour une agence (nommée dans la description, ou alerte seule)
    if not contact["email"] and cfg.tavily_cle and verification["type_annonceur"] != "particulier" and \
            (verification["agence"] or not complet):
        trouve = chercher_contact(cfg, db, annonce, verification["agence"])
        if not trouve["telephone"] and contact["telephone"]:
            trouve["telephone"] = contact["telephone"]
        contact = trouve
    if not (contact["email"] or contact["telephone"]):
        contact["raison"] = ("particulier sans contact affiché : passez par le site" if
                             verification["type_annonceur"] == "particulier" else
                             contact.get("raison") or "aucun nom d'agence ni contact dans l'annonce")
    strategie = choisir_et_rediger(cfg, db, annonce, verification, contact)
    return {"evaluation": evaluation, "verification": verification, "contact": contact, **strategie,
            "conseil": conseil(verification, contact, strategie["canal"]),
            "joindre_dossier": verification["type_annonceur"] != "particulier" and verification["niveau"] == "vert"}


def traiter_en_attente(cfg: Config, db: Stockage, limite: int = 1) -> int:
    """Traite jusqu'à `limite` annonces en attente. Renvoie le nombre traité."""
    lignes = db.db.execute("SELECT * FROM annonces WHERE agent_etat='en_attente' ORDER BY id LIMIT ?",
                           (limite,)).fetchall()
    for annonce in lignes:
        db.maj(annonce["id"], agent_etat="en_cours")
        try:
            rapport = executer(cfg, db, annonce)
        except (ia.IAIndisponible, recherche.RechercheIndisponible) as exc:
            if "quota" in str(exc):  # quota du jour : on réessaiera demain
                db.maj(annonce["id"], agent_etat="en_attente")
                raise
            log.warning("Agent : annonce #%d non traitée (%s)", annonce["id"], exc)
            db.maj(annonce["id"], agent_etat="erreur", agent_json=json.dumps({"erreur": str(exc)[:300]}))
            continue
        except Exception as exc:  # une annonce qui plante ne bloque pas les autres
            log.exception("Agent : erreur sur l'annonce #%d", annonce["id"])
            db.maj(annonce["id"], agent_etat="erreur", agent_json=json.dumps({"erreur": str(exc)[:300]}))
            continue
        rapport["avec_description"] = bool(texte_complet(annonce))
        db.maj(annonce["id"], agent_etat="pret", agent_json=json.dumps(rapport, ensure_ascii=False))
        log.info("🤖 Agent : avis prêt pour l'annonce #%d (risque : %s, annonceur : %s, canal : %s)", annonce["id"],
                 rapport.get("verification", {}).get("niveau"), rapport.get("verification", {}).get("type_annonceur"),
                 rapport["canal"])
    return len(lignes)


class Travailleur(threading.Thread):
    """Fil d'arrière-plan : fait travailler les agents sans bloquer Telegram."""

    def __init__(self, cfg: Config, arret: threading.Event):
        super().__init__(daemon=True, name="agents")
        self.cfg, self.arret = cfg, arret

    def run(self) -> None:
        db = Stockage(self.cfg.fichier_db)  # connexion propre à ce fil
        db.db.execute("UPDATE annonces SET agent_etat='en_attente' WHERE agent_etat='en_cours'")
        db.db.commit()
        while not self.arret.is_set():
            try:
                if not traiter_en_attente(self.cfg, db):
                    self.arret.wait(5)
            except (ia.IAIndisponible, recherche.RechercheIndisponible) as exc:
                log.warning("Agents en pause 10 min (%s)", exc)
                self.arret.wait(600)
            except Exception:
                log.exception("Agents : erreur inattendue, reprise dans 60 s")
                self.arret.wait(60)
