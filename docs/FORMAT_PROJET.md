# Format d'un projet CUTWEAVE

Un projet est un **dossier** choisi par l'utilisateur. Les rushs ne sont jamais copiés ni modifiés : le projet ne contient que des références et des fichiers produits.

```
MonProjet/
├── project.json              manifeste (schéma 1)
├── cutweave.sqlite3          métadonnées, instructions de montage, historique des tâches
├── exports/                  exports proposés par défaut (et aperçus des variantes du Studio)
├── montages/                 Studio : <mode>_<lettre>.mp4 (master assemblé), .plan.json, .result.json
├── assets/                   copies des musiques et SFX intégrés utilisés (projet autonome)
├── tracking/                 trajectoires de tracking exportées (JSON)
├── .cutweave_pipeline/       checkpoints de reprise et instructions compilées
│   ├── state_media_<id>.json
│   └── stages/<étape>_<id>.json | .mp4
└── .cutweave_cache/          cache local (purgeable)
    ├── render/<sha256>.mp4
    ├── tracking/<sha256>.json
    ├── analysis/<empreinte>.json        analyse des rushs (mouvement, son, netteté…)
    ├── autoedit_shots/<sha256>.mkv      plans normalisés réutilisés entre variantes
    └── thumbnails/broll-<id>.jpg | rush-<id>.jpg | <mode>_<lettre>.jpg
```

## `project.json`

```json
{
  "schema_version": 1,
  "id": "uuid",
  "name": "MonProjet",
  "created_at": "2026-10-25T10:00:00+00:00",
  "settings": {
    "output_dir": "exports",
    "cache_dir": ".cutweave_cache",
    "modules": {"framing": true, "sharpening": true, "captions": true, "masks": true,
                "broll": true, "music": true, "sfx": true}
  }
}
```

`cache_dir` doit rester à l'intérieur du dossier projet. `modules` est absent tant qu'aucun module n'a été désactivé (tout est actif par défaut). Le manifeste est réécrit de façon atomique (fichier temporaire puis remplacement).

## Base SQLite `cutweave.sqlite3`

| Table | Contenu |
|---|---|
| `media` | Rushs : `stored_path` (relatif au projet si le fichier est dans le dossier projet, absolu sinon), durée, résolution, fréquence d'images, présence d'audio. |
| `visual_settings` | Format (9:16, 16:9, 1:1), résolution, netteté (activation, intensité, rayon, seuil) par média. |
| `framing_keyframes` | Keyframes de cadrage : temps, centre X/Y normalisés (0–1), zoom (1–4). |
| `captions` | Segments internes communs (début ms, fin ms, texte), quel que soit le format importé. |
| `caption_presets`, `caption_settings` | Presets personnalisés (JSON) et preset choisi par média. |
| `mask_regions` | Masques : géométrie, forme, sommets, keyframes (JSON), effet `opaque`/`blur`, rayon de flou, bornes temporelles. |
| `broll_assets`, `broll_placements` | Bibliothèque B-roll (catégorie, tags) et insertions manuelles (début timeline, entrée/sortie source, piste). |
| `broll_rules` | Règles explicites mot-clé de caption → clip ou tag (JSON). |
| `audio_assets` | Bibliothèque audio (musique et SFX, catégorie, tags). |
| `music_mixes` | Piste musicale par média : extrait, départ, volume, fondus, bouclage, audio original, ducking manuel (JSON) et ducking sur captions. |
| `sfx_placements` | SFX placés manuellement. |
| `sfx_rules` | Règles événement → SFX (JSON). |
| `timeline_markers` | Marqueurs manuels (temps, libellé). |
| `tasks` | Historique : type, statut (`running`, `succeeded`, `failed`, `cancelled`), entrée, sortie, erreur, horodatage. |

Les colonnes ajoutées par une version ultérieure sont créées automatiquement à l'ouverture d'un ancien projet.

## Trajectoire de tracking (`tracking/*.json`)

```json
{
  "schema_version": 1,
  "source_name": "rush01.mp4",
  "method": "CSRT",
  "fps": 25.0,
  "frame_width": 1920, "frame_height": 1080,
  "start_frame": 50,
  "initial_bbox": [800, 400, 120, 160],
  "samples": [
    {"frame": 50, "time_ms": 2000, "bbox": [800, 400, 120, 160]},
    {"frame": 51, "time_ms": 2040, "bbox": null}
  ]
}
```

`bbox` vaut `null` lorsque le tracker a perdu la cible. Les coordonnées sont en pixels de la source.

## Checkpoints et reprise

`state_media_<id>.json` enregistre, pour chaque étape (`visual`, `composition`, `finishing`, `render`), la clé de cache, le statut, l'empreinte SHA-256 de l'artefact produit et l'éventuelle erreur. Une relance réutilise une étape seulement si sa clé (fichiers source, paramètres, version du module, clés des dépendances) **et** l'empreinte de son artefact sont inchangées.

## Montages du Studio (`montages/`)

Chaque variante générée par le Studio produit :

- `<mode>_<lettre>.plan.json` : le plan de montage complet (extraits source, durées, effets, transitions, look, musique et tempo, ducking, SFX, titres, graine). Le même plan produit toujours le même master.
- `<mode>_<lettre>.mp4` : le master assemblé (rushs coupés, recadrés, étalonnés, enchaînés). Il est enregistré comme média du projet ; ses réglages (captions de titre, preset, musique en boucle avec ducking, SFX, netteté) sont écrits dans la base et restent modifiables dans l'éditeur.
- `<mode>_<lettre>.result.json` : résumé affiché dans le Studio (aperçu, miniature, durée, nombre de plans).

Les médias situés dans `montages/` sont des montages ; tous les autres sont des rushs.

