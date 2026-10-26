# Sous-titres calés sur la voix

CUTWEAVE ne reconnaît pas la parole : le cahier des charges interdit toute IA, y compris la transcription automatique.
Ce qu'il sait faire, avec du traitement du signal classique, c'est **dater précisément chaque mot d'un texte que tu lui fournis**.

## Utilisation

- **Studio, étape 4** : coche « Ajouter des sous-titres », colle le texte prononcé (ou importe un `.txt`, `.srt`, `.vtt`),
  choisis *Phrases* ou *Mot à mot*, puis génère. Le script suit les rushs dans l'ordre chronologique (date de modification, puis nom).
- **Éditeur, onglet Captions** : *Aligner un script…* cale un texte sur la voix du média sélectionné ; *Plages de parole* crée
  un segment « … » à chaque passage parlé, avec les bons temps, pour n'avoir plus qu'à écrire.
- Les indications `[entre crochets]` du script sont ignorées. Les `.srt`/`.vtt` importés comme script ne servent que pour leur texte.

## Comment ça marche

1. **Syllabes du son.** Le son est analysé dans la bande de la voix (250–2500 Hz) ; chaque voyelle forme un pic d'énergie.
   Seuls les pics situés dans de la voix comptent : une mesure de *périodicité* (autocorrélation entre 70 et 400 Hz) écarte toux,
   claquements, souffle et bruits de bouche.
2. **Syllabes du texte.** Chaque mot est découpé selon des règles de prononciation française (groupes vocaliques, *e* muet,
   *qu*/*gu*, hiatus, nombres en toutes lettres, sigles épelés). Les virgules, points et paragraphes annoncent des pauses.
3. **Alignement.** Une programmation dynamique apparie les deux suites : un coût pour une syllabe non détectée ou parasite, un
   coût quand la pause attendue ne correspond pas à la pause mesurée. Une erreur de comptage est absorbée.
4. **Frontières de mots.** Placées au creux d'énergie entre deux syllabes, ou au début et à la fin du souffle autour d'une pause.
5. **Sous-titres.** Les mots sont reportés sur la timeline du montage (les mots coupés disparaissent), regroupés en cartons de
   deux lignes de 30 caractères au plus, coupés aux fins de phrase, aux virgules et aux silences, sans recouvrement.

Mesures sur voix synthétique : erreur moyenne de 30 ms avec un script exact, 95 à 140 ms avec des erreurs de syllabes ou des
mots manquants. Une voix réelle est moins régulière : compte quelques dizaines de millisecondes de plus.

## Limites

- Le texte doit être **celui qui est prononcé**, dans l'ordre. CUTWEAVE refuse un script trop éloigné de la voix (fiabilité
  inférieure à 45 %) et explique pourquoi.
- Les répétitions, hésitations et improvisations absentes du script dégradent localement la précision.
- Une musique ou un bruit de fond forts peuvent masquer des syllabes ; une voix chuchotée n'a pas de hauteur et est traitée
  comme du bruit.
- Une seule langue à la fois, avec des règles pensées pour le français (elles fonctionnent approximativement pour l'anglais).
