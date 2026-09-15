"""
stages/gemini_client.py
غلاف مشترك لاستدعاء Gemini عبر SDK الجديد (google-genai)، بدل تكرار نفس
الكود في كل ملف (trend_finder.py و script_writer.py)، لتفادي تعارض/تفرّق
الإصدارات لاحقاً. يحل ثلاث مشاكل ظهرت فعلياً:

1) مكتبة `google.generativeai` القديمة توقف دعمها بالكامل — الاستبدال هنا
   بمكتبة `google.genai` الحديثة (السطر الوحيد اللي لازم يتغيّر مستقبلاً
   لو الموديل اتغيّر تاني هو MODEL_NAME هنا فقط، مش في كل ملف لوحده).
2) موديلات Gemini 3.x تستهلك من *نفس* ميزانية max_output_tokens في
   "التفكير الداخلي" قبل الإجابة النهائية — لو الميزانية صغيرة، الرد بيتقطع
   فجأة ويطلع JSONDecodeError غامض. الحل: ضبط thinking_level صراحة +
   ميزانية أكبر، وإعطاء رسالة خطأ واضحة عند القطع بدل خطأ JSON مبهم.
3) الاعتماد على response_mime_type="application/json" بدل تنظيف نصي هش
   بعد الاستلام (regex لإزالة ```json```)، مع إبقاء التنظيف كشبكة أمان فقط.
"""

import json
import os
import re

from google import genai
from google.genai import types

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
MODEL_NAME = "gemini-3.8-flash"

_client = None


def _get_client() -> genai.Client:
    global _client
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY environment variable is not set")
    if _client is None:
        _client = genai.Client(api_key=GEMINI_API_KEY)
    return _client


def _strip_markdown_fences(text: str) -> str:
    """شبكة أمان إضافية فقط — response_mime_type=json يفترض ألا يحتاجها أصلاً."""
    return re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()


def generate_json(
    system_instruction: str,
    user_prompt: str,
    temperature: float = 1.0,
    max_output_tokens: int = 2048,
    thinking_level: str = "low",
) -> dict:
    """
    ينادي Gemini ويُرجع dict جاهز، أو يرفع RuntimeError برسالة واضحة تشرح
    السبب الحقيقي (قطع بسبب حد التوكينز، رفض أمان، ...) بدل ترك استثناء
    JSON مبهم يوصل للمستخدم كما حصل سابقاً.
    """
    client = _get_client()

    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=user_prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            response_mime_type="application/json",
            thinking_config=types.ThinkingConfig(thinking_level=thinking_level),
        ),
    )

    candidate = response.candidates[0] if response.candidates else None
    finish_reason = getattr(candidate, "finish_reason", None) if candidate else None

    raw_text = (response.text or "").strip()
    if not raw_text:
        raise RuntimeError(
            f"[gemini_client] Empty response from {MODEL_NAME} "
            f"(finish_reason={finish_reason}). This usually means "
            "max_output_tokens was too low for this model's internal "
            "thinking + final answer combined — raise max_output_tokens "
            "or lower thinking_level and retry."
        )

    try:
        return json.loads(_strip_markdown_fences(raw_text))
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"[gemini_client] {MODEL_NAME} response was not valid JSON "
            f"(finish_reason={finish_reason}). First 300 chars: {raw_text[:300]!r}"
        ) from e
