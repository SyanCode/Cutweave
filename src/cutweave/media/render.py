"""Construction et exécution sécurisée de commandes FFmpeg."""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Sequence

from cutweave.core.proc import popen_hidden, run_hidden
from cutweave.stages.composition.captions import CaptionCue, sanitize_caption_markup
from cutweave.stages.composition.broll import BrollRenderClip
from cutweave.stages.composition.masking import MaskRegion
from cutweave.stages.finishing.music import MusicRenderTrack, SfxRenderClip
from cutweave.stages.finishing.caption_presets import BUILTIN_BY_NAME, CaptionPreset
from cutweave.stages.visual.framing import FramingKeyframe, interpolation_expression, validate_keyframes


LOGGER = logging.getLogger("cutweave.render")


class RenderError(RuntimeError):
    pass


@dataclass(frozen=True)
class VisualSettings:
    aspect: str = "9:16"
    width: int = 1080
    height: int = 1920
    sharpening: float = 0.35
    framing_keyframes: tuple[FramingKeyframe, ...] = ()
    sharpening_enabled: bool = True
    sharpening_radius: int = 5
    sharpening_threshold: int = 0

    def validate(self) -> None:
        if self.aspect not in {"9:16", "16:9", "1:1"}:
            raise ValueError("Le format doit être 9:16, 16:9 ou 1:1.")
        if self.width < 16 or self.height < 16 or self.width > 7680 or self.height > 7680:
            raise ValueError("La résolution doit être comprise entre 16 et 7680 pixels par dimension.")
        if self.width % 2 or self.height % 2:
            raise ValueError("La largeur et la hauteur doivent être paires pour l'encodage MP4.")
        if not 0 <= self.sharpening <= 2:
            raise ValueError("L'intensité de netteté doit être comprise entre 0 et 2.")
        if self.sharpening_radius < 3 or self.sharpening_radius > 23 or self.sharpening_radius % 2 == 0:
            raise ValueError("Le rayon de netteté doit être impair et compris entre 3 et 23.")
        if not 0 <= self.sharpening_threshold <= 255:
            raise ValueError("Le seuil de netteté doit être compris entre 0 et 255.")
        validate_keyframes(self.framing_keyframes)
        expected = {"9:16": 9 / 16, "16:9": 16 / 9, "1:1": 1.0}[self.aspect]
        if abs(self.width / self.height - expected) > 0.02:
            raise ValueError("La résolution ne correspond pas au format sélectionné.")


def build_visual_filter(
    settings: VisualSettings,
    *,
    caption_filename: str | None = None,
    masks: Sequence[MaskRegion] = (),
) -> str:
    settings.validate()
    if settings.framing_keyframes:
        zoom = interpolation_expression(settings.framing_keyframes, "zoom")
        center_x = interpolation_expression(settings.framing_keyframes, "center_x")
        center_y = interpolation_expression(settings.framing_keyframes, "center_y")
        filters = [
            f"scale=w='{settings.width}*({zoom})':h='{settings.height}*({zoom})':"
            "force_original_aspect_ratio=increase:force_divisible_by=2:eval=frame",
            f"crop={settings.width}:{settings.height}:"
            f"x='min(max((iw-ow)*({center_x}),0),iw-ow)':"
            f"y='min(max((ih-oh)*({center_y}),0),ih-oh)'",
            "setsar=1",
        ]
    else:
        filters = [
            f"scale={settings.width}:{settings.height}:force_original_aspect_ratio=increase",
            f"crop={settings.width}:{settings.height}",
            "setsar=1",
        ]
    if settings.sharpening_enabled and settings.sharpening > 0:
        amount = settings.sharpening
        radius = settings.sharpening_radius
        filters.append(f"unsharp={radius}:{radius}:{amount:.3f}:{radius}:{radius}:0.000")
    filters.extend(mask.filter_fragment(settings.width, settings.height) for mask in masks)
    if caption_filename is not None:
        filters.append(_caption_filter(caption_filename))
    return ",".join(filters)


