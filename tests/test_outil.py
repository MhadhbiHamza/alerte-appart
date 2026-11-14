"""Tests sans connexion réseau : python -m unittest discover tests

Les emails ci-dessous imitent la structure des alertes (plusieurs cartes
d'annonces, en-tête rappelant les critères, pied de page de désinscription).
"""
import sys
import tempfile
import unittest
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import extraction  # noqa: E402
import main  # noqa: E402
import modele  # noqa: E402
import suivi  # noqa: E402
import zones  # noqa: E402
import analyse  # noqa: E402
import config  # noqa: E402
from config import Config, _zones  # noqa: E402
from stockage import Stockage  # noqa: E402

HTML_JINKA = """
<html><body><table>
<tr><td>3 nouvelles annonces pour votre alerte : Paris, budget max 900 €, 20 m² min
 <a href="https://www.jinka.fr/alert/settings">Modifier mon alerte</a></td></tr>
<tr><td><a href="https://t.jinka.fr/c/abc111"><img src="x.jpg"></a>
  <table><tr><td><a href="https://t.jinka.fr/c/abc111">Appartement 2 pièces</a></td></tr>
  <tr><td>880&nbsp;€ CC · 32 m² · Paris 11e</td></tr></table></td></tr>
<tr><td><a href="https://t.jinka.fr/c/abc222">Studio lumineux</a>
  <div>1&nbsp;150 € · 28 m² · 75014</div></td></tr>
<tr><td><a href="https://t.jinka.fr/c/abc333">Studio</a>
  <div>790 € · 22 m² · Paris 20e</div></td></tr>
<tr><td><a href="https://t.jinka.fr/unsubscribe?u=1">Se désinscrire</a></td></tr>
</table></body></html>
"""

HTML_SELOGER = """
<html><body>
<div class="card"><a href="https://www.seloger.com/annonces/locations/appartement/paris-15eme-75/123.htm?utm_source=mail">
Appartement 2 pièces 35 m²</a><p>Paris 15ème · 820 € + 60 € de charges</p>
<p>Agence Dupont – contact@agence-dupont.fr – 01 45 67 89 10</p></div>
<div class="card"><a href="https://www.seloger.com/annonces/locations/appartement/paris-17eme-75/456.htm">
Studio 19 m²</a><p>Paris 17ème · 700 € hors charges</p></div>
<footer>Ne pas répondre : no-reply@seloger.com</footer>
</body></html>
"""

TEXTE_PAP = """Bonjour,

Une nouvelle annonce correspond à votre recherche :

Location appartement 2 pièces 30 m² - Paris 18e (75018)
890 € charges comprises
https://www.pap.fr/annonces/appartement-paris-18e-r123456

Particulier : 06 12 34 56 78
"""


def email_de(expediteur, html=None, texte=None):
    msg = EmailMessage()
    msg["From"] = expediteur
    msg["Subject"] = "Nouvelles annonces"
    if texte:
        msg.set_content(texte)
    if html:
        if texte:
            msg.add_alternative(html, subtype="html")
        else:
            msg.set_content(html, subtype="html")
    # aller-retour en octets, comme un vrai email reçu par IMAP
    return BytesParser(policy=policy.default).parsebytes(msg.as_bytes())


class TestExtraction(unittest.TestCase):
    def test_jinka_plusieurs_annonces(self):
        annonces = extraction.extraire_annonces(email_de("Jinka <alerte@jinka.fr>", HTML_JINKA))
        resume = [(a.prix_cc, a.surface, a.ville, a.lien) for a in annonces]
        self.assertEqual(resume, [
            (880, 32.0, "Paris 11e", "https://t.jinka.fr/c/abc111"),
            (1150, 28.0, "Paris 14e", "https://t.jinka.fr/c/abc222"),
            (790, 22.0, "Paris 20e", "https://t.jinka.fr/c/abc333"),
        ])
        self.assertTrue(all(a.source == "Jinka" for a in annonces))

    def test_seloger_charges_et_contact(self):
        a1, a2 = extraction.extraire_annonces(email_de("SeLoger <alertes@mail.seloger.com>", HTML_SELOGER))
        self.assertEqual((a1.prix_cc, a1.surface, a1.ville), (880, 35.0, "Paris 15e"))
        self.assertEqual(a1.email_contact, "contact@agence-dupont.fr")
        self.assertEqual(a1.telephone, "01 45 67 89 10")
        self.assertEqual(a1.source, "SeLoger")
        self.assertEqual((a2.prix_cc, a2.ville, a2.charges_incertaines), (700, "Paris 17e", True))
        self.assertIsNone(a2.email_contact)  # no-reply@seloger.com ignoré

    def test_pap_texte_brut(self):
        (a,) = extraction.extraire_annonces(email_de("PAP <alerte@pap.fr>", texte=TEXTE_PAP))
        self.assertEqual((a.prix_cc, a.surface, a.ville), (890, 30.0, "Paris 18e"))
        self.assertEqual(a.lien, "https://www.pap.fr/annonces/appartement-paris-18e-r123456")
        self.assertEqual(a.source, "PAP")

    def test_prix(self):
        self.assertEqual(extraction.extraire_prix("Loyer 800 € HC, soit 850 € CC"), (850, False))
        self.assertEqual(extraction.extraire_prix("75011 1 050 € CC"), (1050, False))
        self.assertEqual(extraction.extraire_prix("850,00 € charges comprises"), (850, False))
        self.assertEqual(extraction.extraire_prix("Charges : 40 € — loyer 760 € HC"), (800, False))

    def test_filtre(self):
        voulues = {"Paris 13e", "Ivry-sur-Seine"}
        a = extraction.Annonce("Jinka", "l", "", prix_cc=900, surface=20, ville="Ivry-sur-Seine")
        self.assertTrue(extraction.est_eligible(a, 900, voulues)[0])
        a.prix_cc = 901
        self.assertFalse(extraction.est_eligible(a, 900, voulues)[0])
        a.prix_cc, a.ville = 800, "Paris 18e"
        self.assertEqual(extraction.est_eligible(a, 900, voulues), (False, "hors zone (Paris 18e)"))


