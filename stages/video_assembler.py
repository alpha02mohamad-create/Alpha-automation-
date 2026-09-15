"""
stages/video_assembler.py
يركّب الفيديو النهائي، مطبّقاً القسمين الخامس والسابع من README:
- حركة كاميرا مختلفة لكل مشهد حسب "camera_move" القادم من السكربت
  (zoom-in / zoom-out / pan يمين/يسار / اهتزاز خفيف بلحظة الذروة فقط)
- تدرج ألوان (color grading) موحّد لكل مشاهد الفيديو حسب نوع المحتوى
- كابشن كلمة-بكلمة (word-by-word) بدل الجملة كاملة دفعة واحدة، مع تمييز
  الأرقام وكلمات الخطر بلون مختلف
- whoosh عند كل انتقال + pop بالبداية + موسيقى خلفية منخفضة تحت الراوي
- إخراج عمودي 1080x1920، بحد أقصى مطلق 59 ثانية (يُتحقق منه في quality_check.py)
"""

import glob
import os
import random
import subprocess

ASSETS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets")
SFX_DIR = os.path.join(ASSETS_DIR, "sfx")
MUSIC_DIR = os.path.join(ASSETS_DIR, "music")

WIDTH, HEIGHT = 1080, 1920
MUSIC_VOLUME_DB = -22
SFX_VOLUME_DB = -8
FPS = 30

# كلمات تُميَّز بلون مختلف بالكابشن — القسم 7.1 بند 5 من README
DANGER_WORDS = {
    "fire", "danger", "trapped", "dead", "kill", "explosion", "collapse",
    "flood", "smoke", "attack", "gone", "crushed", "deadly",
}


def _pick_random(directory: str, exts=(".mp3", ".wav")) -> str | None:
    files = [f for f in glob.glob(os.path.join(directory, "*")) if f.lower().endswith(exts)]
    return random.choice(files) if files else None


def _color_grade_filter(content_type: str) -> str:
    """تدرج ألوان موحّد حسب نوع المحتوى — القسم 5.1 بند 7 من README:
    بارد/أقل تشبعاً لمحتوى النجاة (توتر)، أكثر حيوية وتبايناً لمحتوى المواجهات."""
    if content_type == "survival":
        return "eq=contrast=1.08:saturation=0.92:brightness=-0.02"
    return "eq=contrast=1.15:saturation=1.15:brightness=0.01"


def _make_motion_clip(image_path: str, duration_ms: int, out_path: str,
                       camera_move: str, content_type: str) -> None:
    """صورة ثابتة + حركة كاميرا محددة حسب camera_move + تدرج ألوان — القسم الخامس من README."""
    duration_s = max(duration_ms / 1000, 0.5)
    total_frames = max(int(duration_s * FPS), 1)

    if camera_move == "slow_zoom_in":
        zp = f"zoompan=z='min(zoom+0.0018,1.5)':d={total_frames}:s={WIDTH}x{HEIGHT}:fps={FPS}"
    elif camera_move == "slow_zoom_out":
        zp = (f"zoompan=z='if(eq(on,0),1.4,max(zoom-0.0018,1.0))':"
              f"d={total_frames}:s={WIDTH}x{HEIGHT}:fps={FPS}")
    elif camera_move == "pan_left":
        zp = (f"zoompan=z=1.15:x='iw/2-(iw/zoom/2)-on*1.2':y='ih/2-(ih/zoom/2)':"
              f"d={total_frames}:s={WIDTH}x{HEIGHT}:fps={FPS}")
    elif camera_move == "pan_right":
        zp = (f"zoompan=z=1.15:x='iw/2-(iw/zoom/2)+on*1.2':y='ih/2-(ih/zoom/2)':"
              f"d={total_frames}:s={WIDTH}x{HEIGHT}:fps={FPS}")
    elif camera_move == "static_with_shake":
        # اهتزاز خفيف جداً — يُستخدم فقط بلحظة الذروة — القسم 5.1 بند 6
        zp = (f"zoompan=z=1.05:x='iw/2-(iw/zoom/2)+3*sin(on/2)':"
              f"y='ih/2-(ih/zoom/2)+3*cos(on/2)':d={total_frames}:s={WIDTH}x{HEIGHT}:fps={FPS}")
    else:
        zp = f"zoompan=z='min(zoom+0.0015,1.4)':d={total_frames}:s={WIDTH}x{HEIGHT}:fps={FPS}"

    color_filter = _color_grade_filter(content_type)
    filter_complex = f"scale={WIDTH * 2}:{HEIGHT * 2},{zp},{color_filter}"

    subprocess.run(
        ["ffmpeg", "-y", "-loop", "1", "-i", image_path,
         "-vf", filter_complex, "-t", str(duration_s),
         "-c:v", "libx264", "-pix_fmt", "yuv420p", out_path],
        check=True, capture_output=True,
    )


def _concat_video_clips(clip_paths: list[str], out_path: str) -> None:
    list_path = out_path + "_list.txt"
    with open(list_path, "w") as f:
        for p in clip_paths:
            f.write(f"file '{os.path.abspath(p)}'\n")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path,
         "-c", "copy", out_path],
        check=True, capture_output=True,
    )
    os.remove(list_path)


def _split_words_with_timing(text: str, start_ms: int, spoken_duration_ms: int) -> list[tuple[int, int, str]]:
    """يوزّع مدة الكلام على الكلمات تناسبياً حسب طول كل كلمة (تقريب بدون forced-alignment)."""
    words = text.split()
    if not words:
        return []
    total_chars = sum(len(w) for w in words) or 1
    entries = []
    cursor = start_ms
    for w in words:
        share = len(w) / total_chars
        w_dur = max(int(spoken_duration_ms * share), 120)
        entries.append((cursor, cursor + w_dur, w))
        cursor += w_dur
    return entries


