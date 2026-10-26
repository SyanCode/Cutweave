# Contribuer à CUTWEAVE

Merci de l'intérêt que tu portes à CUTWEAVE. Le projet est libre (licence MIT) et les contributions sont bienvenues :
corrections, tests, documentation, traductions, idées de styles de montage.

## Principes à respecter

CUTWEAVE se définit par quatre engagements qu'une contribution ne doit pas affaiblir :

1. **Aucune IA.** Ni modèle, ni reconnaissance vocale, ni détection d'objets ou de personnes, ni service distant. Les
   décisions viennent de règles explicites et de traitement du signal classique. `tests/test_constraints.py` vérifie
   l'absence de bibliothèques d'IA et d'accès réseau dans le code.
2. **100 % local.** Aucun compte, aucune télémétrie, aucun téléchargement à l'exécution.
3. **Les rushs ne sont jamais modifiés.** Toute sortie est écrite ailleurs, de façon atomique.
4. **Déterminisme.** Mêmes entrées et mêmes réglages donnent le même résultat (graines dérivées des entrées).

## Installer l'environnement de développement

```bash
python -m venv .venv
source .venv/bin/activate            # Windows : .\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

Il faut aussi FFmpeg et FFprobe dans le `PATH` (`cutweave-cli check` vérifie l'installation). Sous Linux, les
bibliothèques Qt système (`libegl1`, `libgl1`, `libxkbcommon0`, `libdbus-1-3`, `libfontconfig1`) sont nécessaires pour les
tests de l'interface.

## Lancer les tests

```bash
QT_QPA_PLATFORM=offscreen python -m pytest -q
```

Les médias de test sont générés à la volée avec FFmpeg (`lavfi`) ; les tests qui l'exigent sont ignorés s'il est absent.
Un correctif doit venir avec un test qui échoue avant et réussit après. Les tests de l'interface vérifient notamment qu'aucune
page ne déborde horizontalement de 960 × 600 à 1920 × 1080.

## Conventions

- Python 3.12 ou plus, annotations de types, lignes de 120 caractères au plus.
- Code, commentaires et textes de l'interface en **français** ; identifiants en anglais ou français selon le voisinage.
- Aucune commande FFmpeg construite par concaténation de texte : listes d'arguments et `subprocess` sans shell, via
  `cutweave.core.proc` (aucune fenêtre console sous Windows).
- Les filtres FFmpeg utilisés doivent exister dans un build **LGPL** (pas de `eq`, `boxblur`…) :
  `tests/data/ffmpeg_lgpl_filters.txt` fait foi et un test le vérifie.
- Interface : composants de `cutweave/ui/widgets.py`, icônes de `cutweave/ui/icons.py`, couleurs de `cutweave/ui/theme.py`.
  Pas d'emoji ni de couleur codée en dur dans une page.

## Proposer une modification

1. Ouvre d'abord un ticket pour les changements importants, afin de s'accorder sur l'approche.
2. Crée une branche, fais des commits courts avec un message clair (quoi et pourquoi).
3. Mets à jour `CHANGELOG.md` (section « Non publié ») et la documentation concernée.
4. Ouvre la demande de fusion en remplissant le modèle fourni.

## Signaler un problème

Utilise le modèle de ticket « Bug » et joins le **diagnostic** (menu *Aide > À propos et diagnostic > Copier le diagnostic*) :
il contient les versions et la configuration FFmpeg, sans aucune donnée personnelle ni chemin de tes vidéos.
