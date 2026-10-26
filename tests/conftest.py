"""Réglages communs : pas de vérification d'environnement en arrière-plan pendant les tests de l'interface."""

import os

os.environ.setdefault("CUTWEAVE_NO_HEALTHCHECK", "1")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
