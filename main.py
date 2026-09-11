"""
main.py
المنسّق الرئيسي — يطبّق الدورة الكاملة الموضّحة بالقسم التمهيدي من README:
فكرة (trend_finder، بأبعاد + فحص تكرار) → سكربت (script_writer، هوك+مشاهد) →
صوت (voice_gen) → صور (image_gen، بنمط بصري ثابت) → فيديو (video_assembler) →
فحص جودة شامل (quality_check) → نشر (youtube_uploader) → تسجيل بالسجل الدائم.

عند فشل فحص الجودة يعيد كتابة سكربت جديد لنفس الفكرة (حد أقصى 3 محاولات)،
لأن الفكرة نفسها اجتازت فحص التكرار أصلاً قبل الوصول لهذه المرحلة.
"""

import os
import tempfile

from stages.trend_finder import get_next_idea
from stages.script_writer import generate_script
from stages.voice_gen import generate_voice
from stages.image_gen import generate_images_for_segments
from stages.video_assembler import assemble_video
from stages.quality_check import run_quality_checks, record_published, load_history
from stages.youtube_uploader import upload_video

MAX_SCRIPT_ATTEMPTS = 3


def run_once() -> None:
    idea = get_next_idea()
    print(f"[main] Idea ({idea['content_type']}): {idea['idea_summary']}")

    history = load_history()
    recent_hook_structures = [e.get("hook_structure", "") for e in history]

    with tempfile.TemporaryDirectory() as workdir:
        final_video_path = None
        script = None

        for attempt in range(MAX_SCRIPT_ATTEMPTS):
            script = generate_script(idea, recent_hook_structures=recent_hook_structures)
            print(f"[main] Attempt {attempt + 1}: title='{script['title']}' "
                  f"hook_structure={script['hook_structure']}")

            voice_path, durations_ms = generate_voice(
                script["segments"], idea["content_type"], os.path.join(workdir, "voice")
            )
            image_paths = generate_images_for_segments(
                script["segments"], script["style_lock"], os.path.join(workdir, "images")
            )
            video_path = assemble_video(
                image_paths, durations_ms, script["segments"], voice_path,
                idea["content_type"], os.path.join(workdir, "video"),
            )

            ok, reason = run_quality_checks(idea, script, video_path)
            if ok:
                final_video_path = video_path
                break
            print(f"[main] Quality check failed: {reason}. Retrying with a new script for the same idea...")

        if final_video_path is None:
            print("[main] All attempts failed quality checks. Skipping this run.")
            return

        video_id = upload_video(
            final_video_path,
            title=script["title"],
            description=script["description"],
            tags=script["tags"],
        )
        print(f"[main] Uploaded: https://youtube.com/watch?v={video_id}")

        record_published(idea, script)


if __name__ == "__main__":
    run_once()
