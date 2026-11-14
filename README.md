# Alerte appartement Paris

Petit outil Python qui tourne sur votre ordinateur et, **toutes les 2 minutes** :

1. lit les nouveaux emails d'alerte **Jinka, SeLoger et PAP** arrivés dans une boîte Gmail dédiée (IMAP) ;
2. extrait de chaque annonce : prix charges comprises, surface, ville ou arrondissement, lien, et email ou téléphone s'il y en a un ;
3. ne garde que les annonces **≤ 900 € CC** (et les **colocations ≤ 600 € CC**) dans **vos zones** (5 arrondissements et 15 communes de proche banlieue, réglables dans `.env`), signale par ⭐ vos **zones prioritaires**, et ignore les doublons ;
4. remplit votre modèle de candidature (`templates.md`) ;
5. vous envoie une **notification Telegram** avec l'aperçu de l'annonce (photo de l'alerte), son lien,
   le message prêt à copier et des **boutons** pour répondre ;
6. quand vous appuyez sur **✅ Postuler**, envoie l'email de candidature avec votre dossier PDF à l'agence
   (ou, au choix, prépare un **brouillon Gmail** pour le relire d'abord) ;
7. tient un fichier de suivi `suivi.csv` et vous **rappelle sur Telegram de relancer au bout de 48 h**.

**Garanties :**
- **Aucun scraping.** L'outil ne visite aucun site, il lit seulement vos emails.
- **Aucun envoi sans votre accord.** Un email ne part que lorsque **vous** appuyez sur « ✅ Postuler »
  sous une annonce, et jamais deux fois pour la même annonce. Les emails envoyés apparaissent dans
  « Messages envoyés » de la boîte dédiée.

### Les boutons Telegram

| Bouton | Ce qu'il fait |
|---|---|
| **✅ Postuler (envoyer le dossier)** | Envoie **immédiatement** le message de `templates.md` + `dossier.pdf` à l'email de l'annonce, depuis la boîte Gmail dédiée. Si `EMAIL_REPONSE` est rempli, cette adresse est **en copie (Cc)** et reçoit les réponses de l'agence ; `NOM_EXPEDITEUR` affiche votre nom comme expéditeur. Statut : `dossier envoyé`. Si le PDF est introuvable, rien n'est envoyé. |
| **📝 Brouillon Gmail** | Crée le même email dans vos **Brouillons** Gmail, pour le relire ou le modifier avant de l'envoyer vous-même. Statut : `brouillon créé`. |
| **✅ Postuler**, annonce **sans email** | Le bot vous demande l'email de l'agence (visible sur l'annonce) : répondez-lui en collant l'adresse, et le dossier part aussitôt. |
| **🌐 Déjà postulé sur le site** | (annonces sans email) Note que vous avez postulé via le formulaire du site. Statut : `postulé`. |
| **❌ Pas intéressé** | **Supprime l'annonce de Telegram** (résumé + photos) et la classe : plus de rappel. Statut : `pas intéressé`. Au-delà de 48 h, Telegram n'autorise plus la suppression : les boutons sont alors remplacés. |

Après un appui, les boutons sont remplacés par la décision (ex. « ✅ Dossier envoyé le 14/11 à 10:32 »).
Seuls les appuis venant de **votre** compte Telegram (`TELEGRAM_CHAT_ID`) sont pris en compte.
Les boutons réagissent en quelques secondes tant que `python main.py` tourne.

**Photos.** Toutes les photos présentes dans l'email d'alerte sont envoyées en album juste avant le résumé
(souvent 1 à 3 : les sites n'en mettent pas plus dans leurs emails). Sans photo, Telegram affiche l'aperçu de
la page de l'annonce. Pour toutes les photos et la description complète : « Voir l'annonce ».
L'outil lui-même ne visite aucun site.

### Le résumé « est-ce que ça vaut le coup ? »

Chaque notification commence par un verdict et une **note globale sur 5**, puis 5 notes détaillées :

| Note | Comment elle est calculée |
|---|---|
| **Emplacement** | Vos priorités : 5 · Paris intramuros : 4 · proche banlieue : 3 · options secondaires : 2 · +1 si un quartier de votre liste est cité |
| **Prix** | Écart avec la médiane €/m² de la zone : ≤ −15 % : 5 · ≤ −5 % : 4 · ± 5 % : 3 · ≤ +15 % : 2 · au-delà : 1. Tant qu'il y a moins de 5 annonces vues dans la zone, prix comparé à votre budget (« vs votre budget ») |
| **Logement** | Base 3 · +1 extérieur · +1 grande surface (≥ 30 m², chambre ≥ 12 m²) · +1 bon DPE (A-C) · +0,5 rénové/lumineux/calme · −1 petite surface, DPE F, étage élevé sans ascenseur · −2 DPE G ou < 9 m² |
| **Frais** | 0 € (particulier) : 5 · ≤ moitié du plafond légal : 4 · inconnus : 3 · proches du plafond : 2 · au-dessus du plafond : 1 |
| **Transports** | Métros + RER : 5 · métro + train : 4 · une seule ligne de métro : 3 · RER/train/tram seulement : 2 · +1 si une station est citée |

Verdict : **🟢 À saisir** si la note globale ≥ 4, **🔴 Bof** si < 3 ou en cas de problème grave
(DPE G, moins de 9 m², frais au-dessus du plafond légal), sinon **🟡 Correct**. Les tableaux de notes
sont dans `analyse.py` (`EMPLACEMENT`, `NOTE_TRANSPORTS`) si vous voulez les ajuster.

Viennent ensuite :

| Ligne | Contenu |
|---|---|
| Type | 🏠 Location (logement entier) ou 👥 Colocation, et le site d'origine |
| 💶 | Prix au m², comparé à la **médiane des annonces déjà reçues dans la même zone** (dès 5 annonces vues) |
| 🧾 | Charges (si indiquées), frais d'agence indiqués, ou **plafond légal** (loi ALUR : 15 €/m² max en zone très tendue) ; 0 € chez un particulier (PAP) |
| 💰 | Budget d'entrée estimé : 1er loyer + dépôt de garantie (1 mois hors charges, 2 si meublé) + frais |
| 👛 | Part du loyer dans vos revenus, si `REVENU_NET_MENSUEL` est rempli dans `.env` |
| 🏢 ✨ | Pièces, meublé/vide, étage, ascenseur, DPE, disponibilité, atouts (balcon, parking…) quand l'annonce les cite |
| 🚇 | Principales lignes de métro/RER/tram de la zone (repère indicatif) + stations citées dans l'annonce |
| ✅ / ⚠️ | Les raisons du verdict : prix sous/au-dessus de la médiane, sans frais, DPE F/G, surface < 9 m², étage élevé sans ascenseur, frais au-dessus du plafond… |

Ces informations viennent uniquement du texte de l'email : si l'alerte ne mentionne pas le DPE ou l'étage,
ils n'apparaissent pas. Le verdict est un tri rapide, pas un avis définitif : regardez toujours l'annonce.

**Colocation.** Une annonce est considérée comme une colocation si son texte (ou l'objet de l'email d'alerte)
contient « colocation », « coloc » ou « colocataire ». Elle est alors acceptée jusqu'à `PRIX_MAX_COLOC` (600 €),
dans les mêmes zones, et le message utilisé est celui de la section « Message colocation » de `templates.md`.

---

## Sommaire

1. [Installer Python et l'outil](#1-installer-python-et-loutil)
2. [Préparer la boîte Gmail dédiée](#2-préparer-la-boîte-gmail-dédiée)
3. [Créer le mot de passe d'application Gmail](#3-créer-le-mot-de-passe-dapplication-gmail)
4. [Créer le bot Telegram](#4-créer-le-bot-telegram)
5. [Remplir le fichier .env](#5-remplir-le-fichier-env)
6. [Personnaliser le message et ajouter votre dossier PDF](#6-personnaliser-le-message-et-ajouter-votre-dossier-pdf)
7. [Tester puis lancer](#7-tester-puis-lancer)
8. [Utiliser le fichier de suivi](#8-utiliser-le-fichier-de-suivi)
9. [Bon à savoir et dépannage](#9-bon-à-savoir-et-dépannage)

---

## 1. Installer Python et l'outil

1. Installez **Python 3.9 ou plus récent** :
   - **Windows** : téléchargez-le sur <https://www.python.org/downloads/> et, pendant l'installation, **cochez « Add Python to PATH »**.
   - **macOS** : téléchargez-le sur <https://www.python.org/downloads/> (ou `brew install python`).
   - **Linux** : il est généralement déjà installé.
2. Copiez le dossier `alerte-appart` où vous voulez sur votre ordinateur.
3. Ouvrez un terminal **dans ce dossier** :
   - Windows : clic droit dans le dossier → « Ouvrir dans le terminal » ;
   - macOS : clic droit sur le dossier → « Nouveau terminal au dossier ».
4. Créez un environnement Python isolé et installez les dépendances :

   **macOS / Linux :**
   ```bash
   python3 -m venv .venv
   ```
   ```bash
   source .venv/bin/activate
   ```
   ```bash
   pip install -r requirements.txt
   ```

   **Windows (PowerShell) :**
   ```bash
   py -m venv .venv
   ```
   ```bash
   .venv\Scripts\Activate.ps1
   ```
   ```bash
   pip install -r requirements.txt
   ```
   > Si PowerShell refuse d'activer l'environnement, lancez une fois
   > `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` puis réessayez.

À chaque fois que vous rouvrez un terminal, réactivez l'environnement (`source .venv/bin/activate` ou `.venv\Scripts\Activate.ps1`).

---

## 2. Préparer la boîte Gmail dédiée

1. Créez (si ce n'est pas déjà fait) une adresse Gmail réservée à la recherche, par ex. `prenom.appart@gmail.com`.
2. Sur **Jinka**, **SeLoger** et **PAP**, créez vos alertes email avec cette adresse
   (location, budget 900 €, vos villes) : suivez le guide pas à pas **[ALERTES.md](ALERTES.md)**.
3. Vérifiez que les alertes arrivent bien dans la **Boîte de réception** (pas en Spam ni en Promotions filtrées ailleurs).
   Si elles arrivent dans un autre libellé, indiquez-le dans `DOSSIER_IMAP` (voir étape 5).

> Depuis 2025, Gmail active IMAP automatiquement. Si vous voyez encore l'option dans
> Gmail → ⚙️ → *Voir tous les paramètres* → *Transfert et POP/IMAP*, vérifiez qu'IMAP est **activé**.

---

## 3. Créer le mot de passe d'application Gmail

Google n'autorise pas un programme à utiliser votre mot de passe habituel. Il faut un
**mot de passe d'application** (16 lettres), qui ne sert qu'à cet outil et peut être supprimé à tout moment.

1. Connectez-vous à la boîte Gmail dédiée, puis allez sur <https://myaccount.google.com/security>.
2. **Activez la validation en deux étapes** (rubrique « Comment vous connecter à Google »).
   C'est obligatoire : sans elle, les mots de passe d'application n'existent pas.
3. Allez sur <https://myaccount.google.com/apppasswords>
   (ou tapez « mots de passe des applications » dans la barre de recherche de votre compte Google).
4. Donnez un nom à l'application, par ex. `Alerte appart`, puis cliquez sur **Créer**.
5. Google affiche un code de 16 lettres du type `abcd efgh ijkl mnop`. **Copiez-le tout de suite**
   (il ne sera plus jamais affiché) : il ira dans `GMAIL_MOT_DE_PASSE_APPLICATION`.

> Si la page « mots de passe des applications » est introuvable : la validation en deux étapes
> n'est pas activée, ou le compte est géré par une entreprise/école qui a bloqué la fonction.
> Utilisez alors un compte Gmail personnel.

---

## 4. Créer le bot Telegram

1. Installez Telegram sur votre téléphone (et éventuellement votre ordinateur).
2. Dans la recherche Telegram, ouvrez **@BotFather** (le compte officiel, avec la coche bleue).
3. Envoyez-lui `/newbot`.
4. Il demande un **nom** (affiché) : par ex. `Mon alerte appart`.
5. Il demande un **identifiant** qui doit finir par `bot` : par ex. `alerte_appart_prenom_bot`.
6. BotFather répond avec un **token** du type `123456789:AAH...`. Copiez-le : il ira dans `TELEGRAM_BOT_TOKEN`.
   Ne le partagez avec personne (quiconque l'a peut contrôler votre bot).
7. Cliquez sur le lien de votre bot (`t.me/alerte_appart_prenom_bot`), appuyez sur **Démarrer**
   et envoyez-lui un message, par ex. `bonjour`. (Un bot ne peut pas vous écrire tant que vous ne lui avez pas écrit.)
8. Récupérez votre **chat ID** : mettez d'abord le token dans `.env` (étape 5), puis lancez :
   ```bash
   python main.py --trouver-chat-id
   ```
   Il affiche une ligne `TELEGRAM_CHAT_ID=123456789` : recopiez-la dans `.env`.

   *Méthode manuelle :* ouvrez dans votre navigateur
   `https://api.telegram.org/bot<VOTRE_TOKEN>/getUpdates` et relevez le nombre après `"chat":{"id":`.

---

## 5. Remplir le fichier .env

1. Copiez `.env.example` en `.env` (dans le même dossier).
   - macOS / Linux : `cp .env.example .env`
   - Windows : `copy .env.example .env`
   > Sous Windows/macOS les fichiers commençant par un point peuvent être masqués :
   > ouvrez-les depuis votre éditeur de texte (Bloc-notes, TextEdit, VS Code…).
2. Remplissez au minimum :

   | Variable | Exemple | Rôle |
   |---|---|---|
   | `GMAIL_ADRESSE` | `prenom.appart@gmail.com` | la boîte dédiée |
   | `GMAIL_MOT_DE_PASSE_APPLICATION` | `abcd efgh ijkl mnop` | étape 3 (les espaces sont acceptés) |
   | `TELEGRAM_BOT_TOKEN` | `123456789:AAH...` | étape 4 |
   | `TELEGRAM_CHAT_ID` | `123456789` | étape 4 |

3. Les autres réglages ont déjà les bonnes valeurs, modifiables si besoin :
   `PRIX_MAX_CC=900`, `PRIX_MAX_COLOC=600`, `REVENU_NET_MENSUEL=…` (facultatif), `ZONES=…` (vos 20 zones), `PRIORITES=…` (vos 5 priorités, marquées ⭐),
   `RELANCE_HEURES=48`, `INTERVALLE_SECONDES=120`, `DOSSIER_PDF=dossier.pdf`, `DOSSIER_IMAP=INBOX`.

   `SOURCES_TOUTES_ZONES=seloger,jinka` : toutes les annonces de ces sites sont gardées quel que soit le lieu
   (pratique avec une alerte « à 30 min de… ») ; seuls les prix max s'appliquent.

   Dans `ZONES` et `PRIORITES`, écrivez `Paris` pour les 20 arrondissements, `Paris 13e` pour un seul, ou le nom d'une commune
   (`Ivry-sur-Seine`, `Asnieres`… les accents et majuscules ne comptent pas). Les communes reconnues sont
   listées dans `zones.py` ; pour en ajouter une, ajoutez une ligne dans `COMMUNES` (nom + code postal).
   Si un nom n'est pas reconnu, l'outil le dit au démarrage.

Le fichier `.env` contient vos secrets : ne l'envoyez à personne et ne le publiez pas.

---

## 6. Personnaliser le message et ajouter votre dossier PDF

1. Ouvrez `templates.md` et adaptez le texte sous **`## Objet`** et **`## Message`**
   (présentation, revenus, garant, signature…). Les variables `{prix}`, `{surface}`,
   `{ville}`, `{quartier}`, `{lien}`, `{source}`, `{contact}` sont remplacées automatiquement,
   ainsi que `{mon_email}` (votre `EMAIL_REPONSE`, à mettre par exemple dans la signature).
   Le fichier est relu à chaque annonce : pas besoin de relancer l'outil après une modification.
2. Fusionnez votre dossier de location en **un seul PDF** et placez-le dans le dossier du projet
   sous le nom `dossier.pdf` (ou indiquez son chemin dans `DOSSIER_PDF`).
   Pensez à masquer les informations non nécessaires (ex. filigrane « Document exclusivement destiné à la location »).

---

## 7. Tester puis lancer

1. **Test de connexion** (Gmail, Telegram, modèle, PDF) :
   ```bash
   python main.py --test
   ```
   Vous devez recevoir « ✅ Test réussi » sur Telegram.

2. **Vérifier l'extraction** sur vos vrais emails des 2 derniers jours (n'envoie rien, n'enregistre rien) :
   ```bash
   python main.py --analyser
   ```
   Chaque annonce trouvée s'affiche avec ✅ (gardée) ou ❌ (et la raison).

3. **Lancer l'outil** :
   ```bash
   python main.py
   ```
   Laissez le terminal ouvert : l'outil vérifie la boîte toutes les 2 minutes. `Ctrl+C` pour l'arrêter.
   Au premier lancement, il traite les alertes des 2 derniers jours (`JOURS_HISTORIQUE`).

   Pour un seul passage (utile pour essayer) : `python main.py --une-fois`

   **Mode essai** : `python main.py --essai` notifie les 3 dernières annonces **écartées** (`--essai 5` pour 5),
   avec un bandeau 🧪 ESSAI. Vous pouvez tout tester sur de vraies annonces : « Postuler » et « Brouillon »
   envoient alors le dossier **à votre adresse** (`EMAIL_REPONSE`, sinon la boîte dédiée), jamais à
   l'annonceur, avec un objet commençant par « [ESSAI] ». Pas de rappel de relance pour ces annonces.

> L'outil ne tourne que lorsque l'ordinateur est allumé et non en veille. Sur un portable,
> désactivez la mise en veille automatique pendant vos recherches, ou lancez-le le matin.
> Les emails arrivés pendant une coupure sont traités au redémarrage (jusqu'à `JOURS_HISTORIQUE` jours).

---

## 8. Utiliser le fichier de suivi

`suivi.csv` (ouvrable avec Excel, LibreOffice ou Numbers) contient une ligne par annonce notifiée :

| id | date | source | annonce | lien | contact | candidature | statut | relance_le |
|---|---|---|---|---|---|---|---|---|

- La colonne **statut** se remplit toute seule quand vous utilisez les boutons Telegram
  (`dossier envoyé`, `brouillon créé`, `postulé`, `pas intéressé`). Vous pouvez aussi la modifier vous-même :
  `visite prévue`, `refusé`…
- **48 h** après la notification, vous recevez un rappel Telegram (« relancez l'agence » si le dossier
  est parti, ou les boutons de nouveau si vous n'avez pas encore répondu).
  Il n'est **pas** envoyé si le statut est `refusé`, `abandonné`, `loué`, `ignoré`, `pas intéressé` ou `dossier accepté`.
- Le statut `nouveau` passe automatiquement à `à relancer` lors du rappel.

> **Fermez le fichier dans Excel** après l'avoir modifié : sous Windows, Excel le verrouille
> et l'outil attendra le cycle suivant pour y écrire. Enregistrez bien au format CSV.

---

## 9. Bon à savoir et dépannage

**Comment l'extraction fonctionne.** Les alertes contiennent souvent plusieurs annonces. L'outil isole
chaque « carte » d'annonce dans l'email puis y cherche le prix, la surface (`m²`), la ville
et un éventuel email ou téléphone.

**Comment la ville est reconnue** (`zones.py`) : d'abord par le **code postal** (`75013`, `94200`…), sinon
par le nom (`Paris 13e`, `Ivry-sur-Seine`…) en gardant la première mention. Les homonymes sont écartés
(Clichy-sous-Bois, Asnières-sur-Oise…), ainsi que les rues parisiennes (« rue de Charenton », « Porte d'Ivry »).
Les **quartiers** ne sont pas filtrés, car les emails d'alerte les donnent rarement : s'ils sont cités, ils sont affichés
dans la notification (« 📌 Quartier repéré : Alésia »). Si un site change la mise en page de ses emails,
lancez `python main.py --analyser` pour voir ce qui est compris.

**Charges.** Si l'annonce indique « HC » avec le montant des charges, il est ajouté au loyer.
Si elle indique « HC » sans montant, l'annonce est gardée (si le loyer HC ≤ 900 €) mais signalée par ⚠️ :
vérifiez le total vous-même. Si rien n'est indiqué, le prix est considéré comme charges comprises.

**Contacts.** Les emails d'alerte contiennent rarement l'email ou le téléphone de l'annonceur
(il faut souvent passer par le site). Dans ce cas, pas d'envoi possible : copiez le message reçu sur Telegram
dans le formulaire de contact de l'annonce, puis appuyez sur « 🌐 Déjà postulé sur le site ». Si l'annonce affiche un email, appuyez plutôt sur « ✅ Postuler » et donnez-le au bot.

**Doublons.** Une annonce est considérée comme déjà vue si son lien est identique, ou si elle a la même
ville, le même prix et la même surface (la même annonce reçue via Jinka et SeLoger n'est notifiée qu'une fois).
Pour repartir de zéro, arrêtez l'outil et supprimez `annonces.db`.

**Les emails ne sont pas marqués comme lus** : l'outil lit en lecture seule.

| Problème | Solution |
|---|---|
| `[AUTHENTICATIONFAILED] Invalid credentials` | Mot de passe d'application erroné ou révoqué : recréez-le (étape 3). N'utilisez pas votre mot de passe Gmail normal. |
| `Il manque dans le fichier .env : …` | Le fichier s'appelle-t-il bien `.env` (et pas `.env.txt`) ? Est-il dans le même dossier que `main.py` ? |
| Telegram : `chat not found` | Vous n'avez pas encore écrit à votre bot (étape 4.7), ou le chat ID est faux. |
| Telegram : `Unauthorized` | Token erroné : recopiez-le depuis @BotFather (`/mybots` → votre bot → *API Token*). |
| Aucune annonce trouvée | Lancez `--analyser`. Vérifiez que les alertes sont dans la boîte de réception et que l'adresse de l'expéditeur contient bien `jinka`, `seloger` ou `pap` (sinon ajoutez le mot dans `EXPEDITEURS`). |
| « Dossier PDF introuvable » en appuyant sur Postuler | Vérifiez le chemin `DOSSIER_PDF` dans `.env`. |
| Les boutons ne réagissent pas | `python main.py` doit être en train de tourner sur votre ordinateur. |
| « 🔌 Connexion Internet perdue » (ex. `WinError 10051`) | L'ordinateur n'a plus accès à Internet : Wi-Fi coupé, sortie de veille, VPN ou réseau d'entreprise qui bloque Telegram. L'outil réessaie tout seul et affiche « Connexion rétablie ». Si ça dure, ouvrez https://api.telegram.org dans le navigateur : si la page ne s'affiche pas, coupez le VPN ou changez de réseau. |
| Votre token Telegram a été affiché ou partagé | Envoyez `/revoke` à @BotFather, choisissez votre bot, puis mettez le nouveau token dans `.env` et relancez l'outil. |
| Erreur `SMTPAuthenticationError` | Même cause que l'erreur IMAP : mot de passe d'application erroné. |

**Tests automatiques** (sans connexion internet) : `python -m unittest discover tests`

---

## Organisation du code

| Fichier | Rôle |
|---|---|
| `main.py` | programme principal : boucle toutes les 2 min et enchaîne les étapes |
| `config.py` | lecture du fichier `.env` |
| `gmail.py` | lecture des alertes (IMAP), brouillons, envoi des candidatures après « Postuler » (SMTP) |
| `extraction.py` | découpage des emails en annonces, extraction prix/surface/contact, filtre |
| `analyse.py` | résumé et verdict de chaque annonce (prix au m², frais, budget, transports…) |
| `zones.py` | vos communes et arrondissements : reconnaissance de la ville et des quartiers |
| `stockage.py` | base SQLite `annonces.db` (doublons, état des notifications et relances) |
| `modele.py` | remplissage de `templates.md` |
| `telegram.py` | messages Telegram, boutons et lecture de vos appuis |
| `suivi.py` | fichier `suivi.csv` |