class TestZones(unittest.TestCase):
    def test_detection(self):
        cas = [
            ("Paris 1er", "Paris 1er"), ("75116", "Paris 16e"), ("Paris 17ème", "Paris 17e"),
            ("13e arrondissement", "Paris 13e"), ("Paris 14 - 25 m²", "Paris 14e"),
            ("Appartement Paris 2 pièces", None),
            ("Ivry-sur-Seine (94200)", "Ivry-sur-Seine"), ("Asnières-sur-Seine", "Asnières-sur-Seine"),
            ("ASNIERES SUR SEINE", "Asnières-sur-Seine"), ("St-Denis, Basilique", "Saint-Denis"),
            ("Maisons Alfort", "Maisons-Alfort"), ("Bécon, Bois-Colombes", "Bois-Colombes"),
            ("Levallois-Perret 92300", "Levallois-Perret (92300)"),
            ("Bel appartement Colombes (92700)", "Colombes (92700)"),
            ("Studio · Saint-Maur-des-Fossés 94210", "Saint-Maur-des-Fossés (94210)"),
            ("Studio 25 m² · 92160", "autre commune (92160)"),
            # homonymes et rues parisiennes
            ("Clichy-sous-Bois", None), ("Asnières-sur-Oise", None), ("Vitry-le-François", None),
            ("Paris 12e, rue de Charenton", "Paris 12e"), ("Porte d'Ivry, Paris 13e", "Paris 13e"),
            ("Avenue de Clichy", None),
            # le code postal l'emporte, sinon la première mention
            ("Paris 13e, proche Ivry (94200)", "Ivry-sur-Seine"),
            ("Ivry-sur-Seine, à 5 min de Paris 13e", "Ivry-sur-Seine"),
            ("Paris 12e, proche Vincennes", "Paris 12e"),
        ]
        for texte, attendu in cas:
            self.assertEqual(zones.detecter_zone(texte), attendu, texte)

    def test_normalisation_env(self):
        self.assertEqual(_zones("paris 13, 75014, Paris 15ème, asnieres, ivry sur seine", "ZONES"),
                         {"Paris 13e", "Paris 14e", "Paris 15e", "Asnières-sur-Seine", "Ivry-sur-Seine"})
        self.assertEqual(len(_zones(config.ZONES_PAR_DEFAUT, "ZONES")), 35)  # 20 arrondissements + 15 communes
        with self.assertRaises(SystemExit):
            _zones("Paris 13e, Levallois", "ZONES")

    def test_tout_paris_et_seloger(self):
        tout = _zones("Paris, Ivry-sur-Seine", "ZONES")
        self.assertEqual(len(tout), 21)
        self.assertIn("Paris 1er", tout)
        self.assertIn("Paris 20e", tout)
        a = extraction.Annonce("SeLoger", "l", "", prix_cc=850, surface=30, ville="autre commune (92700)")
        self.assertFalse(extraction.est_eligible(a, 900, tout)[0])
        self.assertEqual(extraction.est_eligible(a, 900, tout, 600, toutes_zones=True), (True, "ok"))
        a.ville = None
        self.assertTrue(extraction.est_eligible(a, 900, tout, 600, toutes_zones=True)[0])
        a.prix_cc = 950   # le prix max s'applique toujours
        self.assertFalse(extraction.est_eligible(a, 900, tout, 600, toutes_zones=True)[0])

    def test_alerte_trajet_emplacement(self):
        ligne = {"texte": "", "source": "Jinka", "prix_cc": 800, "surface": 25.0, "ville": "Colombes (92700)",
                 "colocation": 0, "charges_incertaines": 0, "quartier": None}
        self.assertEqual(analyse.analyser(ligne, [], trajet_ok=True).notes["Emplacement"], 3)
        self.assertEqual(analyse.analyser(ligne, []).notes["Emplacement"], 2)

    def test_quartier(self):
        self.assertEqual(zones.detecter_quartier("Studio métro Alésia", "Paris 14e"), "Alésia")
        self.assertEqual(zones.detecter_quartier("Proche MAISON BLANCHE", "Paris 13e"), "Maison-Blanche")
        self.assertEqual(zones.detecter_quartier("Quartier Bécon", "Asnières-sur-Seine"), "Bécon")
        self.assertIsNone(zones.detecter_quartier("Alésia", "Paris 13e"))


