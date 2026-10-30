# CamSup — notes pour Claude Code

Alertes de vidéosurveillance pour pharmacies : un poste (Windows visé) par pharmacie surveille son NVR, affiche une
fenêtre d'alerte au pharmacien et envoie un e-mail à l'installateur quand l'état du NVR devient critique ; extrait
vidéo (ZIP de MP4) à remettre aux forces de l'ordre, sans passer par l'interface du NVR.
FastAPI + SQLite + Tkinter + interface React. Langue de l'UI, des messages et des commentaires : français.
L'ancienne version (enregistrement, direct go2rtc, détection de vol, multi-clients, incidents) a été retirée le 30/10/2026 ;
archives : `~/Téléchargements/camsup-sauvegarde-20261030.tar.gz` (code) et `camsup-donnees-20261030.tar.gz` (data, secrets).

## Lancer en dev
```bash
.venv/bin/pip install -r requirements-dev.txt
cd frontend && npm ci && npm run build && cd ..
.venv/bin/python -m app.run                      # lit .env à la racine (CAMSUP_ENV pour un autre fichier)
.venv/bin/python -m pytest tests -q && (cd frontend && npm test && npm run build)
```
Démarrage détaché : chemins absolus + `setsid nohup`. Arrêt : `kill $(pgrep -f "[p]ython -m app[.]run")`, jamais
dans une commande qui contient elle-même le texte « python -m app.run » en clair (pgrep la tuerait).
Matériel réel : NVR Dahua 192.168.1.160, 7 caméras (canaux 1–7) ; port 80 du NVR planté → repli HTTPS:8080 (`devicehttp`).

## Architecture (`app/`)
- `monitor.py` : thread unique, toutes les `CHECK_INTERVAL` (5) s : battement de cœur (`settings.heartbeat`),
  `nvrwatch.check` toutes les `CAMERA_CHECK_SECONDS` (10) s et `storage.check` (`HDD_CHECK_SECONDS`, 30 s) en arrière-plan ; état des
  caméras (`status`), purge des journaux.
- `nvrwatch.py` (Dahua RPC2), un relevé ≈ 0,8 s pour 7 canaux : heure du NVR, `getCameraState`, fin du dernier fichier
  par canal (`mediaFileFind` ; le NVR ignore `Order` → lire tous les fichiers, garder le max ; le fichier en cours
  avance avec l'horloge du NVR, retard mesuré 0 s). Exigence utilisateur : **caméra débranchée affichée en < 30 s**.
  Mesuré le 30/10/2026 : le NVR met ≈ 50 s à passer une caméra en « Unconnect », mais l'enregistrement s'arrête
  aussitôt → `camera:<id>` si plus d'images depuis `CAMERA_STALL_SECONDS` (15) s alors qu'une autre caméra du NVR est
  à jour (toutes en retard = horloge du NVR qui saute ou NVR qui n'enregistre plus : pas d'alerte caméra), ou état
  ≠ Connected `NVR_FAILS` fois. `rec:<id>` après `RECORD_STALE_MINUTES` (15) min, **horloge du NVR**.
  `nvr:<hôte>` injoignable (message lisible via `reason()`) ; délai de grâce `NVR_BOOT_GRACE_SECONDS` (120 s) après
  son retour (il met ≈ 1 min à reconnecter ses caméras : sinon fausses alertes, vu le 30/10). Hikvision non pris en charge.
- `export.py` : extrait vidéo (menu « Voir la vidéo » : bouton « Extraire et enregistrer » après les ciseaux ; tous
  les rôles, sur leurs sites ; `showSaveFilePicker` sinon téléchargement ; pas de liste d'extraits, copie effacée après 1 jour). Période saisie à l'heure
  du PC, convertie à l'heure du NVR (écart lu par `nvrwatch.probe`) ; `loadfile.cgi` (vidéo d'origine .dav, 400 = aucun
  enregistrement) → `ffmpeg -c:v copy` en MP4 (image non réencodée) → ZIP + `LISEZ-MOI.txt` (empreintes SHA-256).
  15 min au plus (`EXPORT_MAX_MINUTES`), un extrait à la fois (file de 1), table `exports`, fichiers `data/exports/<id>.zip`, purge après
  `EXPORT_RETENTION_DAYS`, `recover()` au démarrage. Mesuré : 10 min d'une caméra 5 Mpx H.265 = 231 Mo en 22 s.
  ffmpeg à fournir sous Windows (`FFMPEG`) : non vérifié.
- `preview.py` : « Voir la vidéo » : tranche horaire (≤ 6 h), 1 à 4 caméras synchronisées, lue par morceaux de 5 min
  (morceau en cours + suivant demandés par l'interface), ciseaux début / fin sur la barre de temps.
  La planche d'images (« chercher le moment ») a été retirée à la demande de l'utilisateur (30/10/2026).
  Le NVR n'enregistre pas le flux secondaire (subtype=1 → 400). Mode `copie` (MP4 H.265 étiqueté `hvc1`, 0,04 s/min)
  si le navigateur répond à `canPlayType('video/mp4; codecs="hvc1…"')`, sinon ou si la lecture échoue → `conversion`
  (H.264 960 px 15 i/s ; décoder le H.265 5 Mpx coûte ≈ 30 s CPU par minute de vidéo). 2 aperçus à la fois, en
  mémoire (`_jobs`) + `data/previews`, effacés 1 h après la dernière demande et au démarrage. Mesuré : 3 caméras en
  copie prêtes en 13–25 s ; 1 caméra convertie en 32 s (PC 16 cœurs). Lecture H.265 sous Windows : non vérifiée.
  Heure affichée sous la vidéo ≈ ±3 s (l'heure incrustée dans l'image fait foi).
- `dahuarpc.Session` : connexion RPC2 (défi MD5). `dahuarpc.shared()` garde une session ouverte par NVR pour
  `storage` et `nvrwatch` (keepAlive après 20 s ; refaite après une erreur) : avant, ≈ 380 connexions/h dans le
  journal du NVR, après 0 (mesuré le 30/10/2026). `close_all()` à l'arrêt.
- `storage.py` : disques des NVR (Dahua RPC2 + SMART, Hikvision ISAPI). `_trend` : compteurs 5/197/198 comparés au premier
  relevé du disque (`smart_baseline`, par n° de série) ; en hausse = `bad`. Alerte `hdd:<hôte>` seulement au niveau `bad`.
  Équipements regroupés par `cameras.device_key()` (le jeton Fernet diffère par caméra : ne pas grouper par `password_enc`).
- `alerting.py` : `raise_alert(key, …)` / `clear_alert(key)` dédupliquées par clé ; e-mail seulement pour
  `config.EMAIL_KINDS` (`nvr`, `rec`, `hdd`), webhook pour tout ; envois en arrière-plan.
- `mail.py` : réglages e-mail saisis dans Paramètres (`settings.email`, JSON, mot de passe chiffré ; sinon `SMTP_*` du
  .env), plusieurs destinataires, STARTTLS / SSL / aucune, résultat du dernier envoi (`settings.email_status`).
  Bouton de test = envoi immédiat avec l'erreur exacte. Aucun compte réel essayé (pas d'identifiants fournis).
- Ajout d'un NVR (`main.py` `/nvr/*`, `nvrwatch.discover`) : identifiants saisis une fois, liste des canaux non
  « Empty » (`ChannelTitle`, noms génériques → « Caméra N »), ajout groupé sans doublon, « chercher de nouvelles
  caméras » avec les identifiants gardés, identifiants communs changés pour tout le NVR (vérifiés avant).
- `popup.py` : processus séparé lancé par `run.py` (Tkinter) avec l'identifiant du démarrage (`settings.instance`) :
  une fenêtre d'un démarrage précédent se ferme seule. Lit la base en lecture seule toutes les 5 s ; `Reminder`
  = logique de rappel (4 h, ou nouveau problème). Heartbeat muet 5 min → « surveillance arrêtée » ; `"arret"` écrit
  à l'arrêt normal → la fenêtre se ferme (uvicorn tue le processus après SIGTERM : le `finally` de `run.main` ne s'exécute pas).
  Essai sans écran : `xvfb-run` + capture `ffmpeg -f x11grab`.
