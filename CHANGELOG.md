# Journal des changements

Format inspiré de [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/) ; versionnage sémantique.

## [Non publié]

## [0.2.0]

### Ajouté
- **Studio** : montage automatique à partir des rushs et d'un style (Edit, Vlog, Cinématique, Highlights, Souvenirs, Face cam),
  avec jusqu'à trois variantes déterministes (Signature, Énergique, Posée), aperçus rapides et rendu final à l'export.
- **Analyse des rushs** par traitement du signal : mouvement, netteté, luminosité, niveau sonore, changements de plan,
  parole, périodicité de la voix (bruits et toux écartés), syllabes.
- **Sous-titres calés sur la voix** à partir d'un script (sans reconnaissance vocale) : alignement des syllabes et des pauses,
  regroupement lisible, report sur le montage, mode phrases ou mot à mot ; plages de parole à compléter dans l'éditeur.
- **Bibliothèque intégrée** synthétisée localement : 6 musiques, 11 effets sonores, 8 looks, 7 styles de titres.
- Détection du tempo des musiques importées pour couper sur le temps.
- Encodage accéléré : encodeurs matériels (NVENC, Quick Sync, AMF) après encodage d'essai, repli automatique.
- Interface refaite : thème unifié, icônes vectorielles, boutons homogènes, mise en page adaptative (960 × 600 à 4K),
  barre latérale réductible, notifications, projets récents, raccourcis clavier, glisser-déposer sur toute la fenêtre.
- Vérification de l'environnement (FFmpeg, filtres, encodeurs) et rapport de diagnostic copiable.
- Logo, icône Windows, fenêtre « À propos ».
- Commande `cutweave-cli auto`.
- Élagage automatique du cache des projets.

### Modifié
- Le montage n'utilise plus que des filtres FFmpeg présents dans les builds LGPL.
- Les processus FFmpeg ne font plus apparaître de fenêtre console sous Windows.
- Titre d'ouverture et texte de fin facultatifs et vides par défaut.

## [0.1.0]

- Première version : pipeline en trois étapes (visuel, composition, finition), tracking classique, masques, B-roll, captions,
  presets, musique avec ducking, SFX, règles explicites, cache, reprise, annulation, CLI.
