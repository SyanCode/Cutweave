"""Interface en ligne de commande : vérification de l'environnement et rendu sans interface graphique.

Exemples ::

    cutweave-cli check
    cutweave-cli info  D:\\Projets\\demo
    cutweave-cli render D:\\Projets\\demo --media rush01.mp4 --output D:\\exports\\rush01.mp4
    cutweave-cli render D:\\Projets\\demo --media rush01.mp4 --stage visual
    cutweave-cli purge-cache D:\\Projets\\demo
    cutweave-cli auto D:\\Projets\\demo D:\\Rushs\\Vacances --mode vlog --aspect 16:9 --variants 3
"""

from __future__ import annotations

import argparse
import shutil
import sys
import threading

from cutweave.core.cache import StageCache
from cutweave.core.pipeline import STAGE_LABELS, PipelineInputError, collect_job, run_job, stage_statuses
from cutweave.core.settings import AppSettings
from cutweave.storage.project_store import ProjectStore


def _find_media(store: ProjectStore, name: str):
    media = store.list_media()
    matches = [item for item in media if item.name.casefold() == name.casefold() or str(item.id) == name]
    if not matches:
        available = ", ".join(item.name for item in media) or "aucun"
        raise SystemExit(f"Média introuvable dans le projet : {name} (disponibles : {available})")
    return matches[0]


def _check(_args) -> int:
    from cutweave.media.probe import ffprobe_path
    from cutweave.media.render import ffmpeg_path
    from cutweave.stages.visual.tracking import available_trackers
    from cutweave.ui.dialogs import tool_report

    print(tool_report(ffmpeg_path() or shutil.which("ffmpeg"), ffprobe_path() or shutil.which("ffprobe")))
    print("Trackers OpenCV classiques : " + (", ".join(available_trackers()) or "aucun"))
    return 0 if ffmpeg_path() and ffprobe_path() else 1


def _info(args) -> int:
    store = ProjectStore(args.project)
    print(f"Projet : {store.manifest['name']} ({store.root})")
    print("Modules : " + ", ".join(f"{name}={'on' if enabled else 'off'}" for name, enabled in store.modules().items()))
    for item in store.list_media():
        state = " [MANQUANT]" if item.missing else ""
        print(f"- #{item.id} {item.name}{state} : {item.duration or 0:.2f} s, {item.width}×{item.height}")
        for stage, entry in stage_statuses(store, item.id).items():
            print(f"    {STAGE_LABELS.get(stage, stage)} : {entry.get('status')}")
    return 0


