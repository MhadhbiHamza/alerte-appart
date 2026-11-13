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
from config import Config  # noqa: E402
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
        resume = [(a.prix_cc, a.surface, a.arrondissement, a.lien) for a in annonces]
        self.assertEqual(resume, [
            (880, 32.0, 11, "https://t.jinka.fr/c/abc111"),
            (1150, 28.0, 14, "https://t.jinka.fr/c/abc222"),
            (790, 22.0, 20, "https://t.jinka.fr/c/abc333"),
        ])
        self.assertTrue(all(a.source == "Jinka" for a in annonces))

    def test_seloger_charges_et_contact(self):
        a1, a2 = extraction.extraire_annonces(email_de("SeLoger <alertes@mail.seloger.com>", HTML_SELOGER))
        self.assertEqual((a1.prix_cc, a1.surface, a1.arrondissement), (880, 35.0, 15))
        self.assertEqual(a1.email_contact, "contact@agence-dupont.fr")
        self.assertEqual(a1.telephone, "01 45 67 89 10")
        self.assertEqual(a1.source, "SeLoger")
        self.assertEqual((a2.prix_cc, a2.arrondissement, a2.charges_incertaines), (700, 17, True))
        self.assertIsNone(a2.email_contact)  # no-reply@seloger.com ignoré

    def test_pap_texte_brut(self):
        (a,) = extraction.extraire_annonces(email_de("PAP <alerte@pap.fr>", texte=TEXTE_PAP))
        self.assertEqual((a.prix_cc, a.surface, a.arrondissement), (890, 30.0, 18))
        self.assertEqual(a.lien, "https://www.pap.fr/annonces/appartement-paris-18e-r123456")
        self.assertEqual(a.source, "PAP")

    def test_prix(self):
        self.assertEqual(extraction.extraire_prix("Loyer 800 € HC, soit 850 € CC"), (850, False))
        self.assertEqual(extraction.extraire_prix("75011 1 050 € CC"), (1050, False))
        self.assertEqual(extraction.extraire_prix("850,00 € charges comprises"), (850, False))
        self.assertEqual(extraction.extraire_prix("Charges : 40 € — loyer 760 € HC"), (800, False))

    def test_arrondissements(self):
        for texte, n in [("Paris 1er", 1), ("75116", 16), ("Paris 17ème", 17), ("11e arrondissement", 11),
                         ("Paris 14 - 25 m²", 14), ("Appartement Paris 2 pièces", None)]:
            self.assertEqual(extraction.extraire_arrondissement(texte), n, texte)

    def test_filtre(self):
        a = extraction.Annonce("Jinka", "l", "", prix_cc=900, surface=20, arrondissement=18)
        self.assertTrue(extraction.est_eligible(a, 900, {11, 14, 15, 17, 18})[0])
        a.prix_cc = 901
        self.assertFalse(extraction.est_eligible(a, 900, {11, 14, 15, 17, 18})[0])


class TestModele(unittest.TestCase):
    def test_remplissage(self):
        objet, message = modele.charger(Path(__file__).parent.parent / "templates.md")
        v = {"prix": "880", "surface": "32", "arrondissement": "11e", "lien": "https://x"}
        self.assertEqual(modele.remplir(objet, v), "Candidature – Appartement 32 m² Paris 11e – 880 € CC")
        self.assertIn("880 € charges comprises (https://x)", modele.remplir(message, v))
        self.assertEqual(modele.remplir("{inconnue}", v), "{inconnue}")


class TestCycleComplet(unittest.TestCase):
    """Simule un cycle complet avec Gmail et Telegram remplacés par des faux."""

    def test_cycle(self):
        tmp = Path(tempfile.mkdtemp())
        pdf = tmp / "dossier.pdf"
        pdf.write_bytes(b"%PDF-1.4 test")
        cfg = Config("moi@gmail.com", "x", "INBOX", "tok", "42", 900, {11, 14, 15, 17, 18},
                     ["jinka", "seloger", "pap"], pdf, 120, 48, 2,
                     Path(__file__).parent.parent / "templates.md", tmp / "suivi.csv", tmp / "a.db")
        db = Stockage(cfg.fichier_db)
        emails = [
            ("INBOX:1:1", email_de("Jinka <alerte@jinka.fr>", HTML_JINKA)),
            ("INBOX:1:2", email_de("SeLoger <alertes@mail.seloger.com>", HTML_SELOGER)),
            # même annonce que la 1re de Jinka, reçue via PAP : doit être ignorée (doublon)
            ("INBOX:1:3", email_de("PAP <alerte@pap.fr>",
                                   texte="Paris 11e - 32 m²\n880 € CC\nhttps://www.pap.fr/annonces/r999")),
        ]

        def faux_lire(imap, dossier, exp, jours, deja_traite=None):
            return [(c, m) for c, m in emails if not (deja_traite and deja_traite(c))]

        with mock.patch.object(main.gmail, "connexion"), \
             mock.patch.object(main.gmail, "lire_emails", side_effect=faux_lire), \
             mock.patch.object(main.gmail, "creer_brouillon") as brouillon, \
             mock.patch.object(main.telegram, "envoyer") as envoyer:
            main.cycle(cfg, db)
            # 3 annonces retenues : Jinka 11e 880 €, SeLoger 15e 820+60 €,
            # SeLoger 17e 700 € HC (retenue, mais signalée « charges inconnues »)
            self.assertEqual(envoyer.call_count, 3)
            self.assertEqual(brouillon.call_count, 1)  # seule l'annonce SeLoger a un email d'agence
            args = brouillon.call_args[0]
            self.assertEqual(args[2], "contact@agence-dupont.fr")
            self.assertEqual(args[5], pdf)
            self.assertIn("<pre>", envoyer.call_args_list[0][0][2])

            # 2e cycle : rien de nouveau
            main.cycle(cfg, db)
            self.assertEqual(envoyer.call_count, 3)

            lignes = suivi._lire(cfg.fichier_csv)
            self.assertEqual(len(lignes), 3)
            self.assertEqual(lignes[0]["statut"], "nouveau")

            # Relance 48 h plus tard : on vieillit les dates, et on clôt une candidature
            db.db.execute("UPDATE annonces SET notifiee_le='2000-01-01T00:00:00'")
            db.db.commit()
            lignes[2]["statut"] = "Refusé"
            suivi._ecrire(cfg.fichier_csv, lignes)
            main.cycle(cfg, db)
            self.assertEqual(envoyer.call_count, 5)  # 2 rappels, pas pour la candidature refusée
            self.assertIn("relancer", envoyer.call_args[0][2])
            self.assertEqual(suivi._lire(cfg.fichier_csv)[0]["statut"], "à relancer")


class TestGmail(unittest.TestCase):
    def test_critere(self):
        c = main.gmail.critere_recherche(["jinka", "seloger", "pap"], 0)
        self.assertTrue(c.startswith('(OR OR FROM "jinka" FROM "seloger" FROM "pap") SINCE '))


if __name__ == "__main__":
    unittest.main()
