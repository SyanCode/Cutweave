"""Garde-fous des contraintes absolues : aucune IA, aucun accès réseau, aucun shell."""

import re
import tomllib
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "src" / "cutweave"
PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"

FORBIDDEN_CODE = {
    r"cv2\.dnn|dnn_superres|readNet\w*\(": "modèle OpenCV DNN",
    r"CascadeClassifier|haarcascade|HOGDescriptor_getDefaultPeopleDetector|FaceDetectorYN|FaceRecognizerSF":
        "détecteur préentraîné",
    r"^\s*(import|from)\s+(torch|tensorflow|keras|onnx\w*|openvino|mediapipe|whisper|vosk|transformers|"
    r"sklearn|openai|anthropic|ultralytics)\b": "bibliothèque d'IA",
    r"^\s*(import|from)\s+(requests|httpx|aiohttp|socket|urllib\.request|http\.client|ftplib|smtplib)\b":
        "accès réseau",
    r"shell\s*=\s*True": "subprocess avec shell",
    r"os\.system\(": "commande shell",
}


def test_source_contains_no_ai_network_or_shell_usage():
    violations = []
    for path in SOURCE.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for pattern, label in FORBIDDEN_CODE.items():
            for match in re.finditer(pattern, text, flags=re.MULTILINE):
                line = text.count("\n", 0, match.start()) + 1
                violations.append(f"{path.relative_to(SOURCE)}:{line} {label}")
    assert violations == []


def test_declared_dependencies_are_conventional_only():
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]
    names = {re.split(r"[<>=!~\[ ]", item, maxsplit=1)[0].casefold() for item in project["dependencies"]}
    assert names == {"pyside6-essentials", "pyside6-addons", "opencv-contrib-python-headless"}
