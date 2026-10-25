import subprocess

import pytest

from cutweave.cli import main
from cutweave.media.probe import ffprobe_path, probe_media
from cutweave.media.render import VisualSettings, ffmpeg_path
from cutweave.storage.project_store import ProjectStore


def test_info_lists_media_and_missing_files(tmp_path, capsys):
    store = ProjectStore.create(tmp_path / "p", name="Démo")

    class Info:
        duration, width, height, frame_rate, has_audio = 2.0, 64, 36, 25.0, False

    ghost = tmp_path / "absent.mp4"
    ghost.write_bytes(b"x")
    store.add_media(ghost, Info)
    ghost.unlink()
    assert main(["info", str(store.root)]) == 0
    output = capsys.readouterr().out
    assert "Démo" in output and "absent.mp4 [MANQUANT]" in output


def test_render_reports_configuration_errors_without_traceback(tmp_path, capsys):
    store = ProjectStore.create(tmp_path / "p")

    class Info:
        duration, width, height, frame_rate, has_audio = 2.0, 64, 36, 25.0, False

    ghost = tmp_path / "absent.mp4"
    ghost.write_bytes(b"x")
    store.add_media(ghost, Info)
    ghost.unlink()
    code = main(["render", str(store.root), "--media", "absent.mp4", "--output", str(tmp_path / "o.mp4")])
    assert code == 2
    assert "introuvable" in capsys.readouterr().err


@pytest.mark.skipif(not ffmpeg_path() or not ffprobe_path(), reason="FFmpeg/FFprobe non installés")
def test_render_full_pipeline_from_cli(tmp_path, capsys):
    source = tmp_path / "clip.mp4"
    subprocess.run([ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc2=size=160x90:rate=25:duration=1", "-c:v", "mpeg4", str(source)],
                   check=True, capture_output=True)
    store = ProjectStore.create(tmp_path / "p")
    media = store.add_media(source, probe_media(source))
    store.set_visual_settings(media.id, VisualSettings("1:1", 90, 90, 0))
    output = tmp_path / "carre.mp4"
    assert main(["render", str(store.root), "--media", "clip.mp4", "--output", str(output)]) == 0
    assert probe_media(output).width == 90
    assert "Terminé" in capsys.readouterr().out
    assert main(["purge-cache", str(store.root)]) == 0
