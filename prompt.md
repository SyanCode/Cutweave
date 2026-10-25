# CUTWEAVE — Cahier des charges et instructions de développement

## 1. Rôle et objectif

Tu es un ingénieur logiciel senior spécialisé en Python, traitement vidéo, FFmpeg, OpenCV classique et interfaces de bureau.

Ta mission est de concevoir et de développer **CUTWEAVE**, une application de montage vidéo automatique fonctionnant localement sur Windows.

CUTWEAVE est un moteur de montage déterministe, modulaire et configurable. Il prend des rushs vidéo et applique une chaîne de traitement composée de trois étapes successives :

1. Traitement visuel : cadrage, tracking et sharpening.
2. Composition : captions, masking et B-roll.
3. Finition : presets captions, musique et effets sonores (SFX).

Le logiciel doit être réellement fonctionnel, avec une interface graphique, une gestion de projets, un système de traitement en arrière-plan et un export vidéo final.

**Tu dois écrire le code, créer les fichiers, installer/configurer les dépendances lorsque l'environnement le permet, exécuter les tests et corriger les erreurs. Ne te contente pas de proposer une architecture ou de générer du pseudo-code.**

## 2. Contraintes absolues

### Aucune intelligence artificielle

Le logiciel ne doit utiliser aucune IA, aucun modèle de machine learning et aucun service d'IA, qu'il soit local ou distant.

Sont notamment interdits :
- LLM et modèles génératifs.
- Modèles de détection d'objets ou de personnes fondés sur le machine learning.
- Modèles de segmentation neuronale.
- Reconnaissance vocale automatique.
- Transcription automatique.
- Embeddings et recherche sémantique.
- APIs cloud, services externes et assistants intégrés.
- Téléchargement automatique de modèles ou de ressources au lancement.

Utiliser exclusivement des algorithmes classiques, des règles déterministes, des paramètres configurables, des fichiers fournis par l'utilisateur et des bibliothèques conventionnelles de traitement multimédia.

OpenCV est autorisé uniquement pour ses algorithmes classiques de vision par ordinateur. Ne pas utiliser ses fonctionnalités DNN ou des modèles préentraînés.

### Fonctionnement local et confidentialité

- Application de bureau Windows.
- Aucun serveur distant requis.
- Aucun compte utilisateur requis.
- Aucune télémétrie, aucun tracking d'utilisation et aucune collecte de données.
- Aucune connexion réseau nécessaire pendant l'exécution.
- Tous les projets, médias, configurations et fichiers temporaires restent sur le disque local.
- Aucun rush original ne doit être modifié ou supprimé.
- Tous les chemins et répertoires de travail doivent être configurables.
- Aucun téléchargement de dépendances ou d'actifs pendant l'utilisation normale du logiciel.

Les dépendances peuvent être installées lors de la préparation du développement. Les binaires et ressources nécessaires à l'exécution doivent ensuite pouvoir être fournis localement.

## 3. Stack technique

Utiliser les technologies suivantes :

- Python 3.12 ou une version compatible avec les dépendances retenues.
- PySide6 pour l'interface graphique.
- FFmpeg et FFprobe pour le traitement, l'analyse technique des médias et le rendu.
- OpenCV pour le tracking classique et les opérations de vision par ordinateur déterministes.
- SQLite pour les métadonnées et l'historique des tâches.
- JSON pour les configurations, presets et instructions de montage.
- pytest pour les tests.
- Logging standard de Python pour les journaux.

Éviter d'ajouter des dépendances lourdes sans nécessité.

Utiliser QThread, QThreadPool ou un mécanisme approprié pour que les opérations longues ne bloquent jamais l'interface graphique.

Ne jamais construire une commande FFmpeg en concaténant directement des chaînes non fiables. Utiliser subprocess avec une liste d'arguments, gérer les chemins correctement et éviter shell=True.

## 4. Interface utilisateur

Créer une interface de bureau moderne, sombre, soignée et professionnelle.

L'application doit comprendre :

### Fenêtre principale

- Barre latérale de navigation.
- Zone de projet courant.
- Import de fichiers et de dossiers.
- Liste des médias avec nom, durée, résolution, fréquence d'images et présence d'audio.
- Prévisualisation vidéo avec lecture, pause et déplacement dans le temps.
- Indicateur de progression du pipeline.
- Journaux accessibles depuis l'interface.
- Bouton de lancement du pipeline complet.
- Boutons permettant de lancer séparément chacune des trois étapes.
- Bouton d'annulation du traitement.
- Accès aux paramètres et à la bibliothèque d'actifs.

### Éditeur d'étapes

Présenter les trois étapes dans des panneaux distincts.

