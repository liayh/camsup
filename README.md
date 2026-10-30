# CamSup — alertes de vidéosurveillance (NVR Dahua / Hikvision)

Petit programme installé sur le PC de la pharmacie. Il surveille l'enregistreur vidéo (NVR) et prévient :
- **le pharmacien**, par une fenêtre d'alerte sur le poste ;
- **l'installateur**, par e-mail, quand l'état du NVR devient critique.

## Ce qui est surveillé

| Problème | Délai | Fenêtre sur le poste | E-mail à l'installateur |
|---|---|---|---|
| Caméra débranchée ou en panne (plus d'images enregistrées depuis 15 s, ou déclarée déconnectée par le NVR) | moins de 30 s (relevé toutes les 10 s) | oui | non |
| Le NVR n'enregistre plus une caméra | 15 min sans nouvel enregistrement | oui | oui |
| Disque du NVR à remplacer (secteurs défectueux en hausse, seuil SMART dépassé, disque en erreur) | relevé toutes les 30 s | oui | oui |
| NVR injoignable | 2 relevés (≈ 20 à 40 s) | oui | oui |
| CamSup lui-même arrêté ou bloqué | 5 min | oui | — (impossible) |

- La fenêtre peut être fermée : elle revient **4 h** plus tard si le problème dure, ou tout de suite si un nouveau
  problème apparaît. Un message vert s'affiche quand tout est rétabli.
- L'e-mail part à l'ouverture du problème et à son rétablissement (pas de répétition).
- « N'enregistre plus » est mesuré avec l'**horloge du NVR** : un NVR à l'heure fausse ne crée pas de fausse alerte.
- Un disque « à surveiller » (secteurs abîmés mais stables) est affiché dans l'interface, sans alerte.

## Voir la vidéo et l'extraire (vol, agression : pour les gendarmes)

Menu **Voir la vidéo**, un seul écran :
1. cocher 1 à 4 caméras, le jour et la tranche horaire (de… à…, 6 h au plus), **Afficher** : les caméras sont lues
   ensemble, synchronisées, d'un bout à l'autre de la tranche (pause, ±30 s, ×2 à ×8, clic sur la barre de temps) ;
2. couper : **✂ Début ici** puis **✂ Fin ici** (15 min au plus), marqués sur la barre de temps ;
3. **⬇ Extraire et enregistrer** (motif ou n° de plainte facultatif) : Chrome / Edge demandent où enregistrer
   (clé USB…) ; les autres navigateurs l'enregistrent dans Téléchargements.

Le fichier est un ZIP à remettre tel quel :
- une vidéo **MP4** par caméra affichée : copie de l'enregistrement du NVR, image non réencodée (aucune perte) ;
- **LISEZ-MOI.txt** : pharmacie, période, qui a fait l'extrait, écart d'horloge du NVR, empreinte SHA-256 de chaque vidéo.

L'heure saisie est celle du PC ; CamSup la convertit à l'heure du NVR si son horloge est décalée. Lecture conseillée :
VLC (le H.265 n'est pas lu par tous les lecteurs de Windows). CamSup n'en garde pas de copie (effacée après 1 jour).
La vidéo s'affiche par morceaux de 5 min, le suivant préparé pendant la lecture : vidéo d'origine si le navigateur lit
le H.265 (≈ 13 s par caméra et par morceau sur ce NVR), sinon convertie par CamSup (≈ 30 s sur un PC récent).
Mesuré sur un NVR Dahua (caméra 5 Mpx) : 10 min ≈ 230 Mo, extraites en ≈ 20 s. NVR Dahua uniquement.

**Limites connues**
- NVR **Dahua** : tout est surveillé. **Hikvision** : seulement l'état des disques et la joignabilité.
- L'alerte « n'enregistre plus » suppose un enregistrement **continu** : un NVR qui n'enregistre que sur détection
  de mouvement donnerait de fausses alertes.
- Windows : installateur préparé (`packaging/windows`), pas encore fabriqué ni essayé sur un poste Windows.

## Installation sur un poste Windows (pharmacie)

**Fabriquer l'installateur** (une fois par version) :
- depuis Linux, avec Docker : `sh packaging/windows/build-docker.sh 1.0.0` (Python Windows et Inno Setup sous Wine,
  images publiques `tobix/pywine:3.12` et `amake/innosetup` ; ffmpeg de gyan.dev, empreinte vérifiée) ;
- ou sur un PC Windows (Python 3.12 + Inno Setup 6) : `powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1`.

→ `dist/CamSup-Installation-1.0.0.exe` (≈ 47 Mo, tout compris : programme, Python, ffmpeg). Rien d'autre à installer
sur le poste de la pharmacie. Déploiement sans question : `CamSup-Installation-1.0.0.exe /VERYSILENT /MOTDEPASSE=…`.

**Installer** sur le poste de la pharmacie (droits administrateur) : lancer `CamSup-Installation-1.0.0.exe`, choisir
le mot de passe du compte `admin` (intégrateur). CamSup :
- démarre à chaque ouverture de session Windows (raccourci « Démarrage », pour tous les utilisateurs) ; pas en service :
  la fenêtre d'alerte doit pouvoir s'afficher ;
- garde ses données dans `C:\ProgramData\CamSup` (base, clé de chiffrement `data\secret.key` à sauvegarder, journaux
  `data\logs`) et ses réglages facultatifs dans `C:\ProgramData\CamSup\camsup.env` ;
- n'est joignable que depuis ce poste : http://localhost:8080 (icône « CamSup » du menu Démarrer ou du bureau).

**Brancher le NVR de la pharmacie** (compte `admin`) : *Enregistreurs et caméras* → **Ajouter un enregistreur** :
pharmacie (ou nouvelle), adresse IP du NVR, identifiants du NVR (une seule fois, pour toutes ses caméras) →
**Se connecter au NVR** : CamSup liste toutes les caméras branchées (16, 32, 64 canaux…) avec leur nom ; **Toutes**,
renommer si besoin → **Ajouter**. Ensuite, par enregistreur : **Chercher de nouvelles caméras** (identifiants déjà
gardés) et **Identifiants du NVR** (changés une fois pour toutes ses caméras, vérifiés sur le NVR). Puis
*Paramètres* → e-mail de l'installateur, et *Utilisateurs* → compte du pharmacien.

Vérifié sous Wine (30/10/2026) : installation silencieuse, fichiers et raccourcis (démarrage, menu), premier
démarrage avec le mot de passe choisi puis retiré du fichier de réglages, ffmpeg. **Pas encore essayé sur un vrai
Windows** : assistant d'installation à l'écran, démarrage à l'ouverture de session, fenêtre d'alerte, lecture du H.265
dans le navigateur.

## Installation (développement, Linux)

```bash
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt   # + ffmpeg (extraction vidéo)
cp .env.example .env                                  # ADMIN_PASSWORD (e-mail : dans Paramètres)
cd frontend && npm ci && npm run build && cd ..       # interface → app/static/app
.venv/bin/python -m app.run                           # https://localhost:8080 + fenêtre d'alerte
```

## Tests

```bash
.venv/bin/python -m pytest tests -q          # serveur (les tests n'utilisent jamais le .env réel)
cd frontend && npm test && npm run build     # interface (types stricts)
```
