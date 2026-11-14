# Héberger l'outil gratuitement (24 h/24, sans votre PC)

L'outil a besoin d'un petit ordinateur **allumé en permanence** : il écoute Gmail et vos boutons Telegram,
et garde sa base d'annonces. La solution gratuite la plus solide est un serveur **Oracle Cloud
« Always Free »** : un vrai serveur Linux, gratuit sans limite de durée, largement assez puissant.

Comptez **30 à 45 minutes** la première fois. Ensuite, plus rien à faire (sauf les mises à jour, 1 commande).

> **Ce qui reste sur votre PC** : rien d'obligatoire. Vous gardez le dossier pour modifier le code ou
> votre message, et vous envoyez les changements au serveur (voir « Au quotidien »).

---

## Étape 1 — Créer le compte Oracle Cloud (gratuit)

1. Allez sur <https://www.oracle.com/cloud/free/> et cliquez sur **Start for free**.
2. Remplissez le formulaire. Pour la **Home Region**, choisissez **France Central (Paris)** ou
   **France South (Marseille)** : elle ne pourra plus être changée, et les ressources gratuites n'existent
   que dans cette région.
3. Une **carte bancaire** est demandée pour vérifier votre identité. Vous n'êtes **pas débité** tant que vous
   n'utilisez que des ressources marquées **« Always Free eligible »** (c'est notre cas).
4. Attendez l'email de confirmation (de quelques minutes à quelques heures).

> ⚠️ **Serveurs inactifs.** Oracle peut arrêter les serveurs gratuits qui ne font presque rien pendant
> 7 jours, et cet outil est très léger. Pour éviter ça, passez votre compte en **« Pay As You Go »**
> (menu ☰ → *Billing & Cost Management* → *Upgrade and Manage Payment*) : tant que vous n'utilisez que
> les ressources « Always Free », la facture reste à **0 €**. Sinon, le **☀️ bilan du matin** sur Telegram
> vous préviendra : s'il n'arrive pas, le serveur a été arrêté (redémarrez-le dans la console Oracle).

## Étape 2 — Créer le serveur

1. Connectez-vous à <https://cloud.oracle.com>, puis menu ☰ → **Compute** → **Instances** → **Create instance**.
2. **Name** : `alerte-appart`.
3. **Image and shape** → *Edit* :
   - **Image** : *Change image* → **Canonical Ubuntu** (24.04).
   - **Shape** : *Change shape* → **VM.Standard.E2.1.Micro** (AMD), ou *Ampere* → **VM.Standard.A1.Flex**
     avec 1 OCPU et 6 Go. Les deux portent la mention **Always Free eligible**.
4. **Networking** : laissez les valeurs par défaut (une adresse IP publique est attribuée).
5. **Add SSH keys** : choisissez **Generate a key pair for me** puis **Save private key**. Un fichier du type
   `ssh-key-2026-11-15.key` est téléchargé : **gardez-le précieusement**, c'est la clé du serveur.
6. Cliquez sur **Create**. Quand l'état passe à **Running**, notez l'**adresse IP publique** (ex. `141.145.12.34`).

> Si Oracle affiche « Out of capacity » pour Ampere, prenez **VM.Standard.E2.1.Micro**, ou réessayez plus tard.

## Étape 3 — Se connecter au serveur depuis Windows

