# CUTWEAVE

CUTWEAVE est une application de bureau Windows de **montage vidéo automatique, déterministe et local**.
Elle applique à des rushs une chaîne de traitement en trois étapes — visuel, composition, finition — puis produit un MP4 final.

**Contraintes respectées**

- Aucune intelligence artificielle : ni modèle, ni détection de personnes, ni transcription, ni service distant. Seuls des algorithmes classiques (trackers OpenCV CSRT/KCF/MOSSE, filtres FFmpeg) et des **règles explicites** saisies par l'utilisateur sont utilisés. Un test (`tests/test_constraints.py`) vérifie l'absence d'API d'IA, de réseau et de `shell=True` dans le code.
- Fonctionnement 100 % local : aucun compte, aucune télémétrie, aucune connexion réseau, aucun téléchargement à l'exécution.
- Les rushs d'origine ne sont jamais modifiés, déplacés ni supprimés.

---

## En bref : le Studio

1. **Dépose tes rushs** (fichiers ou dossiers entiers) dans le Studio — un projet est créé automatiquement.
2. **Choisis un style** : Edit, Vlog, Cinématique, Highlights, Souvenirs ou Face cam.
3. **Clique sur « Générer »** : CUTWEAVE analyse les rushs, compose le montage et propose jusqu'à **3 variantes** (Signature, Énergique, Posée) avec musique, effets sonores, étalonnage et titres.
4. **Exporte** ta préférée, ou **peaufine-la** dans l'éditeur : tout reste modifiable (titres, musique, volumes, ducking, SFX, cadrage, masques, B-roll…).

---

## Prérequis