def _render(args) -> int:
    store = ProjectStore(args.project)
    media = _find_media(store, args.media)
    if args.stage is None and not args.output:
        raise SystemExit("--output est requis pour le pipeline complet.")
    cancel = threading.Event()
    last_decile: dict[str, int] = {}

    def progress(event):
        label = STAGE_LABELS.get(event.stage, event.stage)
        if event.message:
            print(f"[{label}] {event.message}", flush=True)
        elif event.fraction is not None:
            decile = int(event.fraction * 10)
            if last_decile.get(event.stage) != decile:
                last_decile[event.stage] = decile
                print(f"[{label}] {decile * 10} %", flush=True)

    try:
        job = collect_job(store, media)
        output = run_job(store, job, target_stage=args.stage, destination=args.output, cancel_event=cancel,
                         on_progress=progress, force=("visual", "composition", "finishing") if args.force else ())
    except KeyboardInterrupt:
        cancel.set()
        print("Annulé.", file=sys.stderr)
        return 130
    except PipelineInputError as exc:
        print(f"Configuration invalide : {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # message lisible ; détail dans le journal
        print(f"Échec : {exc}", file=sys.stderr)
        return 1
    print(f"Terminé : {output}")
    return 0


def _auto(args) -> int:
    from pathlib import Path

    from cutweave.autoedit.service import StudioError, StudioRequest, generate_variants, rush_media
    from cutweave.media.files import collect_video_files
    from cutweave.media.probe import MediaProbeError, probe_media

    root = Path(args.project).expanduser()
    store = ProjectStore(root) if (root / "project.json").is_file() else ProjectStore.create(root)
    for path in collect_video_files(args.rushes) if args.rushes else ():
        try:
            store.add_media(path, probe_media(path))
        except MediaProbeError as exc:
            print(f"Ignoré : {exc}", file=sys.stderr)
    rushes = tuple(str(media.path) for media in rush_media(store) if not media.missing)
    request = StudioRequest(rushes, args.mode, args.aspect, args.quality, args.duration, args.music, args.look,
                            args.fit, args.title, args.outro, args.variants, draft=args.draft)
    cancel = threading.Event()
    last = [-1]

    def progress(event):
        decile = int((event.fraction or 0) * 20)
        if event.message:
            print(f"[{decile * 5:3d} %] {event.message}", flush=True)
        elif decile != last[0]:
            last[0] = decile
            print(f"[{decile * 5:3d} %]", flush=True)

    try:
        results = generate_variants(store, request, on_progress=progress, cancel_event=cancel)
    except KeyboardInterrupt:
        cancel.set()
        print("Annulé.", file=sys.stderr)
        return 130
    except (StudioError, ValueError) as exc:
        print(f"Configuration invalide : {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Échec : {exc}", file=sys.stderr)
        return 1
    for result in results:
        print(f"Variante {result.letter} ({result.name}) : {result.preview}  [{result.duration:.1f} s, "
              f"{result.shots} plans, {result.music_title}, {result.look_title}]")
    return 0


def _purge(args) -> int:
    store = ProjectStore(args.project)
    removed = StageCache(store.cache_root).purge()
    print(f"{removed} artefact(s) supprimé(s) du cache.")
    return 0


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        # Sortie redirigée (fichier, pipe) : UTF-8 plutôt que la page de code Windows.
        if hasattr(stream, "reconfigure") and not stream.isatty():
            stream.reconfigure(encoding="utf-8", errors="replace")
    AppSettings.load().apply()
    parser = argparse.ArgumentParser(prog="cutweave-cli", description="CUTWEAVE sans interface graphique.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="Vérifie FFmpeg, FFprobe et les trackers disponibles.").set_defaults(func=_check)
    info = commands.add_parser("info", help="Résume un projet et l'état de ses étapes.")
    info.add_argument("project")
    info.set_defaults(func=_info)
    render = commands.add_parser("render", help="Exécute une étape ou le pipeline complet.")
    render.add_argument("project")
    render.add_argument("--media", required=True, help="Nom de fichier ou identifiant du média.")
    render.add_argument("--output", help="Fichier MP4 de sortie (pipeline complet).")
    render.add_argument("--stage", choices=("visual", "composition", "finishing"))
    render.add_argument("--force", action="store_true",
                        help="Recalcule les étapes 1 à 3 ; le cache de rendu reste utilisé (purge-cache pour réencoder).")
    render.set_defaults(func=_render)
    purge = commands.add_parser("purge-cache", help="Supprime le cache de rendu et de tracking du projet.")
    purge.add_argument("project")
    purge.set_defaults(func=_purge)
    from cutweave.assets.library import LOOKS
    from cutweave.autoedit.modes import MODES

    auto = commands.add_parser("auto", help="Montage automatique : rushs + style -> variantes prêtes à exporter.")
    auto.add_argument("project", help="Dossier du projet (créé s'il n'existe pas).")
    auto.add_argument("rushes", nargs="*", help="Vidéos ou dossiers à ajouter comme rushs.")
    auto.add_argument("--mode", choices=[mode.key for mode in MODES], default="edit")
    auto.add_argument("--aspect", choices=("9:16", "16:9", "1:1"), default="9:16")
    auto.add_argument("--quality", type=int, default=1080, help="Petit côté de la sortie en pixels (défaut 1080).")
    auto.add_argument("--duration", type=float, help="Durée visée en secondes (défaut : selon le style).")
    auto.add_argument("--music", default="auto", help="auto, none, clé intégrée (pulse, sunday…) ou fichier audio.")
    auto.add_argument("--look", choices=[look.key for look in LOOKS])
    auto.add_argument("--fit", choices=("fill", "fit_blur"), default="fill")
    auto.add_argument("--title", default="")
    auto.add_argument("--outro", default="")
    auto.add_argument("--variants", type=int, choices=(1, 2, 3), default=3)
    auto.add_argument("--draft", action="store_true",
                      help="Aperçus rapides en 540p (refaits à la qualité demandée à l'export depuis l'application).")
    auto.set_defaults(func=_auto)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