def _ass_color_for_word(word: str) -> str:
    """أصفر/ذهبي للأرقام، أحمر لكلمات الخطر، أبيض لباقي الكابشن — القسم 7.1 بند 5."""
    bare = word.strip(".,!?").lower()
    if bare.isdigit():
        return "&H00D7FF&"  # ذهبي (صيغة ASS هي BGR)
    if bare in DANGER_WORDS:
        return "&H0000FF&"  # أحمر
    return "&HFFFFFF&"  # أبيض


def _fmt_ass_time(ms: int) -> str:
    h, ms_rem = divmod(max(ms, 0), 3600000)
    m, ms_rem = divmod(ms_rem, 60000)
    s, ms_rem = divmod(ms_rem, 1000)
    return f"{h:d}:{m:02d}:{s:02d}.{ms_rem // 10:02d}"


def _build_ass(segments: list[dict], durations_ms: list[int], out_path: str) -> None:
    """كابشن كلمة-بكلمة، خط عريض وواضح، موقع ثابت بالثلث السفلي — القسم السابع من README."""
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {WIDTH}
PlayResY: {HEIGHT}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Word,Arial Black,90,&HFFFFFF&,&HFFFFFF&,&H000000&,&H00000000&,1,0,1,6,0,2,60,60,260,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    cursor = 0
    for seg, dur in zip(segments, durations_ms):
        pause = seg.get("pause_after_ms", 0)
        spoken_dur = max(dur - pause, 200)
        for start, end, word in _split_words_with_timing(seg["text"], cursor, spoken_dur):
            color = _ass_color_for_word(word)
            lines.append(
                f"Dialogue: 0,{_fmt_ass_time(start)},{_fmt_ass_time(end)},Word,,0,0,0,,"
                f"{{\\fad(60,60)\\c{color}}}{word.upper()}"
            )
        cursor += dur

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def assemble_video(
    image_paths: list[str],
    segment_durations_ms: list[int],
    segments: list[dict],
    voice_path: str,
    content_type: str,
    workdir: str,
) -> str:
    os.makedirs(workdir, exist_ok=True)

    # 1) صورة متحركة لكل segment، بحركة كاميرا وتدرج ألوان حسب المشهد ونوع المحتوى
    clip_paths = []
    for i, (img, dur, seg) in enumerate(zip(image_paths, segment_durations_ms, segments)):
        clip_path = os.path.join(workdir, f"clip_{i:02d}.mp4")
        camera_move = seg.get("camera_move", "slow_zoom_in")
        _make_motion_clip(img, dur, clip_path, camera_move, content_type)
        clip_paths.append(clip_path)

    silent_video_path = os.path.join(workdir, "silent_video.mp4")
    _concat_video_clips(clip_paths, silent_video_path)

    # 2) كابشن كلمة-بكلمة
    ass_path = os.path.join(workdir, "captions.ass")
    _build_ass(segments, segment_durations_ms, ass_path)

    # 3) الصوت: راوي + موسيقى خلفية + SFX (whoosh عند كل انتقال، pop بالبداية)
    music_path = _pick_random(MUSIC_DIR)
    pop_path = os.path.join(SFX_DIR, "pop.mp3")
    whoosh_path = os.path.join(SFX_DIR, "whoosh.mp3")

    audio_inputs = ["-i", voice_path]
    filter_parts = ["[0:a]volume=1.0[voice]"]
    mix_inputs = ["[voice]"]
    input_idx = 1

    if music_path and os.path.exists(music_path):
        audio_inputs += ["-i", music_path]
        filter_parts.append(f"[{input_idx}:a]aloop=loop=-1:size=2e9,volume={MUSIC_VOLUME_DB}dB[music]")
        mix_inputs.append("[music]")
        input_idx += 1

    if os.path.exists(pop_path):
        audio_inputs += ["-i", pop_path]
        filter_parts.append(f"[{input_idx}:a]volume={SFX_VOLUME_DB}dB,adelay=0|0[pop]")
        mix_inputs.append("[pop]")
        input_idx += 1

    if os.path.exists(whoosh_path):
        cursor = 0
        for dur in segment_durations_ms[:-1]:
            cursor += dur
            audio_inputs += ["-i", whoosh_path]
            delay_ms = max(cursor - 150, 0)
            filter_parts.append(
                f"[{input_idx}:a]volume={SFX_VOLUME_DB}dB,adelay={delay_ms}|{delay_ms}[whoosh{input_idx}]"
            )
            mix_inputs.append(f"[whoosh{input_idx}]")
            input_idx += 1

    filter_complex = ";".join(filter_parts) + ";" + "".join(mix_inputs) + \
        f"amix=inputs={len(mix_inputs)}:duration=first:dropout_transition=0[aout]"

    mixed_audio_path = os.path.join(workdir, "mixed_audio.mp3")
    subprocess.run(
        ["ffmpeg", "-y", *audio_inputs, "-filter_complex", filter_complex,
         "-map", "[aout]", mixed_audio_path],
        check=True, capture_output=True,
    )

    # 4) دمج الفيديو + الصوت + حرق الكابشن كلمة-بكلمة
    final_path = os.path.join(workdir, "final_video.mp4")
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", silent_video_path, "-i", mixed_audio_path,
            "-vf", f"subtitles={ass_path}",
            "-c:v", "libx264", "-c:a", "aac", "-shortest", final_path,
        ],
        check=True, capture_output=True,
    )

    return final_path