def _caption_filter(caption_filename: str) -> str:
    """Les captions sont incrustées en dernier, au-dessus des masques et du B-roll."""
    if not re.fullmatch(r"cutweave_[a-f0-9]{32}\.ass", caption_filename):
        raise ValueError("Nom de fichier temporaire de captions invalide.")
    return f"ass=filename='{caption_filename}'"


def extract_preview_frame(
    source: str | Path,
    settings: VisualSettings,
    time_ms: int,
    *,
    sharpening: bool = True,
    cancel_event=None,
) -> bytes:
    """Extrait une image PNG au timestamp demandé pour la comparaison visuelle."""
    source_path = Path(source).expanduser().resolve()
    if not source_path.is_file():
        raise RenderError("Le média source de l'aperçu est introuvable.")
    if time_ms < 0:
        raise ValueError("Le timestamp de l'aperçu ne peut pas être négatif.")
    if cancel_event is not None and cancel_event.is_set():
        raise RenderError("Aperçu annulé.")
    binary = ffmpeg_path()
    if not binary:
        raise RenderError("FFmpeg est introuvable.")
    frame_settings = replace(settings, sharpening_enabled=settings.sharpening_enabled and sharpening)
    command = [binary, "-hide_banner", "-loglevel", "error", "-ss", f"{time_ms / 1000:.3f}",
               "-i", str(source_path), "-frames:v", "1"]
    if frame_settings.sharpening_enabled and frame_settings.sharpening > 0 and frame_settings.sharpening_threshold > 0:
        unsharpened = replace(frame_settings, sharpening_enabled=False)
        radius = frame_settings.sharpening_radius
        graph = (
            f"[0:v]setpts=PTS+{time_ms / 1000:.3f}/TB,{build_visual_filter(unsharpened)}[preview_base];"
            "[preview_base]split=2[preview_source][preview_work];"
            f"[preview_work]unsharp={radius}:{radius}:{frame_settings.sharpening:.3f}:"
            f"{radius}:{radius}:0.000[preview_unsharp];"
            f"[preview_source][preview_unsharp]blend=all_expr='if(gt(abs(A-B),"
            f"{frame_settings.sharpening_threshold}),B,A)'[preview_out]"
        )
        command.extend(["-filter_complex", graph, "-map", "[preview_out]"])
    else:
        vf = f"setpts=PTS+{time_ms / 1000:.3f}/TB,{build_visual_filter(frame_settings)}"
        command.extend(["-vf", vf])
    command.extend(["-f", "image2pipe", "-vcodec", "png", "pipe:1"])
    try:
        process = popen_hidden(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as exc:
        raise RenderError(f"Impossible de démarrer FFmpeg : {exc}") from exc
    started = time.monotonic()
    try:
        while True:
            try:
                image, error = process.communicate(timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                if cancel_event is not None and cancel_event.is_set():
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    raise RenderError("Aperçu annulé.")
                if time.monotonic() - started > 60:
                    process.kill()
                    process.wait()
                    raise RenderError("L'aperçu a dépassé 60 secondes.")
        if process.returncode != 0 or not image:
            detail = error.decode("utf-8", errors="replace")[-1000:]
            raise RenderError(f"Impossible de générer l'aperçu : {detail or 'aucune image produite'}")
        return image
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def _append_mask_graph(filters: list[str], previous: str, masks: Sequence[MaskRegion], settings: VisualSettings) -> str:
    """Applique les masques dans l'ordre, avec flux séparé pour les zones floutées."""
    for index, mask in enumerate(masks):
        mask.validate(settings.width, settings.height)
        output = f"mask_out_{index}"
        if mask.effect == "opaque":
            filters.append(f"[{previous}]{mask.filter_fragment(settings.width, settings.height)}[{output}]")
        else:
            base, blur_source, blurred = f"mask_base_{index}", f"mask_blur_src_{index}", f"mask_blurred_{index}"
            filters.append(f"[{previous}]split=2[{base}][{blur_source}]")
            filters.append(
                f"[{blur_source}]gblur=sigma={mask.blur_radius}:steps=2[{blurred}]"
            )
            opacity = mask.opacity_expression()
            condition = re.sub(r"\bX\b", f"(X*{settings.width}/W)", mask.active_expression())
            condition = re.sub(r"\bY\b", f"(Y*{settings.height}/H)", condition)
            expression = (
                f"if({condition},A*(1-{opacity})+B*{opacity},A)"
            )
            filters.append(f"[{base}][{blurred}]blend=all_expr='{expression}'[{output}]")
        previous = output
    return previous


def _append_threshold_sharpen_graph(
    filters: list[str], previous: str, settings: VisualSettings, *, label: str = "threshold_sharp",
) -> str:
    """Sharpen only component values whose change exceeds the configured threshold."""
    source = f"{label}_source"
    sharpened = f"{label}_unsharp"
    radius = settings.sharpening_radius
    filters.extend([
        f"[{previous}]split=2[{source}][{label}_work]",
        f"[{label}_work]unsharp={radius}:{radius}:{settings.sharpening:.3f}:{radius}:{radius}:0.000[{sharpened}]",
        f"[{source}][{sharpened}]blend=all_expr='if(gt(abs(A-B),{settings.sharpening_threshold}),B,A)'[{label}]",
    ])
    return label


def ffmpeg_path() -> str | None:
    configured = os.environ.get("CUTWEAVE_FFMPEG")
    return configured if configured else shutil.which("ffmpeg")


def select_video_encoder(available: set[str]) -> str:
    """Choisit un encodeur vidéo H.264 compatible, puis MPEG-4 classique."""
    for candidate in ("libx264", "libopenh264", "h264_mf", "mpeg4"):
        if candidate in available:
            return candidate
    raise RenderError("Aucun encodeur vidéo compatible n'est disponible dans cette installation de FFmpeg.")


def _available_video_encoders(binary: str) -> set[str]:
    try:
        result = run_hidden(
            [binary, "-hide_banner", "-encoders"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=15, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RenderError(f"Impossible de lire les encodeurs FFmpeg : {exc}") from exc
    if result.returncode:
        raise RenderError("Impossible d'énumérer les encodeurs de FFmpeg.")
    return {
        match.group(1)
        for line in result.stdout.splitlines()
        if (match := re.match(r"\s*V\S*\s+([a-zA-Z0-9_]+)\s", line))
    }


def _ass_timestamp(milliseconds: int, *, end: bool = False) -> str:
    centiseconds = (milliseconds + 9) // 10 if end else milliseconds // 10
    hours, remainder = divmod(centiseconds, 360_000)
    minutes, remainder = divmod(remainder, 6_000)
    seconds, fraction = divmod(remainder, 100)
    return f"{hours}:{minutes:02}:{seconds:02}.{fraction:02}"


def _ass_color(color: str, *, alpha: int = 0) -> str:
    red, green, blue = color[1:3], color[3:5], color[5:7]
    return f"&H{alpha:02X}{blue}{green}{red}&"


def _ducking_expression(segments) -> str:
    expression = "1"
    for segment in reversed(segments):
        start, end = segment.start_ms / 1000, segment.end_ms / 1000
        fade = segment.fade_ms / 1000
        level = segment.level
        if fade == 0:
            inside = f"if(lt(t\\,{end:.3f})\\,{level:.4f}\\,1)"
            expression = f"if(lt(t\\,{start:.3f})\\,{expression}\\,{inside})"
        else:
            fade_in_end, fade_out_start = start + fade, end - fade
            inside = (
                f"if(lt(t\\,{fade_in_end:.3f})\\,1-(1-{level:.4f})*(t-{start:.3f})/{fade:.3f}\\,"
                f"if(lt(t\\,{fade_out_start:.3f})\\,{level:.4f}\\,"
                f"if(lt(t\\,{end:.3f})\\,{level:.4f}+(1-{level:.4f})*(t-{fade_out_start:.3f})/{fade:.3f}\\,1)))"
            )
            expression = f"if(lt(t\\,{start:.3f})\\,{expression}\\,{inside})"
    return expression


def build_ass_content(
    cues: Sequence[CaptionCue], settings: VisualSettings, preset: CaptionPreset | None = None,
) -> str:
    settings.validate()
    preset = preset or BUILTIN_BY_NAME["clean"]
    preset.validate()
    ordered = tuple(sorted(cues, key=lambda cue: (cue.start_ms, cue.end_ms)))
    for cue in ordered:
        cue.validate()
    font_size = max(8, round(preset.font_size * settings.height / 1080))
    margin_lr = round(settings.width * preset.margin_horizontal_percent / 100)
    margin_v = round(settings.height * preset.margin_vertical_percent / 100)
    back_color = _ass_color(preset.background_color, alpha=255 - preset.background_opacity)
    border_style = 3 if preset.background_opacity > 0 else 1
    bold = -1 if preset.bold else 0
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {settings.width}",
        f"PlayResY: {settings.height}", "WrapStyle: 2", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,{preset.font_name},{font_size},{_ass_color(preset.text_color)},"
        f"{_ass_color(preset.highlight_color)},{_ass_color(preset.outline_color)},"
        f"{back_color},{bold},0,0,0,100,100,0,0,{border_style},{preset.outline_width:.2f},"
        f"{preset.shadow:.2f},{preset.alignment},{margin_lr},{margin_lr},{margin_v},1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for cue in ordered:
        tags = []
        if preset.highlight_mode == "cue":
            tags.append(f"\\1c{_ass_color(preset.highlight_color)}")
        if preset.animation == "fade" and preset.animation_ms:
            fade_in = min(preset.animation_ms, cue.end_ms - cue.start_ms)
            fade_out = min(preset.animation_ms, cue.end_ms - cue.start_ms)
            tags.append(f"\\fad({fade_in},{fade_out})")
        elif preset.animation == "pop" and preset.animation_ms:
            duration = min(preset.animation_ms, cue.end_ms - cue.start_ms)
            tags.append(f"\\fscx75\\fscy75\\t(0,{duration},\\fscx100\\fscy100)")
        text = sanitize_caption_markup(cue.text)
        if tags:
            text = "{" + "".join(tags) + "}" + text
        lines.append(
            f"Dialogue: 0,{_ass_timestamp(cue.start_ms)},{_ass_timestamp(cue.end_ms, end=True)},Default,,0,0,0,,{text}"
        )
    return "\n".join(lines) + "\n"


def build_render_command(
    source: str | Path,
    destination: str | Path,
    settings: VisualSettings,
    *,
    caption_filename: str | None = None,
    masks: Sequence[MaskRegion] = (),
    broll: Sequence[BrollRenderClip] = (),
    music: MusicRenderTrack | None = None,
    sfx: Sequence[SfxRenderClip] = (),
    source_has_audio: bool | None = None,
) -> list[str]:
    settings.validate()
    binary = ffmpeg_path()
    if not binary:
        raise RenderError("FFmpeg est introuvable. Installez FFmpeg ou définissez CUTWEAVE_FFMPEG.")
    encoder = select_video_encoder(_available_video_encoders(binary))
    encoding_args = ["-preset", "medium", "-crf", "20"] if encoder == "libx264" else ["-b:v", "8M"]
    if encoder == "mpeg4":
        encoding_args = ["-q:v", "3"]
    command = [binary, "-hide_banner", "-loglevel", "error", "-nostats", "-y", "-i", str(Path(source).resolve())]
    filter_chains: list[str] = []
    video_label = None
    video_filter = None
    threshold_sharpening = settings.sharpening_enabled and settings.sharpening > 0 and settings.sharpening_threshold > 0
    filter_settings = replace(settings, sharpening_enabled=False) if threshold_sharpening else settings
    # Ordre du graphe : cadrage, netteté, B-roll, masques, puis captions au premier plan.
    if not broll:
        if any(mask.effect == "blur" for mask in masks) or threshold_sharpening:
            filter_chains.append(f"[0:v]{build_visual_filter(filter_settings)}[video_base]")
            final_label = "video_base"
            if threshold_sharpening:
                final_label = _append_threshold_sharpen_graph(filter_chains, final_label, settings)
            final_label = _append_mask_graph(filter_chains, final_label, masks, settings)
            if caption_filename is not None:
                filter_chains.append(f"[{final_label}]{_caption_filter(caption_filename)}[captioned]")
                final_label = "captioned"
            video_label = f"[{final_label}]"
        else:
            video_filter = build_visual_filter(filter_settings, caption_filename=caption_filename, masks=masks)
    else:
        for clip in broll:
            clip.placement.validate()
            if not clip.path.is_file():
                raise RenderError(f"Clip B-roll introuvable : {clip.path}")
            command.extend(["-i", str(clip.path.resolve())])
        base_settings = replace(settings, sharpening=0, sharpening_enabled=False)
        filters = [f"[0:v]{build_visual_filter(base_settings)}[base]"]
        previous = "base"
        ordered_broll = sorted(enumerate(broll), key=lambda pair: (pair[1].placement.track, pair[1].placement.timeline_start_ms))
        for index, (original_index, clip) in enumerate(ordered_broll):
            placement = clip.placement
            input_index = original_index + 1
            overlay_label = f"br{index}"
            output_label = f"v{index}"
            start = placement.timeline_start_ms / 1000
            end = placement.timeline_end_ms / 1000
            source_start = placement.source_start_ms / 1000
            source_end = placement.source_end_ms / 1000
            filters.append(
                f"[{input_index}:v]trim=start={source_start:.3f}:end={source_end:.3f},"
                f"setpts=PTS-STARTPTS+{start:.3f}/TB,"
                f"scale={settings.width}:{settings.height}:force_original_aspect_ratio=increase,"
                f"crop={settings.width}:{settings.height},setsar=1[{overlay_label}]"
            )
            filters.append(
                f"[{previous}][{overlay_label}]overlay=x=0:y=0:eof_action=pass:"
                f"enable='between(t,{start:.3f},{end:.3f})'[{output_label}]"
            )
            previous = output_label
        complete_filter = build_visual_filter(filter_settings)
        post_filters = complete_filter.split("setsar=1,", 1)[1] if "setsar=1," in complete_filter else ""
        final_label = previous
        if post_filters:
            filters.append(f"[{previous}]{post_filters}[vfinal]")
            final_label = "vfinal"
        if threshold_sharpening:
            final_label = _append_threshold_sharpen_graph(filters, final_label, settings)
        final_label = _append_mask_graph(filters, final_label, masks, settings)
        if caption_filename is not None:
            filters.append(f"[{final_label}]{_caption_filter(caption_filename)}[captioned]")
            final_label = "captioned"
        filter_chains.extend(filters)
        video_label = f"[{final_label}]"

    audio_labels: list[str] = []
    audio_timeline_duration = music.timeline_duration_ms if music is not None else None
    if music is not None:
        music.settings.validate()
        if music.timeline_duration_ms is not None:
            for segment in music.settings.ducking_segments:
                segment.validate(music.timeline_duration_ms)
        if not music.path.is_file():
            raise RenderError(f"Fichier musical introuvable : {music.path}")
        audio_index = 1 + len(broll)
        command.extend(["-i", str(music.path.resolve())])
        settings_for_audio = music.settings
        source_start = settings_for_audio.source_start_ms / 1000
        source_end = settings_for_audio.source_end_ms / 1000
        audio_filters = [f"atrim=start={source_start:.3f}:end={source_end:.3f}", "asetpts=PTS-STARTPTS", "aresample=48000"]
        target_duration_ms = None
        if music.timeline_duration_ms is not None:
            target_duration_ms = max(0, music.timeline_duration_ms - settings_for_audio.timeline_start_ms)
        if settings_for_audio.loop:
            segment_samples = max(1, settings_for_audio.duration_ms * 48)
            audio_filters.extend([f"aloop=loop=-1:size={segment_samples}"])
            if target_duration_ms is not None:
                audio_filters.append(f"atrim=duration={target_duration_ms / 1000:.3f}")
        audio_filters.append(f"volume={settings_for_audio.volume:.4f}")
        if settings_for_audio.fade_in_ms:
            audio_filters.append(f"afade=t=in:st=0:d={settings_for_audio.fade_in_ms / 1000:.3f}")
        fade_out_duration = settings_for_audio.fade_out_ms
        if fade_out_duration:
            fade_basis = target_duration_ms if settings_for_audio.loop and target_duration_ms is not None else settings_for_audio.duration_ms
            fade_start = max(0, fade_basis - fade_out_duration)
            audio_filters.append(f"afade=t=out:st={fade_start / 1000:.3f}:d={fade_out_duration / 1000:.3f}")
        audio_filters.extend([
            f"adelay={settings_for_audio.timeline_start_ms}:all=1",
            "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo",
        ])
        if settings_for_audio.ducking_segments:
            # Le ducking abaisse la musique pendant les segments de voix déclarés (timeline).
            audio_filters.append(
                f"volume='{_ducking_expression(sorted(settings_for_audio.ducking_segments))}':eval=frame"
            )
        filter_chains.append(f"[{audio_index}:a]{','.join(audio_filters)}[music_a]")
        audio_labels.append("music_a")

    sfx_input_start = 1 + len(broll) + (1 if music is not None else 0)
    for index, clip in enumerate(sfx):
        placement = clip.placement
        placement.validate()
        if not clip.path.is_file():
            raise RenderError(f"Fichier SFX introuvable : {clip.path}")
        command.extend(["-i", str(clip.path.resolve())])
        input_index = sfx_input_start + index
        start = placement.source_start_ms / 1000
        end = placement.source_end_ms / 1000
        filters = [f"atrim=start={start:.3f}:end={end:.3f}", "asetpts=PTS-STARTPTS", "aresample=48000",
                   f"volume={placement.volume:.4f}"]
        if placement.fade_in_ms:
            filters.append(f"afade=t=in:st=0:d={placement.fade_in_ms / 1000:.3f}")
        if placement.fade_out_ms:
            fade_start = max(0, placement.duration_ms - placement.fade_out_ms)
            filters.append(f"afade=t=out:st={fade_start / 1000:.3f}:d={placement.fade_out_ms / 1000:.3f}")
        filters.extend([f"adelay={placement.timeline_start_ms}:all=1",
                        "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"])
        timeline_duration = clip.timeline_duration_ms
        if timeline_duration is not None:
            filters.append(f"atrim=duration={timeline_duration / 1000:.3f}")
            if audio_timeline_duration is None:
                audio_timeline_duration = timeline_duration
        label = f"sfx_{index}"
        filter_chains.append(f"[{input_index}:a]{','.join(filters)}[{label}]")
        audio_labels.append(label)

    if audio_labels:
        if source_has_audio is not False:
            main_filters = ["aresample=48000", "aformat=sample_fmts=fltp:channel_layouts=stereo"]
            filter_chains.append(f"[0:a]{','.join(main_filters)}[main_a]")
            audio_labels.insert(0, "main_a")
        mix_inputs = "".join(f"[{label}]" for label in audio_labels)
        duration_mode = "first" if source_has_audio is not False else "longest"
        filter_chains.append(
            f"{mix_inputs}amix=inputs={len(audio_labels)}:duration={duration_mode}:dropout_transition=0:normalize=0,"
            "alimiter=limit=0.98[mix_a]"
        )
        if audio_timeline_duration is not None:
            filter_chains.append(f"[mix_a]atrim=duration={audio_timeline_duration / 1000:.3f}[audio_out]")
        else:
            filter_chains.append("[mix_a]anull[audio_out]")

    if video_filter is not None:
        command.extend(["-vf", video_filter])
    if filter_chains:
        command.extend(["-filter_complex", ";".join(filter_chains)])
        if video_label is not None:
            command.extend(["-map", video_label])
        elif music is not None or sfx:
            command.extend(["-map", "0:v:0"])
        if music is not None or sfx:
            command.extend(["-map", "[audio_out]"])
        elif broll or video_label is not None:
            command.extend(["-map", "0:a?"])
    command.extend([
        "-c:v", encoder, *encoding_args, "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart", str(Path(destination).resolve()),
    ])
    return command


def parse_progress_line(line: str, duration_ms: int | None) -> float | None:
    """Convertit une ligne ``-progress`` de FFmpeg en fraction 0..1 si la durée est connue."""
    key, _, value = line.strip().partition("=")
    if key not in {"out_time_us", "out_time_ms"} or not duration_ms or duration_ms <= 0:
        return None
    try:
        microseconds = int(value)
    except ValueError:
        return None
    # FFmpeg exprime out_time_ms en microsecondes, comme out_time_us.
    return max(0.0, min(1.0, microseconds / 1000 / duration_ms))


def _run_ffmpeg(command: list[str], *, cwd: Path, duration_ms: int | None, on_progress, cancel_event,
                cancel_message: str) -> None:
    """Exécute FFmpeg avec lecture asynchrone de la progression et des erreurs."""
    command = [command[0], "-progress", "pipe:1", *command[1:]]
    process = popen_hidden(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                               text=True, encoding="utf-8", errors="replace", cwd=cwd)
    stderr_lines: list[str] = []
    fractions: list[float] = []

    def read_stdout():
        for line in process.stdout:
            fraction = parse_progress_line(line, duration_ms)
            if fraction is not None:
                fractions.append(fraction)

    def read_stderr():
        for line in process.stderr:
            stderr_lines.append(line.rstrip())
            del stderr_lines[:-40]

    readers = [threading.Thread(target=read_stdout, daemon=True), threading.Thread(target=read_stderr, daemon=True)]
    for reader in readers:
        reader.start()
    try:
        last_reported = -1.0
        while process.poll() is None:
            if cancel_event is not None and cancel_event.is_set():
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                raise RenderError(cancel_message)
            if on_progress:
                current = fractions[-1] if fractions else None
                if current is None:
                    on_progress(None)
                elif current - last_reported >= 0.01:
                    last_reported = current
                    on_progress(current)
            time.sleep(0.1)
        for reader in readers:
            reader.join(timeout=5)
        if process.returncode:
            detail = "\n".join(stderr_lines[-12:]) or f"code de sortie {process.returncode}"
            raise RenderError(f"Échec FFmpeg :\n{detail}")
        if on_progress:
            on_progress(1.0)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def render_visual(
    source: str | Path,
    destination: str | Path,
    settings: VisualSettings,
    *,
    on_progress=None,
    cancel_event=None,
    captions: Sequence[CaptionCue] = (),
    caption_preset: CaptionPreset | None = None,
    masks: Sequence[MaskRegion] = (),
    broll: Sequence[BrollRenderClip] = (),
    music: MusicRenderTrack | None = None,
    sfx: Sequence[SfxRenderClip] = (),
    source_has_audio: bool | None = None,
    duration_ms: int | None = None,
) -> Path:
    """Rend le fichier final. ``on_progress`` reçoit une fraction 0..1 ou None si inconnue."""
    output = Path(destination).resolve()
    source_path = Path(source).expanduser().resolve()
    if output == source_path:
        raise RenderError("Le fichier source ne peut pas être remplacé par son export.")
    if output.suffix.lower() != ".mp4":
        raise RenderError("Le fichier de sortie doit porter l'extension .mp4.")
    if cancel_event is not None and cancel_event.is_set():
        raise RenderError("Export annulé.")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.stem}.{uuid.uuid4().hex}.part.mp4")
    caption_filename = f"cutweave_{uuid.uuid4().hex}.ass" if captions else None
    caption_path = output.parent / caption_filename if caption_filename else None
    try:
        if caption_path is not None:
            caption_path.write_text(build_ass_content(captions, settings, caption_preset), encoding="utf-8", newline="\n")
        command = build_render_command(
            source_path, temporary, settings, caption_filename=caption_filename, masks=masks, broll=broll,
            music=music, sfx=sfx, source_has_audio=source_has_audio,
        )
        LOGGER.info("Rendu FFmpeg : %s -> %s", source_path.name, output.name)
        LOGGER.debug("Commande FFmpeg : %s", command)
        _run_ffmpeg(command, cwd=output.parent, duration_ms=duration_ms, on_progress=on_progress,
                    cancel_event=cancel_event, cancel_message="Export annulé.")
        if cancel_event is not None and cancel_event.is_set():
            raise RenderError("Export annulé.")
        if not temporary.is_file() or temporary.stat().st_size == 0:
            raise RenderError("FFmpeg s'est terminé sans produire de fichier vidéo.")
        temporary.replace(output)
        return output
    except OSError as exc:
        raise RenderError(f"Erreur de fichier pendant le rendu : {exc}") from exc
    finally:
        temporary.unlink(missing_ok=True)
        if caption_path is not None:
            caption_path.unlink(missing_ok=True)