- `accounts.py` : rôles `integrator` / `pharmacist`, sessions cookie, verrouillage 5 échecs/15 min, CSRF par en-tête
  `X-CamSup: 1`, journal `audit`. Routeurs `main.py` : `public`, `router` (connecté), `admin` (intégrateur).
  Toute route liée à un site passe par `_cam()` / `require_site()` / `site_filter()` (404 hors périmètre).
- `config.py` : variables d'environnement, ou `.env` (l'environnement l'emporte). Les tests pointent `CAMSUP_ENV` ailleurs.
- Base SQLite : `sites`, `cameras` (un canal d'un NVR), `alerts`, `status`, `settings`, `smart_baseline`, `exports`, `users`,
  `user_sites`, `sessions`, `audit`. `db._drop_obsolete` retire les tables de l'ancienne version et reconstruit `sites`
  sans `client_id` (clés étrangères suspendues pendant l'opération, sinon cascade sur les caméras).
- Interface (`frontend/`, compilée dans `app/static/app`, thème clair) : État et alertes (alertes + disques : secteurs
  réalloués / en attente / illisibles / CRC avec évolution, `components/DiskHealth`), Voir la vidéo, Enregistreurs et
  caméras (un module : disques + caméras par NVR, assistant d'ajout), Sites, Utilisateurs, Paramètres (compte, e-mail).
- Windows (`packaging/windows`) : `camsup_win.py` (point d'entrée ; `--popup` = fenêtre d'alerte), `camsup.spec`
  (PyInstaller, dossier), `camsup.iss` (Inno Setup : Program Files, `%PROGRAMDATA%\CamSup`, démarrage à l'ouverture de
  session, mot de passe admin demandé (ou `/MOTDEPASSE=` en silencieux) puis retiré du .env par
  `config.forget_admin_password`), `build.ps1` (sur Windows), `build-docker.sh` (depuis Linux : Wine, images
  tobix/pywine:3.12 et amake/innosetup ; Inno sous Wine a besoin d'un écran virtuel seulement pour *exécuter* le setup).
  `config.FROZEN` : 127.0.0.1, HTTP, ffmpeg à côté de l'exe, journaux dans `data/logs`. Installateur vérifié
  sous Wine (installation silencieuse, premier démarrage) ; pas encore sur un vrai Windows.

## Règles à respecter
- Ne jamais renvoyer ni journaliser un mot de passe : `cameras.public()` pour l'API, `security.scrub()` pour tout message d'erreur.
- Un seul worker uvicorn.
- Changement de schéma : traité dans `db.init()` (pas d'outil de migration).
- Ne rien inventer : signaler ce qui n'est pas vérifié (Windows, Hikvision, enregistrement sur détection).

## Reste à faire
E-mail à configurer dans Paramètres (compte d'envoi + destinataire) ; essayer l'installateur sur un vrai poste
Windows ; essai réel d'une caméra débranchée ; Hikvision (caméras et enregistrement) ;
compte NVR en lecture seule au lieu de l'admin ; NTP du NVR désactivé (horloge en retard).