Chaque module doit disposer :
- D'une case d'activation.
- De paramètres éditables.
- De valeurs par défaut raisonnables.
- D'une validation des paramètres.
- D'une possibilité de réinitialisation.
- D'un aperçu lorsque cela est techniquement possible.

Permettre de modifier les paramètres puis de relancer uniquement les étapes affectées.

L'application ne doit pas se limiter à une maquette statique : les boutons, les champs, les sélecteurs de fichiers et les contrôles de progression doivent être réellement fonctionnels.

## 5. Étape 1 — Traitement visuel

### 5.1 Cadrage automatique déterministe

Implémenter un système de cadrage basé sur des règles et des trajectoires configurables.

Fonctionnalités :
- Formats de sortie 9:16, 16:9 et 1:1.
- Définition de la résolution de sortie.
- Position et échelle du cadre.
- Marges de sécurité.
- Keyframes de position et de zoom.
- Interpolation entre les keyframes.
- Lissage des trajectoires.
- Correction manuelle des positions.
- Gestion des limites de l'image source.

Le système doit distinguer clairement le cadrage automatique déterministe du cadrage intelligent par IA, qui est interdit.

Par défaut, sans cible détectée ni trajectoire configurée, utiliser un cadrage centré avec des paramètres modifiables.

### 5.2 Tracking classique

Permettre à l'utilisateur de sélectionner une région, un objet ou un point directement dans la prévisualisation.

Utiliser uniquement des méthodes classiques disponibles dans OpenCV, telles que :
- KCF, CSRT ou MOSSE lorsque disponibles dans la version installée.
- Suivi optique Lucas-Kanade.
- Détection de points caractéristiques classiques.
- Descripteurs et correspondances classiques, si utiles.

Le tracking doit permettre :
- L'initialisation manuelle d'une cible.
- Le suivi sur les images successives.
- La visualisation de la trajectoire.
- La correction manuelle des positions.
- La gestion des pertes de suivi.
- La réinitialisation du tracker.
- L'export de la trajectoire dans un fichier JSON.

Ne jamais prétendre reconnaître automatiquement une personne ou comprendre un objet. Le suivi concerne une cible explicitement sélectionnée ou une caractéristique visuelle suivie par un algorithme classique.

### 5.3 Sharpening

Utiliser le filtre FFmpeg unsharp ou une méthode classique équivalente.

Exposer les paramètres pertinents :
- Activation.
- Intensité.
- Rayon.
- Seuil.
- Prévisualisation et comparaison avant/après si possible.

Prévoir des valeurs par défaut modérées et documenter les limites du filtre.

## 6. Étape 2 — Captions, masking et B-roll

### 6.1 Captions

Aucune transcription automatique n'est autorisée.

Prendre en charge :
- Import SRT.
- Import VTT.
- Import ASS/SSA.
- Création manuelle de sous-titres.
- Modification du texte.
- Modification des timestamps.
- Découpage et fusion de segments.
- Synchronisation avec la timeline.
- Validation des fichiers et signalement des timestamps incorrects.

Conserver les informations de synchronisation dans une représentation interne commune, indépendante du format de fichier d'origine.

### 6.2 Masking

Créer un système de masques déterministes.

Prendre en charge :
- Rectangles.
- Ellipses.
- Polygones.
- Masques fixes.
- Masques animés par keyframes.
- Interpolation de positions, tailles et points.
- Opacité.
- Flou d'une zone.
- Masquage d'une zone.
- Détourage géométrique ou masques dessinés manuellement.

Le suivi d'un masque peut être relié à une trajectoire produite par le tracker classique.

Ne pas implémenter de segmentation neuronale ou de détourage automatique fondé sur l'IA.

### 6.3 B-roll

Créer une bibliothèque de clips locale.

Fonctionnalités :
- Import de clips depuis des dossiers.
- Miniatures et métadonnées.
- Catégories et tags saisis manuellement.
- Recherche par nom, catégorie et tag.
- Prévisualisation des clips.
- Sélection d'un clip et définition des points d'entrée et de sortie.
- Insertion à un timestamp défini.
- Gestion des pistes vidéo.
- Découpage et ajustement des clips.
- Règles d'insertion configurables.

Les clips peuvent être associés à des segments de captions via des tags ou des règles explicites.

Ne pas inventer une compréhension sémantique du contenu. En l'absence de correspondance configurée, ne rien insérer automatiquement.

## 7. Étape 3 — Presets captions, musique et SFX

### 7.1 Presets captions

Créer un système de presets sauvegardables et modifiables.

Chaque preset peut contenir :
- Police et taille.
- Couleur du texte.
- Couleur de surbrillance.
- Contour.
- Ombre.
- Fond.
- Alignement.
- Position.
- Marges.
- Animations déterministes.
- Durées d'animation.
- Style de mise en évidence.