class TestModele(unittest.TestCase):
    def test_remplissage(self):
        objet, message = modele.charger(Path(__file__).parent.parent / "templates.md")
        v = {"prix": "880", "surface": "32", "ville": "Paris 11e", "lien": "https://x"}
        self.assertEqual(modele.remplir(objet, v), "Candidature – Appartement 32 m² Paris 11e – 880 € CC")
        self.assertIn("880 € charges comprises (https://x)", modele.remplir(message, v))
        self.assertEqual(modele.remplir("{inconnue}", v), "{inconnue}")


HTML_COLOC = """
<html><body>
<div><a href="https://www.jinka.fr/c/coloc1"><img src="https://img.jinka.fr/logo.png" width="120"></a>
<img src="https://img.jinka.fr/photos/coloc1.jpg" width="300" height="200">
<a href="https://www.jinka.fr/c/coloc1">Chambre en colocation</a> 14 m² · 580 € CC · Montrouge (92120)</div>
<div><a href="https://www.jinka.fr/c/coloc2">Colocation 4 personnes</a> 18 m² · 650 € CC · Paris 15e</div>
</body></html>
"""


class TestColocationEtPhoto(unittest.TestCase):
    def test_coloc(self):
        a1, a2 = extraction.extraire_annonces(email_de("Jinka <alerte@jinka.fr>", HTML_COLOC))
        self.assertTrue(a1.colocation and a2.colocation)
        self.assertEqual(a1.photo, "https://img.jinka.fr/photos/coloc1.jpg")  # pas le logo
        voulues = _zones(config.ZONES_PAR_DEFAUT, "ZONES")
        self.assertEqual(extraction.est_eligible(a1, 900, voulues, 600), (True, "ok"))
        self.assertEqual(extraction.est_eligible(a2, 900, voulues, 600)[1], "trop cher (650 € en colocation)")
        # un appartement entier à 650 € reste accepté (plafond 900 €)
        a2.colocation = False
        self.assertTrue(extraction.est_eligible(a2, 900, voulues, 600)[0])

    def test_coloc_par_objet_de_l_email(self):
        msg = email_de("PAP <alerte@pap.fr>", texte="Paris 13e - 12 m²\n550 € CC\nhttps://www.pap.fr/annonces/r1")
        msg.replace_header("Subject", "Votre alerte Colocation Paris")
        self.assertTrue(extraction.extraire_annonces(msg)[0].colocation)

    def test_modele_coloc(self):
        chemin = Path(__file__).parent.parent / "templates.md"
        objet, message = modele.charger(chemin, colocation=True)
        self.assertIn("colocation", objet.lower())
        self.assertIn("chambre en colocation", message)
        self.assertNotIn("colocation", modele.charger(chemin)[0].lower())


# Zones du scénario de test : 5 arrondissements + 15 communes (comme avant « tout Paris »)
ZONES_SCENARIO = ("Paris 15e, Paris 14e, Paris 12e, Paris 13e, Paris 11e, Ivry-sur-Seine, Maisons-Alfort, Clamart, "
                  "Asnières-sur-Seine, Bois-Colombes, Vitry-sur-Seine, Saint-Denis, Montreuil, Meudon, Vincennes, "
                  "Charenton-le-Pont, Montrouge, Clichy, Boulogne-Billancourt, Issy-les-Moulineaux")


def clic(id_clic, data, chat=42, message_id=900):
    return {"update_id": id_clic, "callback_query": {
        "id": f"c{id_clic}", "data": data, "message": {"message_id": message_id, "chat": {"id": chat}}}}


