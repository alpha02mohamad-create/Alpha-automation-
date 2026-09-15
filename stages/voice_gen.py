"""
stages/voice_gen.py
يولّد صوت الراوي segment-by-segment عبر edge-tts (rate + pitch متغيّرين لكل جزء)،
مطبّقاً القسم السادس من README:
- صوت مختلف قليلاً حسب نوع المحتوى (نبرة أكثر إلحاحاً للنجاة، أكثر سردية للمواجهات)
- حد أقصى للوقفات الصامتة (600ms) لمنع أي وقفة تكسر الإيقاع (6.1 بند 5)
- دعم pitch اختياري لكل segment لتفادي الرتابة اللحنية (6.1 بند 4)

لو edge-tts فشل (خدمة غير رسمية، ممكن تنكسر)، يرجع تلقائياً إلى gTTS كـ fallback.

المخرج: ملف صوت نهائي واحد (voice.mp3) + قائمة بمدة كل segment (لمزامنة الكابشن لاحقاً)
"""

import asyncio
import os
import subprocess
import tempfile
from gtts import gTTS

# صوت مختلف حسب نوع المحتوى — القسم 6.1 بند 6 من README
VOICE_BY_CONTENT_TYPE = {
    "survival": "en-US-GuyNeural",
    "creature_clash": "en-US-ChristopherNeural",
}
DEFAULT_VOICE = "en-US-GuyNeural"

MAX_PAUSE_MS = 600  # يمنع أي وقفة أطول تُكسر الإيقاع — القسم 6.1 بند 5


async def _edge_tts_segment(text: str, voice: str, rate: str, pitch: str, out_path: str) -> None:
    import edge_tts
    communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
    await communicate.save(out_path)


def _edge_tts_segment_sync(text: str, voice: str, rate: str, pitch: str, out_path: str) -> None:
    asyncio.run(_edge_tts_segment(text, voice, rate, pitch, out_path))


def _gtts_fallback_segment(text: str, out_path: str) -> None:
    """gTTS لا يدعم rate/pitch — يُستخدم فقط لو edge-tts فشل بالكامل."""
    tts = gTTS(text=text, lang="en")
    tmp_mp3 = out_path.replace(".mp3", "_raw.mp3")
    tts.save(tmp_mp3)
    os.replace(tmp_mp3, out_path)


def _make_silence(duration_ms: int, out_path: str) -> None:
    if duration_ms <= 0:
        return
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
         "-t", str(duration_ms / 1000), "-q:a", "9", out_path],
        check=True, capture_output=True,
    )


def _get_duration_ms(path: str) -> int:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        check=True, capture_output=True, text=True,
    )
    return int(float(result.stdout.strip()) * 1000)


def generate_voice(segments: list[dict], content_type: str, workdir: str) -> tuple[str, list[int]]:
    """
    segments: كل عنصر فيه "text", "rate", "pause_after_ms"، وقد يحمل "pitch" اختيارياً.
    content_type: "survival" أو "creature_clash" — يحدد الصوت المستخدم.
    Returns: (final_voice_path, [duration_ms_per_segment_including_pause, ...])
    """
    os.makedirs(workdir, exist_ok=True)
    voice = VOICE_BY_CONTENT_TYPE.get(content_type, DEFAULT_VOICE)
    part_files = []
    durations = []

    for i, seg in enumerate(segments):
        seg_path = os.path.join(workdir, f"seg_{i:02d}.mp3")
        rate = seg.get("rate", "+0%")
        pitch = seg.get("pitch", "+0Hz")
        try:
            _edge_tts_segment_sync(seg["text"], voice, rate, pitch, seg_path)
        except Exception as e:
            print(f"[voice_gen] edge-tts failed on segment {i} ({e}); falling back to gTTS")
            _gtts_fallback_segment(seg["text"], seg_path)

        seg_duration = _get_duration_ms(seg_path)
        part_files.append(seg_path)
        durations.append(seg_duration)

        pause_ms = min(seg.get("pause_after_ms", 0), MAX_PAUSE_MS)
        if pause_ms > 0:
            silence_path = os.path.join(workdir, f"seg_{i:02d}_silence.mp3")
            _make_silence(pause_ms, silence_path)
            part_files.append(silence_path)
            durations[-1] += pause_ms

    concat_list_path = os.path.join(workdir, "concat_list.txt")
    with open(concat_list_path, "w") as f:
        for p in part_files:
            f.write(f"file '{os.path.abspath(p)}'\n")

    final_path = os.path.join(workdir, "voice.mp3")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_list_path,
         "-c", "copy", final_path],
        check=True, capture_output=True,
    )

    return final_path, durations


if __name__ == "__main__":
    demo_segments = [
        {"text": "Smoke fills the room before you even open your eyes.", "rate": "+5%", "pause_after_ms": 200},
        {"text": "The door is already too hot to touch.", "rate": "-5%", "pause_after_ms": 400},
    ]
    with tempfile.TemporaryDirectory() as td:
        path, durs = generate_voice(demo_segments, "survival", td)
        print(path, durs)