Fournir au moins trois presets initiaux :
- Clean : sous-titres sobres.
- Dynamic : sous-titres avec animation et mise en évidence.
- Cinematic : typographie et positionnement cinématographiques.

Les presets doivent pouvoir être dupliqués, modifiés, exportés et importés.

Les animations doivent être pilotées par des règles et des timestamps, sans génération par IA.

### 7.2 Musique

Créer un système de gestion des pistes musicales.

Fonctionnalités :
- Import de fichiers audio locaux.
- Sélection de la piste.
- Position de départ.
- Points d'entrée et de sortie.
- Découpage.
- Fondu d'entrée et de sortie.
- Volume configurable.
- Bouclage optionnel.
- Ducking configurable pendant les segments contenant de la voix, si l'utilisateur fournit ces segments ou les définit manuellement.

Ne pas utiliser de reconnaissance vocale. Le ducking doit se baser sur des timestamps, des marqueurs ou des règles déterministes.

### 7.3 SFX

Créer un système de placement des effets sonores.

Fonctionnalités :
- Bibliothèque audio locale.
- Catégories personnalisables.
- Position temporelle.
- Volume.
- Fondu d'entrée et de sortie.
- Découpage.
- Déclenchement sur marqueurs ou événements explicites.
- Règles configurables pour associer certains événements à certains effets.

Événements possibles :
- Coupe entre deux clips.
- Début ou fin d'un clip.
- Animation d'un caption.
- Changement de preset.
- Marqueur ajouté manuellement.
- Début ou fin d'une transition.

Ne pas prétendre détecter automatiquement un événement sémantique qui n'est pas explicitement défini.

## 8. Moteur de pipeline

Créer un orchestrateur qui exécute les trois étapes dans l'ordre.

Chaque module doit respecter une interface commune comprenant :
- Identifiant unique.
- Paramètres.
- Validation.
- Entrées et sorties.
- Exécution.
- Progression.
- Journalisation.
- Annulation.
- Gestion des erreurs.

L'orchestrateur doit :
- Exécuter une seule étape ou le pipeline complet.
- Gérer les dépendances entre les étapes.
- Conserver les résultats intermédiaires utiles.
- Éviter de recalculer les étapes inchangées.
- Permettre de reprendre un projet après interruption.
- Afficher la progression par étape.
- Permettre l'annulation propre des processus.
- Conserver un journal des erreurs.
- Ne jamais annoncer un succès avant la fin réelle du traitement.

### Cache

Mettre en place un cache local.

Une modification du preset captions ne doit pas déclencher un nouveau tracking si celui-ci n'est pas affecté.

La clé de cache doit dépendre des fichiers source pertinents, des paramètres de l'étape, de la version du module et des résultats des dépendances.

Prévoir une commande ou une option permettant de purger le cache.

### Rendu

Construire le graphe de filtres FFmpeg et les pistes audio/vidéo à partir des instructions de montage.

Réduire les réencodages inutiles. Lorsque possible, conserver les métadonnées et les instructions de montage jusqu'au rendu final.

Utiliser des fichiers intermédiaires uniquement lorsque le calcul ou la composition le nécessite.

Gérer :
- La synchronisation audio/vidéo.
- Les dimensions de sortie.
- La fréquence d'images.
- Les codecs.
- Les pistes audio multiples.
- Les erreurs FFmpeg.
- Les fichiers temporaires.
- L'annulation du rendu.

## 9. Projets et persistance

Créer un format de projet documenté, par exemple un dossier contenant :
- project.json.
- Une base SQLite pour les métadonnées et les tâches.
- Les références aux médias.
- Les trajectoires de tracking.
- Les keyframes de cadrage et de masques.
- Les captions.
- Les placements B-roll.
- Les règles audio.
- Les presets utilisés.
- Les journaux et métadonnées de rendu.

Par défaut, ne pas dupliquer les rushs dans le projet. Enregistrer des références aux fichiers originaux et signaler les fichiers manquants.

Permettre de sauvegarder, fermer et rouvrir un projet sans perdre ses paramètres.

Ne pas enregistrer de chemins absolus lorsqu'une référence relative stable est possible.

## 10. Organisation du dépôt

Adopter une structure modulaire comparable à celle-ci :