class TestCycleComplet(unittest.TestCase):
    """Simule l'outil complet avec Gmail et Telegram remplacés par des faux."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.pdf = self.tmp / "dossier.pdf"
        self.pdf.write_bytes(b"%PDF-1.4 test")
        self.cfg = Config("moi@gmail.com", "x", "INBOX", "tok", "42", 900,
                          _zones(ZONES_SCENARIO, "ZONES"), {"Ivry-sur-Seine"},
                          ["jinka", "seloger", "pap"], self.pdf, 120, 48, 2,
                          Path(__file__).parent.parent / "templates.md", self.tmp / "suivi.csv",
                          self.tmp / "a.db", 600)
        self.db = Stockage(self.cfg.fichier_db)
        self.emails = [
            ("INBOX:1:1", email_de("Jinka <alerte@jinka.fr>", HTML_JINKA)),
            ("INBOX:1:2", email_de("SeLoger <alertes@mail.seloger.com>", HTML_SELOGER)),
            # même annonce que la 1re de Jinka, reçue via PAP : doit être ignorée (doublon)
            ("INBOX:1:3", email_de("PAP <alerte@pap.fr>",
                                   texte="Paris 11e - 32 m²\n880 € CC\nhttps://www.pap.fr/annonces/r999")),
            # banlieue prioritaire, avec un quartier de la liste
            ("INBOX:1:4", email_de("PAP <alerte@pap.fr>",
                                   texte="Ivry-sur-Seine (94200) - 2 pièces 33 m², métro Mairie d'Ivry\n"
                                         "870 € CC\nhttps://www.pap.fr/annonces/r555")),
            ("INBOX:1:5", email_de("Jinka <alerte@jinka.fr>", HTML_COLOC)),
        ]
        self.clics = []
        patchs = [
            mock.patch.object(main.gmail, "connexion"),
            mock.patch.object(main.gmail, "lire_emails", side_effect=self._faux_lire),
            mock.patch.object(main.gmail, "creer_brouillon"),
            mock.patch.object(main.gmail, "envoyer_email"),
            mock.patch.object(main.telegram, "envoyer", side_effect=lambda *a, **k: 900 + len(self.envoyes())),
            mock.patch.object(main.telegram, "mises_a_jour", side_effect=self._faux_clics),
            mock.patch.object(main.telegram, "envoyer_photos", return_value=[801, 802]),
            mock.patch.object(main.telegram, "supprimer_message"),
            mock.patch.object(main.telegram, "demander_reponse", return_value=950),
            mock.patch.object(main.telegram, "repondre_clic"),
            mock.patch.object(main.telegram, "modifier_boutons"),
        ]
        self.m = {}
        for pt in patchs:
            self.m[pt.attribute] = pt.start()
            self.addCleanup(pt.stop)

    def _faux_lire(self, imap, dossier, exp, jours, deja_traite=None):
        return [(c, m) for c, m in self.emails if not (deja_traite and deja_traite(c))]

    def _faux_clics(self, token, offset, attente):
        return [c for c in self.clics if c["update_id"] >= offset]

    def envoyes(self):
        return self.m["envoyer"].call_args_list

    def d(self, action, id_):
        """Donnée d'un bouton, avec la signature de l'annonce."""
        return main.donnee_bouton(self.db.annonce(id_), action)

    def test_pas_interesse_supprime_le_message(self):
        main.boucle(self.cfg, self.db, une_fois=True)
        self.clics = [clic(1, self.d("refuser", 7), message_id=self.db.annonce(7)["message_id"])]
        main.traiter_boutons(self.cfg, self.db, 0)
        supprimes = [c[0][2] for c in self.m["supprimer_message"].call_args_list]
        # le résumé + les 2 photos de l'album (la coloc Montrouge a une photo dans l'email)
        self.assertEqual(sorted(supprimes), sorted([self.db.annonce(7)["message_id"], 801, 802]))
        self.assertEqual(self.db.annonce(7)["statut"], "pas intéressé")
        self.assertEqual(self.m["repondre_clic"].call_args[0][2], "Annonce supprimée.")

    def test_pas_interesse_message_trop_ancien(self):
        main.boucle(self.cfg, self.db, une_fois=True)
        self.m["supprimer_message"].side_effect = RuntimeError("message can't be deleted")
        self.clics = [clic(1, self.d("refuser", 1), message_id=self.db.annonce(1)["message_id"])]
        main.traiter_boutons(self.cfg, self.db, 0)
        self.m["modifier_boutons"].assert_called()   # on remplace les boutons à la place
        self.assertEqual(self.m["repondre_clic"].call_args[0][2], "Annonce classée.")

    def test_postuler_sans_email_demande_l_adresse(self):
        main.boucle(self.cfg, self.db, une_fois=True)
        # Ivry (n° 6) n'a pas d'email : Postuler demande l'adresse, rien n'est envoyé
        self.clics = [clic(1, self.d("postuler", 6))]
        main.traiter_boutons(self.cfg, self.db, 0)
        self.m["demander_reponse"].assert_called_once()
        self.m["envoyer_email"].assert_not_called()
        # réponse sans email -> on redemande
        self.clics = [{"update_id": 2, "message": {"message_id": 951, "chat": {"id": 42}, "text": "euh",
                                                   "reply_to_message": {"message_id": 950}}}]
        main.traiter_boutons(self.cfg, self.db, 0)
        self.m["envoyer_email"].assert_not_called()
        self.assertIn("pas trouvé d'adresse email", self.m["envoyer"].call_args[0][2])
        # message d'un inconnu : ignoré
        self.clics = [{"update_id": 3, "message": {"message_id": 952, "chat": {"id": 666},
                                                   "text": "pirate@x.fr", "reply_to_message": {"message_id": 950}}}]
        main.traiter_boutons(self.cfg, self.db, 0)
        self.m["envoyer_email"].assert_not_called()
        # vraie réponse -> envoi immédiat à l'agence, une seule fois
        self.clics = [{"update_id": 4, "message": {"message_id": 953, "chat": {"id": 42},
                                                   "text": "C'est Agence.Martin@Immo.fr",
                                                   "reply_to_message": {"message_id": 950}}}]
        main.traiter_boutons(self.cfg, self.db, 0)
        self.m["envoyer_email"].assert_called_once()
        self.assertEqual(self.m["envoyer_email"].call_args[0][2], "agence.martin@immo.fr")
        self.assertEqual(self.db.annonce(6)["statut"], "dossier envoyé")
        self.assertIn("Dossier envoyé à agence.martin@immo.fr", self.m["envoyer"].call_args[0][2])
        self.clics = [{"update_id": 5, "message": {"message_id": 954, "chat": {"id": 42},
                                                   "text": "agence.martin@immo.fr",
                                                   "reply_to_message": {"message_id": 950}}}]
        main.traiter_boutons(self.cfg, self.db, 0)
        self.m["envoyer_email"].assert_called_once()

    def test_bouton_perime(self):
        main.boucle(self.cfg, self.db, une_fois=True)
        # ancien format (sans signature) ou signature d'une autre annonce : rien n'est fait
        self.clics = [clic(1, "postuler:4"), clic(2, "postuler:4:deadbeef")]
        main.traiter_boutons(self.cfg, self.db, 0)
        self.m["envoyer_email"].assert_not_called()
        self.assertIsNone(self.db.annonce(4)["statut"])
        self.assertIn("Bouton périmé", self.m["repondre_clic"].call_args[0][2])

    def test_parcours_complet(self):
        main.boucle(self.cfg, self.db, une_fois=True)
        # n° des annonces : rejetées comprises (SeLoger 15e = 4, Ivry = 6, coloc Montrouge = 7)
        # 4 retenues : Jinka 11e 880 €, SeLoger 15e 820+60 €, PAP Ivry 870 €, coloc Montrouge 580 €
        # (SeLoger 17e hors zone, Jinka 14e trop cher, Jinka 20e hors zone, coloc 15e à 650 € > 600 €)
        self.assertEqual(len(self.envoyes()), 4)
        textes = [c[0][2] for c in self.envoyes()]
        ivry, coloc = textes[2], textes[3]
        self.assertIn("⭐", ivry)
        self.assertIn("📍 <b>Ivry-sur-Seine</b> · 33 m² · <b>870 € CC</b>", ivry)
        self.assertIn("cité : Mairie d'Ivry", ivry)
        self.assertIn("📌 Quartier : Mairie d'Ivry", ivry)
        self.assertIn("à Ivry-sur-Seine, proposé à 870 €", ivry)
        self.assertNotIn("⭐", textes[0])
        self.assertIn("👥 Colocation", coloc)
        self.assertIn("chambre en colocation à Montrouge", coloc)
        # aperçu = photo de l'email si présente, sinon le lien de l'annonce
        # photos de l'email -> album, puis résumé sans aperçu ; sans photo -> aperçu du lien
        self.m["envoyer_photos"].assert_called_once()
        self.assertEqual(self.m["envoyer_photos"].call_args[0][2], ["https://img.jinka.fr/photos/coloc1.jpg"])
        self.assertIsNone(self.envoyes()[3][1]["apercu"])
        self.assertEqual(self.envoyes()[0][1]["apercu"], "https://t.jinka.fr/c/abc111")
        # résumé « en un coup d'œil »
        self.assertIn("🏠 Location (logement entier)", textes[0])
        self.assertRegex(textes[0], r"<b>(🟢 À saisir|🟡 Correct|🔴 Bof) · \d(,\d)?/5</b>")
        for critere in ("Emplacement", "Prix", "Logement", "Frais", "Transports"):
            self.assertIn(f"<code>{critere:<12}</code>", textes[0])
        self.assertIn("(vs votre budget)", textes[0])   # pas encore 5 annonces dans la zone
        self.assertIn("Emplacement </code>★★★★★ 5/5", ivry)  # zone prioritaire
        self.assertIn("€/m²", textes[0])
        self.assertIn("🚇 M7 · RER C", ivry)
        self.assertIn("0 € de frais d'agence", ivry)  # PAP = particulier
        # boutons : Postuler partout ; Brouillon si email d'agence, sinon « Déjà postulé sur le site »
        seloger = self.envoyes()[1][1]["boutons"]
        self.assertEqual([d.rsplit(":", 1)[0] for ligne in seloger for _, d in ligne],
                         ["postuler:4", "brouillon:4", "refuser:4"])
        self.assertEqual(self.envoyes()[0][1]["boutons"][0][0][1], self.d("postuler", 1))
        self.assertEqual(self.envoyes()[0][1]["boutons"][1][0][1], self.d("site", 1))
        self.m["envoyer_email"].assert_not_called()   # rien n'est envoyé sans appui
        self.m["creer_brouillon"].assert_not_called()

        # Appuis : Postuler (SeLoger) deux fois, Pas intéressé (Jinka), un intrus, J'ai postulé (Ivry)
        self.clics = [clic(1, self.d("postuler", 4)), clic(2, self.d("postuler", 4)), clic(3, self.d("refuser", 1)),
                      clic(4, self.d("site", 6), chat=666), clic(5, self.d("site", 6))]
        main.traiter_boutons(self.cfg, self.db, 0)
        self.m["envoyer_email"].assert_called_once()
        args = self.m["envoyer_email"].call_args[0]
        self.assertEqual(args[2], "contact@agence-dupont.fr")
        self.assertEqual(args[5], self.pdf)
        self.assertIn("880 € charges comprises", args[4])
        reponses = [c[0][2] if len(c[0]) > 2 else "" for c in self.m["repondre_clic"].call_args_list]
        self.assertIn("Dossier envoyé à contact@agence-dupont.fr ✅", reponses)
        self.assertTrue(any(r.startswith("Dossier déjà envoyé") for r in reponses))
        self.assertIn("Non autorisé.", reponses)
        # les appuis déjà traités ne sont pas rejoués
        main.traiter_boutons(self.cfg, self.db, 0)
        self.m["envoyer_email"].assert_called_once()

        lignes = {l["id"]: l for l in suivi._lire(self.cfg.fichier_csv)}
        self.assertEqual(lignes["4"]["statut"], "dossier envoyé")
        self.assertTrue(lignes["4"]["candidature"].startswith("email envoyé le"))
        self.assertEqual(lignes["1"]["statut"], "pas intéressé")
        self.assertEqual(lignes["6"]["statut"], "postulé")
        self.assertEqual(lignes["7"]["statut"], "nouveau")
        self.assertTrue(lignes["6"]["annonce"].startswith("⭐ Ivry-sur-Seine"))
        self.assertTrue(lignes["7"]["annonce"].startswith("Coloc – Montrouge"))
        self.assertIn(lignes["7"]["verdict"], ("🟢 À saisir", "🟡 Correct", "🔴 Bof"))
        self.assertRegex(lignes["7"]["note"], r"^\d(,\d)?/5$")

        # 2e passage : rien de nouveau
        main.boucle(self.cfg, self.db, une_fois=True)
        self.assertEqual(len(self.envoyes()), 4)

        # 48 h plus tard : rappels sauf pour « pas intéressé » ; boutons remis si pas de décision
        self.db.db.execute("UPDATE annonces SET notifiee_le='2000-01-01T00:00:00'")
        self.db.db.commit()
        main.boucle(self.cfg, self.db, une_fois=True)
        rappels = self.envoyes()[4:]
        self.assertEqual(len(rappels), 3)
        self.assertIn("relancez l'agence", rappels[0][0][2])
        self.assertIsNone(rappels[0][1]["boutons"])
        self.assertIn("pas encore répondu", rappels[2][0][2])
        self.assertEqual(rappels[2][1]["boutons"][0][0][1], self.d("postuler", 7))

    def test_mode_essai(self):
        self.cfg.email_reponse = "moi@perso.fr"
        main.boucle(self.cfg, self.db, une_fois=True)
        avant = len(self.envoyes())
        ids = self.db.passer_en_essai(2)   # les 2 dernières annonces écartées
        self.assertEqual(len(ids), 2)
        main.boucle(self.cfg, self.db, une_fois=True)
        essais = self.envoyes()[avant:]
        self.assertEqual(len(essais), 2)
        self.assertIn("🧪 <b>ESSAI</b>", essais[0][0][2])
        self.assertIn("moi@perso.fr", essais[0][0][2])
        # boutons d'envoi présents même sans email d'annonceur
        self.assertEqual(essais[0][1]["boutons"][0][0][1], self.d("postuler", ids[0]))
        self.clics = [clic(1, self.d("postuler", ids[0]))]
        main.traiter_boutons(self.cfg, self.db, 0)
        args = self.m["envoyer_email"].call_args[0]
        self.assertEqual(args[2], "moi@perso.fr")          # jamais l'annonceur
        self.assertTrue(args[3].startswith("[ESSAI] "))
        self.assertEqual(self.db.annonce(ids[0])["statut"], "dossier envoyé (essai)")
        # pas de rappel de relance pour les essais
        self.db.db.execute("UPDATE annonces SET notifiee_le='2000-01-01T00:00:00'")
        self.db.db.commit()
        self.assertTrue(all(not r["essai"] for r in self.db.a_relancer(48)))

    def test_seloger_hors_zone_accepte(self):
        self.cfg.sources_toutes_zones = {"seloger"}
        main.boucle(self.cfg, self.db, une_fois=True)
        # en plus des 4 habituelles : SeLoger Paris 17e (hors des zones du scénario) est gardée
        self.assertEqual(len(self.envoyes()), 5)
        self.assertTrue(any("Paris 17e" in c[0][2] for c in self.envoyes()))

    def test_photos_refusees_par_telegram(self):
        self.m["envoyer_photos"].side_effect = RuntimeError("Bad Request: wrong file identifier")
        main.boucle(self.cfg, self.db, une_fois=True)
        self.assertEqual(len(self.envoyes()), 4)  # le résumé part quand même
        self.assertEqual(self.envoyes()[3][1]["apercu"], "https://www.jinka.fr/c/coloc1")

    def test_brouillon_et_pdf_manquant(self):
        main.boucle(self.cfg, self.db, une_fois=True)
        self.pdf.unlink()
        self.clics = [clic(1, self.d("postuler", 4))]
        main.traiter_boutons(self.cfg, self.db, 0)
        self.m["envoyer_email"].assert_not_called()
        self.assertIn("Dossier PDF introuvable", self.m["repondre_clic"].call_args[0][2])
        self.clics = [clic(2, self.d("brouillon", 4))]
        main.traiter_boutons(self.cfg, self.db, 0)
        self.m["creer_brouillon"].assert_called_once()
        self.assertEqual(self.m["creer_brouillon"].call_args[0][2], "contact@agence-dupont.fr")
        self.assertEqual(self.db.annonce(4)["statut"], "brouillon créé")


class TestReseau(unittest.TestCase):
    def test_token_jamais_dans_les_erreurs(self):
        token = "123456:SECRET-TOKEN"
        panne = main.telegram.requests.ConnectionError(f"Max retries exceeded with url: /bot{token}/getUpdates")
        with mock.patch.object(main.telegram.requests, "post", side_effect=panne):
            with self.assertRaises(main.telegram.TelegramInjoignable) as ctx:
                main.telegram.mises_a_jour(token, 0, 1)
        self.assertNotIn("SECRET", str(ctx.exception))
        self.assertIsNone(ctx.exception.__cause__)
        self.assertTrue(ctx.exception.__suppress_context__)

    def test_coupure_signalee_une_seule_fois(self):
        etat = main.EtatReseau()
        with self.assertLogs("alerte-appart", "INFO") as logs:
            for _ in range(3):
                etat.erreur(OSError("réseau injoignable"))
            etat.ok()
        self.assertEqual(len(logs.output), 2)
        self.assertIn("Connexion Internet perdue", logs.output[0])
        self.assertIn("Connexion rétablie", logs.output[1])


class TestAnalyse(unittest.TestCase):
    def ligne(self, **k):
        base = {"texte": "", "source": "SeLoger", "prix_cc": 900, "surface": 30.0, "ville": "Paris 13e",
                "colocation": 0, "charges_incertaines": 0, "quartier": None}
        base.update(k)
        return base

    def test_details(self):
        d = analyse.extraire_details(
            "Appartement T2 meublé 30 m², 5e étage sans ascenseur, balcon, DPE : D, 900 € dont 60 € de charges, "
            "honoraires : 390 €, disponible le 01/12, métro Place d'Italie, RER C", "SeLoger")
        self.assertEqual((d.pieces, d.meuble, d.etage, d.ascenseur, d.dpe, d.charges, d.frais_agence, d.disponible),
                         (2, True, 5, False, "D", 60, 390, "01/12"))
        self.assertIn("balcon", d.atouts)
        self.assertEqual(d.transports_cites, ["Place d'Italie", "RER C"])
        self.assertEqual(analyse.extraire_details("Studio vide", "PAP").frais_agence, 0)
        self.assertEqual(analyse.extraire_details("Studio", "Jinka").pieces, 1)

    def test_verdict_et_comparaison(self):
        moins_cher = analyse.analyser(self.ligne(prix_cc=750), [30.0] * 6)   # 25 €/m² vs 30
        self.assertEqual(moins_cher.verdict, "🟢 À saisir")
        # Paris 13e : emplacement 4, prix -17 % : 5, logement 30 m² : 4, frais inconnus : 3, transports 5
        self.assertEqual(moins_cher.notes, {"Emplacement": 4, "Prix": 5, "Logement": 4, "Frais": 3,
                                            "Transports": 5})
        self.assertEqual(moins_cher.note_globale, 4.2)
        self.assertIn("17 % moins cher que la médiane", moins_cher.lignes[0])
        cher = analyse.analyser(self.ligne(prix_cc=900, surface=20.0), [30.0] * 6)  # 45 €/m²
        self.assertEqual(cher.notes["Prix"], 1)
        self.assertEqual(cher.verdict, "🟡 Correct")  # bien placé, mais cher
        self.assertIn("loyers encadrés ici : vérifiez le plafond légal", cher.contre)
        peu = analyse.analyser(self.ligne(), [30.0] * 2, prix_max=900)
        self.assertIn("comparaison dès 5 annonces", peu.lignes[0])
        self.assertEqual((peu.notes["Prix"], peu.repere_prix), (2, "vs votre budget"))
        self.assertIsNone(analyse.analyser(self.ligne(), []).notes["Prix"])

    def test_alertes_legales(self):
        r = analyse.analyser(self.ligne(texte="DPE G, honoraires 900 €", surface=8.0, prix_cc=500), [])
        self.assertEqual(r.verdict, "🔴 Bof")   # forcé, quelle que soit la note
        self.assertEqual(r.notes["Frais"], 1)
        self.assertEqual(r.notes["Logement"], 1)
        self.assertTrue(any("DPE G" in c for c in r.contre))
        self.assertTrue(any("plafond légal (~120 €)" in c for c in r.contre))
        self.assertTrue(any("minimum légal de 9 m²" in c for c in r.contre))

    def test_budget_et_revenus(self):
        r = analyse.analyser(self.ligne(texte="meublé, dont 50 € de charges", source="PAP"), [], revenu_net=2700)
        # 900 + dépôt 2 × 850 (meublé) + 0 € de frais (particulier)
        self.assertIn("💰 Entrée ≈ 2 600 €", "\n".join(r.lignes))
        self.assertIn("👛 33 % de vos revenus nets", r.lignes)
        self.assertEqual(r.notes["Frais"], 5)  # particulier


class TestGmail(unittest.TestCase):
    def test_email_reponse(self):
        pdf = Path(tempfile.mkdtemp()) / "dossier.pdf"
        pdf.write_bytes(b"%PDF")
        msg = main.gmail.construire_email("boite@gmail.com", "agence@x.fr", "Objet", "Corps", pdf,
                                          "perso@y.fr", "Hamza Test")
        self.assertEqual((msg["From"], msg["To"], msg["Cc"], msg["Reply-To"]),
                         ("Hamza Test <boite@gmail.com>", "agence@x.fr", "perso@y.fr", "perso@y.fr"))
        self.assertIsNone(msg["Bcc"])
        sans = main.gmail.construire_email("boite@gmail.com", "agence@x.fr", "Objet", "Corps", pdf)
        self.assertIsNone(sans["Reply-To"])
        self.assertIsNone(sans["Cc"])
        self.assertEqual(sans["From"], "boite@gmail.com")
        envoye = []

        class FauxSMTP:
            def __init__(self, *a, **k): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def login(self, *a): pass
            def send_message(self, m): envoye.append(m)
        with mock.patch.object(main.gmail.smtplib, "SMTP_SSL", FauxSMTP):
            main.gmail.envoyer_email("boite@gmail.com", "mdp", "agence@x.fr", "O", "C", pdf, "perso@y.fr")
        self.assertEqual((envoye[0]["Cc"], envoye[0]["Reply-To"]), ("perso@y.fr", "perso@y.fr"))

    def test_critere(self):
        c = main.gmail.critere_recherche(["jinka", "seloger", "pap"], 0)
        self.assertTrue(c.startswith('(OR OR FROM "jinka" FROM "seloger" FROM "pap") SINCE '))


if __name__ == "__main__":
    unittest.main()
