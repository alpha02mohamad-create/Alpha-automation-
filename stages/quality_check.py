"""
stages/quality_check.py
بوابة الفحص الشاملة — تطبّق القسم صفر (منع التكرار المطلق) من README بكل مستوياته:
1) تكرار الفكرة الجوهرية (idea_summary / core_concept_short)
2) تكرار تركيبة الأبعاد نفسها (نفس نوع الخطر/الكائن + نفس المكان...)
3) تكرار العنوان أو نص الهوك
4) تكرار بنية الهوك (hook_structure) بشكل متتالٍ
5) سقف المدة الأقصى المطلق (59 ثانية) بدون أي حد أدنى ثابت

كل فيديو يُعتمد يُسجَّل بالكامل في "Content Ledger" (القسم 0.2 بند 1) ليُستخدم
كمرجع تلقائي لكل الفيديوهات القادمة.
"""

import difflib
import json
import os

HISTORY_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "history")
HISTORY_FILE = os.path.join(HISTORY_DIR, "published_content.json")
MAX_HISTORY = 500  # سجل أوسع من 30 فقط، لأن فحص التكرار الجوهري يحتاج مدى أطول
SIMILARITY_THRESHOLD = 0.68
MAX_VIDEO_SECONDS = 59  # سقف أقصى مطلق فقط — لا يوجد حد أدنى ثابت (القسم 2.2 من README)


def load_history() -> list[dict]:
    if not os.path.exists(HISTORY_FILE):
        return []
    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_history(entries: list[dict]) -> None:
    os.makedirs(HISTORY_DIR, exist_ok=True)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(entries[-MAX_HISTORY:], f, ensure_ascii=False, indent=2)


def _sim(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, (a or "").lower(), (b or "").lower()).ratio()


def _keyword_overlap(a: str, b: str) -> float:
    """تشابه Jaccard بسيط على الكلمات المفتاحية — بديل خفيف عن embeddings لتفادي تبعية ثقيلة."""
    stop = {"a", "an", "the", "in", "on", "at", "of", "to", "and", "or", "is",
            "are", "with", "over", "into", "for", "as", "it", "its"}
    wa = {w for w in (a or "").lower().split() if w not in stop and len(w) > 2}
    wb = {w for w in (b or "").lower().split() if w not in stop and len(w) > 2}
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def idea_is_duplicate(idea: dict, history: list[dict] | None = None) -> tuple[bool, str]:
    """
    فحص التكرار على مستوى الفكرة الجوهرية — يُستدعى من trend_finder.py
    قبل حتى كتابة أي سكربت (أول بوابة في القسم صفر).
    """
    history = history if history is not None else load_history()
    summary = idea.get("idea_summary", "")
    core = idea.get("core_concept_short", "")

    for entry in history:
        entry_summary = entry.get("idea_summary", "")
        entry_core = entry.get("core_concept_short", "")

        if _sim(core, entry_core) >= 0.6:
            return True, f"core_concept too similar to previous: {entry_core!r}"

        combined_sim = max(_sim(summary, entry_summary), _keyword_overlap(summary, entry_summary))
        if combined_sim >= SIMILARITY_THRESHOLD:
            return True, f"idea_summary too similar to previous: {entry_summary!r}"

        # نفس تركيبة الأبعاد بالضبط (حتى لو الصياغة النهائية مختلفة) = تكرار محرَّم
        if idea.get("content_type") == entry.get("content_type"):
            dims_a, dims_b = idea.get("dimensions", {}), entry.get("dimensions", {})
            shared_keys = set(dims_a) & set(dims_b)
            if shared_keys and all(dims_a[k] == dims_b[k] for k in shared_keys):
                return True, "identical dimension combination as a previous video"

    return False, "ok"


def _hook_text(script: dict) -> str:
    return script["segments"][0]["text"] if script.get("segments") else ""


def script_is_duplicate(script: dict, history: list[dict] | None = None) -> tuple[bool, str]:
    """فحص إضافي بعد كتابة السكربت: تطابق العنوان/الهوك الفعلي + منع تكرار بنية الهوك تباعاً."""
    history = history if history is not None else load_history()
    hook = _hook_text(script)
    title = script.get("title", "")
    hook_structure = script.get("hook_structure", "")

    for entry in history:
        if _sim(hook, entry.get("hook", "")) >= SIMILARITY_THRESHOLD:
            return True, "duplicate_hook_text"
        if _sim(title, entry.get("title", "")) >= SIMILARITY_THRESHOLD:
            return True, "duplicate_title"

    last_two_structures = [e.get("hook_structure") for e in history[-2:]]
    if hook_structure and last_two_structures.count(hook_structure) >= 2:
        return True, "hook_structure_repeated_too_many_times_in_a_row"

    return False, "ok"


def check_duration(video_path: str) -> bool:
    import subprocess
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", video_path],
        check=True, capture_output=True, text=True,
    )
    duration = float(result.stdout.strip())
    return duration <= MAX_VIDEO_SECONDS


def run_quality_checks(idea: dict, script: dict, video_path: str) -> tuple[bool, str]:
    is_dup, reason = script_is_duplicate(script)
    if is_dup:
        return False, reason
    if not check_duration(video_path):
        return False, "video_exceeds_59_seconds"
    if not script.get("segments"):
        return False, "empty_script"
    return True, "ok"


def record_published(idea: dict, script: dict) -> None:
    """يسجّل كل بيانات الفيديو المنشور (Content Ledger) — القسم 0.2 بند 1 من README."""
    history = load_history()
    history.append({
        "content_type": idea.get("content_type"),
        "dimensions": idea.get("dimensions"),
        "idea_summary": idea.get("idea_summary"),
        "core_concept_short": idea.get("core_concept_short"),
        "title": script.get("title", ""),
        "hook": _hook_text(script),
        "hook_structure": script.get("hook_structure", ""),
        "visual_elements": script.get("visual_elements", {}),
    })
    _save_history(history)
