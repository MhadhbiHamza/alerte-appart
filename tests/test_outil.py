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
            ("Levallois-Perret 92300", "autre commune (92300)"),
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
        self.assertEqual(len(_zones(config.ZONES_PAR_DEFAUT, "ZONES")), 20)
        with self.assertRaises(SystemExit):
            _zones("Paris 13e, Levallois", "ZONES")

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
                          _zones(config.ZONES_PAR_DEFAUT, "ZONES"), {"Ivry-sur-Seine"},
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

    def test_parcours_complet(self):
        main.boucle(self.cfg, self.db, une_fois=True)
        # n° des annonces : rejetées comprises (SeLoger 15e = 4, Ivry = 6, coloc Montrouge = 7)
        # 4 retenues : Jinka 11e 880 €, SeLoger 15e 820+60 €, PAP Ivry 870 €, coloc Montrouge 580 €
        # (SeLoger 17e hors zone, Jinka 14e trop cher, Jinka 20e hors zone, coloc 15e à 650 € > 600 €)
        self.assertEqual(len(self.envoyes()), 4)
        textes = [c[0][2] for c in self.envoyes()]
        ivry, coloc = textes[2], textes[3]
        self.assertIn("⭐", ivry)
        self.assertIn("📍 Ivry-sur-Seine · 33 m² · <b>870 € CC</b>", ivry)
        self.assertIn("Quartier repéré : Mairie d'Ivry", ivry)
        self.assertIn("à Ivry-sur-Seine, proposé à 870 €", ivry)
        self.assertNotIn("⭐", textes[0])
        self.assertIn("👥 Colocation", coloc)
        self.assertIn("chambre en colocation à Montrouge", coloc)
        # aperçu = photo de l'email si présente, sinon le lien de l'annonce
        self.assertEqual(self.envoyes()[3][1]["apercu"], "https://img.jinka.fr/photos/coloc1.jpg")
        self.assertEqual(self.envoyes()[0][1]["apercu"], "https://t.jinka.fr/c/abc111")
        # boutons : Postuler/Brouillon/Pas intéressé si email d'agence, sinon « J'ai postulé »
        seloger = self.envoyes()[1][1]["boutons"]
        self.assertEqual([d for ligne in seloger for _, d in ligne], ["postuler:4", "brouillon:4", "refuser:4"])
        self.assertEqual(self.envoyes()[0][1]["boutons"][0][0][1], "postuler:1")
        self.m["envoyer_email"].assert_not_called()   # rien n'est envoyé sans appui
        self.m["creer_brouillon"].assert_not_called()

        # Appuis : Postuler (SeLoger) deux fois, Pas intéressé (Jinka), un intrus, J'ai postulé (Ivry)
        self.clics = [clic(1, "postuler:4"), clic(2, "postuler:4"), clic(3, "refuser:1"),
                      clic(4, "postuler:6", chat=666), clic(5, "postuler:6")]
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
        self.assertEqual(rappels[2][1]["boutons"][0][0][1], "postuler:7")

    def test_brouillon_et_pdf_manquant(self):
        main.boucle(self.cfg, self.db, une_fois=True)
        self.pdf.unlink()
        self.clics = [clic(1, "postuler:4")]
        main.traiter_boutons(self.cfg, self.db, 0)
        self.m["envoyer_email"].assert_not_called()
        self.assertIn("Dossier PDF introuvable", self.m["repondre_clic"].call_args[0][2])
        self.clics = [clic(2, "brouillon:4")]
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


class TestGmail(unittest.TestCase):
    def test_critere(self):
        c = main.gmail.critere_recherche(["jinka", "seloger", "pap"], 0)
        self.assertTrue(c.startswith('(OR OR FROM "jinka" FROM "seloger" FROM "pap") SINCE '))


if __name__ == "__main__":
    unittest.main()
