# Licences et redistribution

Le code CUTWEAVE est publié sous licence MIT (fichier `LICENSE`).

## Dépendances d'exécution

| Composant | Licence | Remarques |
|---|---|---|
| PySide6-Essentials / PySide6-Addons (Qt for Python) | LGPLv3 / GPLv3 (édition communautaire) | Utilisation en liaison dynamique. L'exécutable PyInstaller est construit en mode **dossier** (`dist\CUTWEAVE\_internal`) : les DLL Qt restent des fichiers séparés remplaçables par l'utilisateur, ce qui facilite le respect de la LGPL. Fournir le texte de licence et l'offre de sources Qt si vous redistribuez. Voir [licences Qt for Python](https://doc.qt.io/qtforpython-6/licenses.html). |
| OpenCV (`opencv-contrib-python-headless`) | Apache 2.0 | Seuls les trackers classiques CSRT, KCF et MOSSE sont utilisés. Aucune fonction DNN, aucun modèle ni cascade préentraînée (vérifié par `tests/test_constraints.py`). |
| NumPy (dépendance d'OpenCV) | BSD-3-Clause | |

## FFmpeg et FFprobe

CUTWEAVE **n'embarque pas** FFmpeg : il appelle les binaires présents sur le poste (PATH ou chemins définis dans *Outils > Paramètres*).
FFmpeg est principalement sous LGPL 2.1 ou ultérieure ; un build activant des composants GPL (par exemple `libx264`) ou non libres change les obligations, voire rend le binaire non redistribuable. Consultez [la page de licence FFmpeg](https://www.ffmpeg.org/legal.html) et les licences fournies avec le build utilisé.

Le poste de développement utilise FFmpeg 9.0.1 de BtbN, variante **LGPL** (encodeurs H.264 disponibles : `libopenh264`, `h264_mf`). Ce binaire n'est ni inclus dans le dépôt ni distribué avec CUTWEAVE. Si vous placez un FFmpeg à côté de l'exécutable pour le redistribuer, vous devez respecter la licence de ce build précis (texte de licence, sources ou offre écrite pour la LGPL/GPL).

Les captions sont incrustées avec le filtre `ass` (libass, ISC) qui doit être présent dans le build FFmpeg. `cutweave-cli check` indique sa disponibilité.

## Outils de développement et de construction

| Outil | Licence | Remarques |
|---|---|---|
| pytest et dépendances | MIT | Tests uniquement, non distribués. |
| PyInstaller | GPLv2 avec exception de bootloader | L'exception autorise la distribution d'exécutables construits sous la licence de votre choix. Non redistribué lui-même. |

Pour les obligations exactes d'une version installée, consultez les fichiers `LICENSE`/`COPYING` fournis par chaque paquet (`pip show -f <paquet>`).
