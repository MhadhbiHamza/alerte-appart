# Créer les alertes sur Jinka, SeLoger et PAP

Comptez environ 20 minutes en tout. L'idée est de faire **le moins d'alertes possible** en mettant plusieurs
villes dans la même alerte. L'outil se charge ensuite du tri fin : prix charges comprises, zones exactes,
doublons entre sites, priorités ⭐.

## Avant de commencer

- Utilisez **la boîte Gmail dédiée** pour créer les comptes et recevoir les alertes.
- Critères communs à toutes les alertes :
  - **Location**, appartement (ajoutez « maison » si vous le souhaitez) ;
  - **Loyer max : 900 €**, à mettre en charges comprises quand le site le propose ;
  - fréquence d'envoi : la **plus rapide** proposée (« immédiate » / « dès qu'une annonce paraît »).
- Ne mettez pas trop d'autres filtres (surface, pièces…) : vous risquez de rater des annonces mal renseignées.
- Les sites limitent parfois le nombre de villes par alerte. Si c'est le cas, répartissez-les selon les groupes ci-dessous.

## Les villes, regroupées en 3 alertes par site

**Alerte 1 : vos priorités** ⭐
- Ivry-sur-Seine (94200)
- Maisons-Alfort (94700)
- Paris 13e (75013)
- Paris 14e (75014)
- Asnières-sur-Seine (92600)

**Alerte 2 : le reste de votre liste principale**
- Paris 15e (75015), Paris 12e (75012), Paris 11e (75011)
- Clamart (92140), Bois-Colombes (92270), Vitry-sur-Seine (94400)
- Saint-Denis (93200), Montreuil (93100), Meudon (92190)

**Alerte 3 : options secondaires**
- Vincennes (94300), Charenton-le-Pont (94220), Montrouge (92120)
- Clichy (92110) : ⚠️ choisissez bien **Clichy 92110**, pas Clichy-sous-Bois (93)
- Boulogne-Billancourt (92100), Issy-les-Moulineaux (92130)

## Alertes colocation (600 € max)

Pour la colocation, créez **en plus** une alerte par site avec les mêmes villes (ou seulement vos priorités
pour commencer) et un **loyer max de 600 €** :

- **Jinka** : dans l'alerte, cochez le type **Colocation** s'il est proposé ; sinon créez une alerte
  « appartement » à 600 € max, l'outil reconnaît les colocations au mot « colocation » dans l'annonce.
- **SeLoger** : dans **Louer**, choisissez le type **Colocation** s'il apparaît dans la liste des biens ;
  sinon, faites une recherche « chambre » ou « colocation » à 600 € max.
- **PAP** : choisissez **Colocation** dans le type de bien (rubrique Location), loyer max 600 €.

Nommez ces alertes avec le mot **« Colocation »** quand le site permet de leur donner un nom : si ce mot
apparaît dans l'objet de l'email d'alerte, toutes ses annonces sont traitées comme des colocations.

> Les quartiers (Alésia, Maison-Blanche, Bécon…) ne se règlent pas dans les alertes : les sites filtrent
> par ville ou arrondissement. L'outil affiche le quartier dans la notification quand l'annonce le mentionne.

## Jinka (le plus utile : regroupe de nombreux sites)

1. Allez sur <https://www.jinka.fr> et créez un compte avec la boîte dédiée.
2. Créez une alerte : **Location**, ajoutez les villes de l'**alerte 1**, budget max **900 €**.
3. Activez la réception **par email** dans les réglages de notification. L'application mobile peut aussi
   vous notifier, mais l'outil a besoin des emails.
4. Recommencez pour les alertes 2 et 3. Si Jinka accepte toutes les villes dans une seule alerte, une seule suffit.

## SeLoger

1. Allez sur <https://www.seloger.com>, puis **Louer**.
2. Dans « Où ? », ajoutez une à une les villes de l'**alerte 1**. Tapez le nom puis choisissez la bonne
   suggestion (avec le code postal).
3. Type : **Appartement**. Budget max : **900 €**.
4. Lancez la recherche, puis cliquez sur **« Créer une alerte »** (ou « Sauvegarder la recherche ») et
   connectez-vous avec la boîte dédiée. Choisissez l'envoi par **email**, au rythme le plus rapide.
5. Recommencez pour les alertes 2 et 3.

## PAP (particulier à particulier, sans frais d'agence)

1. Allez sur <https://www.pap.fr>, puis **Location**.
2. Dans « Lieu », ajoutez les villes de l'**alerte 1**.
3. Type : **Appartement**. Loyer max : **900 €**.
4. Lancez la recherche, puis cliquez sur **« Créer une alerte »** et indiquez la boîte dédiée.
5. Recommencez pour les alertes 2 et 3.

## Vérifier que tout marche

1. Le lendemain, vérifiez que des emails de Jinka, SeLoger et PAP arrivent dans la **boîte de réception**
   de la boîte dédiée. Regardez aussi dans Spam et Promotions. S'ils y sont, ouvrez-en un et cliquez sur
   « Non spam », ou déplacez-le dans « Principale ».
2. Lancez :
   ```bash
   python main.py --analyser
   ```
   Chaque annonce doit afficher la bonne ville. Une ville « introuvable » ou fausse ? Envoyez la sortie pour qu'on ajuste `zones.py`.