- Windows 10 ou 11 (x86-64).
- Python 3.12 (pour l'installation depuis les sources).
- FFmpeg et FFprobe installés localement, avec le filtre `ass` (libass) pour les captions. Exemple testé : build BtbN FFmpeg 9.0.1 **LGPL** (`winget install BtbN.FFmpeg.LGPL.9.0`).

## Installation depuis les sources

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-lock.txt          # versions exactes testées
python -m pip install --no-deps --no-build-isolation -e .
```

`requirements-lock.txt` fige les versions vérifiées sur Windows / Python 3.12 (PySide6 6.12.0, OpenCV contrib headless 4.14.0.94, pytest 8.4.2…). Une installation non figée reste possible avec `python -m pip install -e ".[dev]"`.

## Vérifier FFmpeg et FFprobe

```powershell
cutweave-cli check
```

La commande affiche les versions, la présence des filtres `ass` et `unsharp`, les encodeurs H.264 disponibles et les trackers OpenCV utilisables. Si FFmpeg n'est pas dans le `PATH`, renseignez son chemin dans **Outils > Paramètres** (bouton *Vérifier FFmpeg et FFprobe*), ou définissez `CUTWEAVE_FFMPEG` et `CUTWEAVE_FFPROBE`.

Encodeur vidéo choisi automatiquement, dans l'ordre : `libx264`, `libopenh264`, `h264_mf`, puis `mpeg4` en dernier recours. Audio : AAC 192 kb/s, 48 kHz stéréo.

## Lancement

```powershell
cutweave                 # interface graphique
python -m cutweave       # équivalent
cutweave-cli --help      # ligne de commande (rendu sans interface)
```

### Ligne de commande

```powershell
cutweave-cli info  D:\Projets\Demo
cutweave-cli render D:\Projets\Demo --media rush01.mp4 --output D:\Exports\rush01.mp4
cutweave-cli render D:\Projets\Demo --media rush01.mp4 --stage visual   # une étape seule
cutweave-cli purge-cache D:\Projets\Demo
```

Codes de retour : 0 succès, 1 échec de traitement, 2 configuration invalide (média manquant, masque hors cadre…), 130 annulation (Ctrl+C).

## Studio — montage automatique

Le Studio est l'écran d'accueil. Il enchaîne quatre traitements déterministes :

1. **Analyse des rushs** (traitement du signal classique, résultats en cache) : mouvement (différence entre images), luminosité, netteté (variance du laplacien), niveau sonore (RMS), changements de plan (pics de différence) et zones d'activité sonore (seuil adaptatif au-dessus du bruit de fond — aucune reconnaissance vocale).
2. **Plan de montage** selon le style choisi :

| Style | Règles principales |
|---|---|
| **Edit** | Coupes calées sur le tempo de la musique, meilleurs moments (mouvement + netteté), zooms punch, flashs et glissés, look Punchy. |
| **Vlog** | Garde la voix et retire les blancs (jump cuts), plans d'illustration sous la musique pour les rushs sans voix, musique douce avec ducking pendant la parole. |
| **Cinématique** | Plans longs au ralenti (×0,8), push-in et panoramiques, fondus, bandes 2.39:1 en 16:9, étalonnage Teal & Orange / Film. |
| **Highlights** | Moments les plus intenses (mouvement + son), montée en puissance jusqu'au meilleur plan, flashs et impacts. |
| **Souvenirs** | Récap chronologique réparti sur tous les rushs, fondus et glissés, tons chauds / vintage. |
| **Face cam** | Silences retirés, zoom alterné à chaque coupe pour masquer les jump cuts, musique discrète. |

   Les variantes changent le rythme (Énergique : plans plus courts, accroche en ouverture ; Posée : plans plus longs, ordre chronologique), la musique et le look. Une graine dérivée des rushs et des réglages rend chaque variante reproductible.
3. **Assemblage FFmpeg** : chaque plan est recadré (remplir le cadre ou fond flou), animé, normalisé à 30 i/s et mis en cache (réutilisé d'une variante à l'autre, rendu en parallèle) ; les plans sont enchaînés par coupes ou transitions `xfade`/`acrossfade`, puis le look, les bandes et les fondus sont appliqués.
4. **Finition éditable** : le master devient un média du projet configuré avec le preset de titre, la musique (en boucle, calée sur le premier temps, avec ducking), les SFX et la netteté, puis l'aperçu est rendu par le pipeline standard. L'export final réutilise ce rendu en cache tant que rien n'a changé.

Réglages facultatifs : format (9:16, 16:9, 1:1), durée, musique (intégrée, la tienne — tempo détecté automatiquement par autocorrélation du flux spectral — ou aucune), look, cadrage, nombre de variantes, titre d'ouverture, texte de fin, qualité (1080p / 720p rapide).

En ligne de commande : `cutweave-cli auto D:\Projets\Demo D:\Rushs --mode vlog --aspect 16:9 --variants 3`.

## Bibliothèque d'actifs intégrés

Tous les actifs sont **synthétisés localement** au premier usage (numpy : oscillateurs, bruit à graine fixe, enveloppes, filtres et réverbération par convolution) dans `%APPDATA%\CUTWEAVE\assets\v1` — aucun téléchargement, libres de droits.

- **6 musiques en boucle** au tempo connu (cuts exactement sur le temps) : Pulse (électro 124 BPM), Overdrive (énergie 140), Sunday (lo-fi 84), Horizon (cinématique 70), Daylight (pop 112), Nocturne (ambiance 90).
- **11 effets sonores** : whoosh, swipe, whoosh sortant, impact, boom, riser, pop, clic, ding, déclencheur photo, glitch.
- **8 looks** (filtres FFmpeg) : Naturel, Punchy, Chaud, Froid, Teal & Orange, Vintage, Film, Noir & blanc.
- **7 styles de titres** : Clean, Dynamic, Cinematic, Impact, Vlog, Néon, Souvenir.

La page **Bibliothèque** permet d'écouter chaque son et de l'ajouter au projet (onglet *Musique & SFX* de l'éditeur).

## Interface

- **Studio** (accueil), **Éditeur** (réglages détaillés par étape), **Bibliothèque**, historique, journaux et paramètres dans la barre latérale ; thème sombre unifié. Les traitements s'affichent dans la barre de progression du bas (bouton *Annuler*, panneau *Journal* repliable).
- **Barre latérale** : nouveau/ouvrir un projet, import, accès direct à chaque éditeur, historique des tâches, journaux, paramètres.
- **Médias** : import de fichiers ou d'un dossier (récursif), tableau nom / durée / résolution / images/s / audio ; les fichiers introuvables sont marqués `[manquant]` (menu *Projet > Relier les médias manquants* pour les retrouver par nom dans un dossier).
- **Aperçu** : lecture, pause, déplacement dans le temps (lecteur Qt Multimedia).
- **Éditeurs d'étapes** (onglets) : Visuel, Presets captions, Masques, B-roll, Musique & SFX, Règles & marqueurs, Pipeline.
- **Pipeline** : activation des modules, statut de chaque étape pour la vidéo sélectionnée, lancement du pipeline complet ou d'une étape seule, option de recalcul forcé.
- **Progression** : barre déterminée pendant les encodages (lecture de `-progress` FFmpeg) avec le nom de l'étape en cours ; bouton **Annuler le traitement**.
- **Journaux** : panneau en bas de fenêtre ; fichier tournant `%APPDATA%\CUTWEAVE\logs\cutweave.log` (dossier configurable) ; *Outils > Historique des tâches* liste chaque tâche avec son statut et son erreur éventuelle.

Toutes les modifications sont enregistrées immédiatement dans le projet : on peut fermer puis rouvrir un projet sans perte.

## Projets

Un projet est un dossier contenant `project.json`, la base `cutweave.sqlite3`, `exports/`, `tracking/`, les checkpoints `.cutweave_pipeline/` et le cache `.cutweave_cache/`. Les médias placés sous le dossier du projet sont enregistrés en chemins relatifs ; les autres gardent leur chemin d'origine (les rushs ne sont jamais dupliqués). Détail complet : [`docs/FORMAT_PROJET.md`](docs/FORMAT_PROJET.md).

## Les trois étapes

### Étape 1 — Visuel

- **Cadrage déterministe** : formats 9:16, 16:9, 1:1 et résolution paire au choix. Sans keyframe, cadrage centré (`scale` + `crop`). Avec keyframes (temps, centre X/Y normalisés, zoom 1–4), interpolation *smoothstep* entre keyframes, positions bornées à l'image source. Chaque ligne du tableau est corrigeable à la main.
- **Tracking classique** : *Sélectionner une cible et suivre* affiche l'image au curseur ; tracez un rectangle autour de la zone. Le suivi (CSRT, KCF ou MOSSE d'OpenCV contrib) tourne en arrière-plan et s'annule. La trajectoire est exportée en JSON dans `tracking/` puis affichée (chemin des centres, pertes de suivi marquées) ; le journal indique le nombre d'images perdues et le premier instant de perte. Pour réinitialiser le tracker, relancez une sélection à cet instant.
- **Trajectoire → cadrage** : *Importer une trajectoire de tracking* convertit les boîtes suivies en keyframes de cadrage, avec **lissage** (moyenne glissante, réglable) et **marge de sécurité** autour de la cible.
- **Netteté** : filtre FFmpeg `unsharp` — activation, intensité 0–2 (défaut 0,35), rayon impair 3–23 (défaut 5), seuil 0–255 (0 = tout le contenu ; au-delà, seuls les pixels changeant de plus que le seuil sont accentués). Bouton de comparaison avant/après au curseur et réinitialisation. Limites : `unsharp` accentue aussi le bruit et les artefacts de compression ; il s'applique après mise à l'échelle.

### Étape 2 — Composition

- **Captions** (aucune transcription) : import SRT, WebVTT, ASS/SSA ; création au curseur, édition du texte et des timestamps dans le tableau, scission, fusion, saut au segment. Les timestamps incohérents sont refusés avec un message explicite. Représentation interne commune quel que soit le format d'origine.
- **Masques** : rectangles, ellipses, polygones (`X:Y;X:Y;…`), opacité, bornes temporelles, effet opaque ou flou (rayon 1–50 px), keyframes `ms:x:y:largeur:hauteur:opacité` interpolées. **Masque suivant une trajectoire** : crée un masque animé à partir d'une trajectoire de tracking, projetée dans le cadre de sortie (cadrage inclus) ; les keyframes restent corrigeables.
- **B-roll** : bibliothèque locale avec miniatures, catégories et tags saisis à la main, recherche, prévisualisation ; insertion au curseur avec entrée/sortie source et piste ; tout reste modifiable dans le tableau. Le clip couvre le cadre de sortie ; seul l'audio du rush principal est conservé.
- **Règles B-roll** (*Règles & marqueurs*) : « mot-clé présent dans une caption → clip précis ou tag ». Correspondance littérale du mot entier, insensible à la casse ; avec un tag, les clips correspondants sont utilisés à tour de rôle par ordre alphabétique. Une insertion au plus par caption, jamais sur un placement manuel existant de la même piste. **Sans règle correspondante, rien n'est inséré.**

### Étape 3 — Finition

- **Presets captions** : Clean, Dynamic et Cinematic intégrés ; presets personnalisés dupliqués, modifiés, importés et exportés en JSON (police, taille, couleurs du texte, de surbrillance, du contour, de l'ombre, du fond, opacité du fond, alignement ASS 1–9, marges, gras, animation `none`/`fade`/`pop` et durée, mise en évidence `none`/`cue`). Les animations sont des transformations ASS calées sur les timestamps.
- **Musique** : fichier local, extrait (entrée/sortie), départ dans la timeline, volume, fondus, bouclage, conservation ou non de l'audio du rush. **Ducking** : la musique est atténuée pendant des intervalles manuels (`debut_ms,fin_ms,niveau,fondu_ms; …`) et/ou pendant les captions (*Atténuer pendant les captions* : les timestamps des captions servent de segments de voix déclarés ; les captions proches sont fusionnées pour éviter le pompage). Aucune détection de la parole.
- **SFX** : bibliothèque audio locale (catégories libres), placements manuels (position, extrait, volume, fondus, libellé) et **règles** « événement → son » avec volume, décalage (−10 s à +10 s), extrait, fondus, filtre de libellé et intervalle minimal. Événements disponibles : début/fin de clip (rush ou B-roll), coupe rush ↔ B-roll, début/fin de caption, animation de caption (preset animé), marqueur manuel.

## Pipeline, cache et reprise

- **Pipeline complet** : les étapes 1 à 3 compilent leurs instructions (JSON dans `.cutweave_pipeline/stages/`) puis le rendu final encode **une seule fois** : cadrage → netteté → B-roll → masques → captions, mixage musique + SFX + audio d'origine (limiteur à −0,2 dBFS).
- **Étape seule** : produit un MP4 intermédiaire vérifiable pour l'étape (et ses dépendances modifiées).
- **Cache** : chaque étape a une clé SHA-256 calculée sur les fichiers sources concernés, ses paramètres, la version du module et les clés des étapes précédentes. Modifier un preset de captions ne recalcule ni le visuel ni la composition ; le rendu final réutilise un encodage identique déjà en cache. Le tracking a son propre cache. *Projet > Purger le cache du projet* (ou `cutweave-cli purge-cache`) vide le cache.
- **Reprise** : un checkpoint par vidéo ; après interruption, une relance reprend aux étapes non valides.
- **Annulation** : arrêt propre de FFmpeg/du tracker ; fichiers temporaires supprimés ; la tâche est enregistrée `cancelled`. Un succès n'est enregistré qu'après l'écriture effective du fichier final.
- **Activation des modules** (onglet Pipeline) : cadrage, netteté, captions, masques, B-roll, musique, SFX.

## Tests

```powershell
python -m pytest
```

157 tests : modèles et validation, parsing de captions, montage automatique (analyse, tempo, plans déterministes de chaque style, variantes et export de bout en bout), actifs synthétisés, presets, cache, orchestrateur, règles, tracking sur vidéo synthétique, rendus FFmpeg réels (cadrage, netteté, captions, masques, B-roll, musique, ducking, SFX), pipeline complet bout-en-bout depuis un projet, annulation, CLI, et démarrage de l'interface en mode hors écran (`QT_QPA_PLATFORM=offscreen`). Les médias de test sont générés à la volée (`lavfi`) ; les tests FFmpeg sont ignorés si FFmpeg est absent.

## Construction d'un exécutable Windows

```powershell
python -m pip install -r requirements-build-lock.txt
.\scripts\build_windows.ps1
```

Le script produit `dist\CUTWEAVE\CUTWEAVE.exe` (interface) et `dist\CUTWEAVE\cutweave-cli.exe` (ligne de commande) en mode dossier, puis démarre l'interface hors écran et vérifie un code de sortie nul. **Vérifié** sur Windows 11 / Python 3.12 / PyInstaller 6.22.3 : démarrage de l'interface, `cutweave-cli check` et rendu complet d'un projet par `cutweave-cli.exe`. Taille ≈ 290 Mo. FFmpeg n'est pas inclus (voir licences). Si la politique d'exécution PowerShell bloque les scripts locaux, autorisez-les pour votre session selon votre politique de sécurité.

## Limites connues

- Dans l'éditeur, une timeline = **un média principal** par rendu, enrichi de B-roll, masques, captions et audio. L'assemblage de plusieurs rushs avec transitions est fait par le Studio, qui produit un master ensuite éditable comme n'importe quel média (le découpage du master lui-même n'est pas modifiable plan par plan dans l'éditeur : régénérez une variante pour changer la sélection).
- Le Studio ne « comprend » pas les images : il choisit les extraits selon des mesures de pixels et de niveau sonore. Un plan net et animé mais sans intérêt peut être retenu ; le cadrage « Remplir » reste centré.
- L'aperçu vidéo lit le rush d'origine ; masques et captions sont visibles dans les rendus (étape seule ou export), pas en direct dans le lecteur. La comparaison avant/après au curseur concerne la netteté.
- B-roll : couverture plein cadre uniquement, sans audio propre.
- Mise en évidence : le mode `cue` colore toute la caption (pas de synchronisation mot à mot).
- Masques animés, ellipses et polygones utilisent le filtre `geq` : correct mais lent en haute résolution.
- La correction manuelle d'un suivi se fait sur les keyframes de cadrage ou de masque issues de la trajectoire, pas sur le fichier de trajectoire lui-même.
- Un média hors du dossier projet est enregistré par chemin absolu ; s'il est déplacé, utilisez *Relier les médias manquants*.

### Limites des trackers classiques

Les trackers suivent une **zone de pixels** choisie par l'utilisateur ; ils ne reconnaissent rien. Ils perdent la cible en cas d'occlusion, de sortie de cadre, de flou de mouvement, de changement d'échelle rapide ou d'éclairage. CSRT est le plus précis et le plus lent ; KCF est rapide mais tolère mal les changements d'échelle ; MOSSE est très rapide et le plus fragile. Les pertes sont enregistrées (`bbox: null`), ignorées lors de la conversion en keyframes et signalées dans le journal.

## Licences

Voir [`docs/LICENCES.md`](docs/LICENCES.md), notamment les conditions de redistribution de Qt (LGPL) et de FFmpeg (selon le build).