Dans **PowerShell** (pas besoin d'installer quoi que ce soit, Windows 10/11 a déjà `ssh`) :

1. Rangez la clé et protégez-la (remplacez le nom du fichier par le vôtre) :
   ```bash
   mkdir $HOME\.ssh -Force; Move-Item $HOME\Downloads\ssh-key-*.key $HOME\.ssh\oracle.key
   ```
   ```bash
   icacls $HOME\.ssh\oracle.key /inheritance:r /grant:r "$($env:USERNAME):(R)"
   ```
2. Connectez-vous (remplacez l'adresse IP par la vôtre) :
   ```bash
   ssh -i $HOME\.ssh\oracle.key ubuntu@141.145.12.34
   ```
   Répondez `yes` à la première question. Vous êtes sur le serveur quand l'invite devient `ubuntu@alerte-appart:~$`.

## Étape 4 — Installer l'outil sur le serveur

Toujours connecté au serveur :

```bash
git clone https://github.com/MhadhbiHamza/alerte-appart.git
```
```bash
bash alerte-appart/deploiement/installer.sh
```

Le script installe tout et crée le service `alerte-appart`, qui démarre avec le serveur et redémarre tout
seul en cas de problème. Il finit par « il manque le fichier .env » : c'est normal, on le copie à l'étape 5.

> Si votre dépôt GitHub est **privé**, `git clone` demandera un identifiant : utilisez votre nom GitHub et un
> *personal access token* (GitHub → Settings → Developer settings → Personal access tokens) comme mot de passe.

## Étape 5 — Copier vos fichiers personnels

1. **Arrêtez l'outil sur votre PC** (Ctrl+C dans sa fenêtre) et supprimez son raccourci de démarrage
   automatique s'il existe (Windows + R → `shell:startup`). Deux exemplaires en même temps enverraient tout
   en double. Si ça arrive, le serveur l'écrit dans son journal (« un autre exemplaire… »).
2. Dans **PowerShell sur votre PC**, depuis le dossier du projet (remplacez l'IP) :
   ```bash
   cd "C:\Personal Projects\alerte-appart"
   ```
   ```bash
   scp -i $HOME\.ssh\oracle.key .env dossier.pdf annonces.db suivi.csv ubuntu@141.145.12.34:~/alerte-appart/
   ```
   ```bash
   scp -i $HOME\.ssh\oracle.key templates.md ubuntu@141.145.12.34:~/alerte-appart/modele-perso.md
   ```
   `annonces.db` et `suivi.csv` gardent votre historique, et l'outil ne renotifie pas les annonces déjà vues.
   S'ils n'existent pas, retirez-les de la commande.
3. **Sur le serveur**, utilisez votre modèle de message, protégez vos identifiants, puis démarrez :
   ```bash
   echo "FICHIER_MODELE=modele-perso.md" >> ~/alerte-appart/.env && chmod 600 ~/alerte-appart/.env
   ```
   ```bash
   cd ~/alerte-appart && .venv/bin/python main.py --test
   ```
   ```bash
   sudo systemctl restart alerte-appart
   ```

## Étape 6 — Vérifier que ça tourne

```bash
journalctl -u alerte-appart -f
```
Vous devez voir « Démarrage. Critères : … ». **Ctrl+C** arrête seulement l'affichage : l'outil continue de
tourner. Sur Telegram, envoyez `/stats` au bot : il doit répondre.

Vous pouvez maintenant **fermer PowerShell et éteindre votre PC** : tout tourne sur le serveur.

---

## Au quotidien

| Je veux… | Comment |
|---|---|
| Voir mes candidatures | `/suivi` dans Telegram, ou `/csv` pour recevoir le fichier Excel |
| Mettre en pause / reprendre | `/pause` et `/reprise` dans Telegram |
| Savoir si le serveur marche | Le **☀️ bilan du matin** arrive chaque jour à 8 h |
| Installer une nouvelle version | `ssh -i $HOME\.ssh\oracle.key ubuntu@IP` puis `bash ~/alerte-appart/deploiement/mettre-a-jour.sh` |
| Modifier mon message | sur le serveur : `nano ~/alerte-appart/modele-perso.md` (Ctrl+O pour enregistrer, Ctrl+X pour quitter). Pris en compte tout de suite. |
| Modifier un réglage (`.env`) | `nano ~/alerte-appart/.env` puis `sudo systemctl restart alerte-appart` |
| Changer de dossier PDF | `scp -i $HOME\.ssh\oracle.key dossier.pdf ubuntu@IP:~/alerte-appart/` depuis le PC |
| Voir ce qui s'est passé | `journalctl -u alerte-appart --since today`, ou le fichier `~/alerte-appart/logs/alerte-appart.log` |
| Arrêter l'outil | `sudo systemctl stop alerte-appart` (et `disable` pour qu'il ne redémarre pas avec le serveur) |

## Dépannage

| Problème | Solution |
|---|---|
| `Permission denied (publickey)` | Mauvaise clé ou mauvais utilisateur : c'est bien `ubuntu@…` et `-i $HOME\.ssh\oracle.key` ? |
| `UNPROTECTED PRIVATE KEY FILE` | Relancez la commande `icacls` de l'étape 3. |
| `Connection timed out` | Le serveur est arrêté (console Oracle → Instances → *Start*) ou l'IP est fausse. |
| Plus de bilan du matin | Le serveur a été arrêté : console Oracle → Instances → *Start*. L'outil repart tout seul. |
| « un autre exemplaire de l'outil tourne déjà » | L'outil tourne aussi sur votre PC : fermez-le. |
| `systemctl status alerte-appart` affiche *failed* | `journalctl -u alerte-appart -n 50` montre l'erreur (souvent une ligne du `.env`). |

## Autre option gratuite : Google Cloud

Google Cloud offre aussi un petit serveur gratuit (**e2-micro**, régions `us-central1`, `us-west1` ou
`us-east1`) qui n'est pas arrêté pour inactivité. Seule limite : 1 Go de trafic sortant gratuit par mois,
suffisant ici (l'outil réutilise ses connexions). L'installation est identique à partir de l'étape 4 :
créez une VM *e2-micro* Ubuntu, connectez-vous avec le bouton **SSH** de la console, puis suivez les étapes 4
à 6 (l'utilisateur n'est pas `ubuntu` mais votre nom Google : adaptez les chemins `~/alerte-appart`).