cutweave/
├── pyproject.toml
├── README.md
├── LICENSE
├── .gitignore
├── src/
│   └── cutweave/
│       ├── app.py
│       ├── ui/
│       │   ├── main_window.py
│       │   ├── project_view.py
│       │   ├── preview.py
│       │   ├── pipeline_view.py
│       │   └── settings_view.py
│       ├── core/
│       │   ├── orchestrator.py
│       │   ├── task.py
│       │   ├── cache.py
│       │   ├── project.py
│       │   └── events.py
│       ├── media/
│       │   ├── probe.py
│       │   ├── ffmpeg.py
│       │   └── thumbnails.py
│       ├── stages/
│       │   ├── visual/
│       │   │   ├── framing.py
│       │   │   ├── tracking.py
│       │   │   └── sharpening.py
│       │   ├── composition/
│       │   │   ├── captions.py
│       │   │   ├── masking.py
│       │   │   └── broll.py
│       │   └── finishing/
│       │       ├── caption_presets.py
│       │       ├── music.py
│       │       └── sfx.py
│       ├── render/
│       │   ├── filtergraph.py
│       │   ├── timeline.py
│       │   └── exporter.py
│       ├── storage/
│       │   ├── database.py
│       │   └── project_store.py
│       └── resources/
│           └── presets/
├── tests/
│   ├── test_probe.py
│   ├── test_tracking.py
│   ├── test_captions.py
│   ├── test_timeline.py
│   ├── test_audio_rules.py
│   └── test_pipeline.py
└── scripts/

Cette structure est indicative. L'adapter si le dépôt existant dispose déjà d'une organisation pertinente, sans introduire de complexité inutile.

## 11. Tests et critères d'acceptation

Créer des tests unitaires et d'intégration.

Le MVP est accepté lorsque :
1. L'application démarre sans erreur.
2. L'utilisateur peut créer et rouvrir un projet.
3. Les médias locaux sont analysés correctement.
4. Le cadrage et le sharpening produisent un rendu vérifiable.
5. Le tracking classique peut être initialisé manuellement et exporte une trajectoire.
6. Les captions SRT/ASS sont importées et rendues avec un preset.
7. Les masques configurés sont visibles dans le rendu.
8. Un clip B-roll peut être inséré dans une chronologie.
9. La musique et les SFX sont positionnés et mixés correctement.
10. Le rendu final est un MP4 lisible, avec synchronisation cohérente.
11. L'annulation d'une tâche ne laisse pas de projet déclaré comme terminé.
12. Le cache évite les recalculs inutiles.
13. Le logiciel fonctionne sans accès Internet après installation.
14. Aucune fonction IA n'est présente dans les dépendances ou le code.
15. Les erreurs de média ou de configuration sont affichées clairement.

Utiliser de petits médias synthétiques pour les tests automatisés, afin d'éviter de dépendre de fichiers externes ou de contenus protégés.

## 12. Installation et documentation

Créer un README complet en français comprenant :
- Prérequis.
- Installation.
- Vérification de FFmpeg et FFprobe.
- Lancement de l'application.
- Exécution des tests.
- Organisation des projets.
- Fonctionnement des trois étapes.
- Configuration des presets.
- Limites des algorithmes classiques de tracking.
- Procédure de construction d'un exécutable Windows si possible.

Fournir un fichier de dépendances verrouillable et documenter les licences des dépendances ainsi que les éventuelles contraintes de redistribution de FFmpeg.

Ne pas déclarer une construction Windows fonctionnelle sans l'avoir testée.

## 13. Méthode de développement obligatoire

Travaille par incréments vérifiables.

Phase A : inspecter le dépôt et l'environnement existants. Identifier les outils disponibles, les versions de Python et la présence de FFmpeg.

Phase B : créer le socle du projet, les modèles de données, la gestion des projets et l'interface principale.

Phase C : implémenter l'import média, le moteur FFmpeg et l'export de base.

Phase D : implémenter l'étape 1, puis l'étape 2, puis l'étape 3.

Phase E : intégrer le pipeline, le cache, l'annulation et la reprise.

Phase F : tester l'ensemble, corriger les problèmes et compléter la documentation.

À chaque phase :
- Écrire du code réel.
- Exécuter les tests disponibles.
- Corriger les erreurs découvertes.
- Mettre à jour la documentation.
- Signaler honnêtement ce qui fonctionne et ce qui reste incomplet.

Ne pas remplacer une fonctionnalité demandée par un bouton décoratif ou une fausse simulation. Si une fonctionnalité est trop complexe pour le premier incrément, fournir une version minimale réellement utilisable et documenter précisément ses limites.

Ne pas réécrire aveuglément un dépôt existant. Examiner les fichiers avant de les modifier et préserver le travail déjà présent.

## 14. Première action

Commence maintenant par inspecter le dépôt et l'environnement. Ensuite, mets en place le socle exécutable de CUTWEAVE et réalise le premier incrément fonctionnel.

Ne te limite pas à un plan de développement : passe immédiatement à l'implémentation, exécute les vérifications possibles et présente un bilan précis des fichiers créés, des fonctionnalités opérationnelles et des prochaines étapes.
