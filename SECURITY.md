# Politique de sécurité

## Versions prises en charge

Seule la dernière version publiée reçoit des correctifs de sécurité.

## Signaler une vulnérabilité

Ne publie pas de vulnérabilité dans un ticket public. Utilise le signalement privé de la plateforme d'hébergement
(« Report a vulnerability » dans l'onglet *Security* du dépôt) ou contacte les mainteneurs en privé. Décris le problème,
les étapes pour le reproduire et son impact ; tu recevras un accusé de réception sous quelques jours.

## Périmètre et garanties de conception

- CUTWEAVE n'ouvre aucune connexion réseau et n'envoie aucune donnée (vérifié par un test sur le code source).
- Les commandes FFmpeg sont des listes d'arguments, jamais passées à un shell ; les chemins et noms de fichiers issus de
  l'utilisateur ne sont pas interprétés comme commandes.
- Les fichiers de projet (JSON, SQLite) et les captions importées sont analysés strictement et leurs échappements ASS neutralisés.
- Les rushs ne sont jamais modifiés ; les sorties sont écrites de façon atomique (fichier temporaire puis remplacement).

Les failles pertinentes concernent par exemple : exécution de code via un fichier de projet, de captions ou de script
importé, traversée de chemin, ou injection dans une ligne de commande FFmpeg.
