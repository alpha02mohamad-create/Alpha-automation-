"""
stages/script_writer.py
يكتب السكربت الكامل عبر Gemini، مطبّقاً القسمين الثاني والثالث من README:
- 3 خيارات هوك مختلفة الزاوية داخلياً، ثم اختيار الأقوى فقط في المخرج النهائي
- تقسيم إلزامي لمشاهد (segments): نص + زاوية كاميرا + حركة + إضاءة + مزاج لكل مشهد
- طول ديناميكي بدون مدة ثابتة، بحد أقصى مطلق 59 ثانية (ملحق نهاية README)
- معدل كلام أسرع لمحتوى المواجهات الكوميدية، أبطأ قليلاً لمحتوى النجاة (القسم 2.2)
- نمط بصري ثابت (style_lock) لكل الفيديو الواحد لضمان الاتساق (القسم 4.3)

يستهلك idea dict من trend_finder.py، ويُرجع dict يُستهلك من voice_gen.py و
image_gen.py و video_assembler.py و youtube_uploader.py.
"""

import json
import os
import re

import google.generativeai as genai

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
MODEL_NAME = "gemini-1.5-flash"

MAX_VIDEO_SECONDS = 59

# معدل الكلام الأقصى بالكلمة/ثانية حسب نوع المحتوى — القسم 2.2 من README
WORDS_PER_SECOND = {
    "survival": 2.5,
    "creature_clash": 3.0,
}

SYSTEM_PROMPT_TEMPLATE = """You are the scriptwriter for a YouTube Shorts automation channel with two content pillars:

1. SURVIVAL: viewer is placed inside a dangerous situation, then given fast practical survival steps.
2. CREATURE_CLASH: an over-the-top unrealistic supernatural/cosmic event in a real famous place, seriously narrated but comedic from scale/contrast.

You will receive one finished IDEA (content_type + a concrete idea_summary). Write ONE complete script for it.

MANDATORY STRUCTURE:
1. First, silently draft 3 different hook options for the very first line — each a genuinely different angle (not just reworded), each 1-2 short sentences, each starting mid-action (never a throat-clearing intro like "Today we will..."). Pick the strongest one using these criteria: creates a real curiosity gap, uses a specific number/fact when possible, would stop a fast scroller. Only output the winning hook in the final script, plus a short structural tag for it.
2. Escalation: every following line must raise tension or information versus the previous one. No filler transitions.
3. Climax: the single most intense beat.
4. Resolution: a practical solution (survival) or a final punchline/reveal shot (creature_clash) that gives a sense of completion.

SCENE BREAKDOWN (mandatory): split the whole script into "segments". Each segment = 1-2 sentences = one beat. For each segment provide:
- "text": the spoken line(s)
- "rate": relative TTS speech-rate offset, one of "-15%","-10%","-5%","+0%","+5%","+10%","+15%" (slower for tense/reflective beats, faster for energetic ones)
- "pause_after_ms": 0 to 600ms silence after this segment (longer before a twist or the final line, never longer than 600)
- "visual_prompt": concrete, specific description of exactly what should appear on screen at this exact moment (a real depicted scene, not an abstract idea)
- "camera_angle": one of "extreme_close_up","close_up","medium_shot","wide_shot","low_angle","high_angle"
- "camera_move": one of "slow_zoom_in","slow_zoom_out","pan_left","pan_right","static_with_shake" (use static_with_shake ONLY for the single climax beat, and never twice in the same script)
- "lighting": one of "cold_blue_night","warm_fire_glow","dramatic_side_light","cosmic_glow","neutral_daylight"
- "mood": 1-3 words describing the emotional tone of this exact beat

LENGTH: There is NO fixed target duration. Write exactly as many segments/words as the idea genuinely needs to be told fully and tightly — never pad it out, never cut it short. The one absolute hard limit is a maximum of {max_seconds} seconds of spoken audio at roughly {wps} spoken words per second for this content type — meaning an ABSOLUTE MAXIMUM of about {max_words} words total for the whole script. Staying well under that maximum is fine and often better than a script that feels stretched.

STYLE: short simple sentences (5-10 words), direct "you" address where natural, plain American English, no cliché openers ("Imagine", "What if", "Have you ever") unless the specific angle truly earns it — and never repeat the same hook structure listed under "recent hook structures to avoid" below.

VISUAL CONSISTENCY: invent one consistent visual style string ("style_lock") for this whole video (photographic/cinematic descriptive keywords) that will be reused across every segment's image generation so the whole video feels like one film. If a recurring subject/character appears in more than one segment, describe it identically (same wording) every time it appears in visual_prompt.

BRAND SAFETY: no explicit real-world gore, no actionable unsafe instructions presented as real advice, no copyrighted/branded characters (only generic mythological/legendary concepts), nothing culturally offensive.

Return ONLY valid JSON, no markdown fences, matching exactly this schema:
{{
  "title": "string, <=100 chars, matches the hook's curiosity gap, no false clickbait",
  "hook_structure": "one of: question, statement_shock, imperative, statistic, in_medias_res",
  "style_lock": "string, the consistent visual style keywords for this whole video",
  "segments": [
    {{"text": "string", "rate": "string", "pause_after_ms": 0, "visual_prompt": "string", "camera_angle": "string", "camera_move": "string", "lighting": "string", "mood": "string"}}
  ],
  "description": "string, 2-3 sentences for YouTube description, keyword-rich but natural",
  "tags": ["string", "..."],
  "visual_elements": {{"primary_subject": "string", "location_or_setting": "string", "color_palette": "string"}}
}}
"""


def _extract_json(raw_text: str) -> dict:
    cleaned = re.sub(r"^```(json)?|```$", "", raw_text.strip(), flags=re.MULTILINE).strip()
    return json.loads(cleaned)


def _build_user_prompt(idea: dict, recent_hook_structures: list[str]) -> str:
    avoid_block = ""
    if recent_hook_structures:
        avoid_block = (
            "Recent hook structures to avoid repeating consecutively:\n"
            + ", ".join(recent_hook_structures[-5:])
        )
    return f"""Content pillar: {idea['content_type']}
Idea to script: {idea['idea_summary']}

{avoid_block}

Write the script now and return the JSON described in the system instructions."""


def generate_script(idea: dict, recent_hook_structures: list[str] | None = None) -> dict:
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY environment variable is not set")

    genai.configure(api_key=GEMINI_API_KEY)

    wps = WORDS_PER_SECOND.get(idea["content_type"], 2.5)
    max_words = int(MAX_VIDEO_SECONDS * wps)
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
        max_seconds=MAX_VIDEO_SECONDS, wps=wps, max_words=max_words
    )

    model = genai.GenerativeModel(MODEL_NAME, system_instruction=system_prompt)
    user_prompt = _build_user_prompt(idea, recent_hook_structures or [])

    response = model.generate_content(
        user_prompt,
        generation_config={"temperature": 0.9, "max_output_tokens": 2048},
    )

    data = _extract_json(response.text)
    _validate_script(data)
    return data


def _validate_script(data: dict) -> None:
    required_keys = {"title", "hook_structure", "style_lock", "segments",
                      "description", "tags", "visual_elements"}
    missing = required_keys - data.keys()
    if missing:
        raise ValueError(f"Script JSON missing keys: {missing}")
    if not data["segments"]:
        raise ValueError("Script has no segments")

    required_seg_keys = {"text", "rate", "pause_after_ms", "visual_prompt",
                          "camera_angle", "camera_move", "lighting", "mood"}
    for seg in data["segments"]:
        missing_seg = required_seg_keys - seg.keys()
        if missing_seg:
            raise ValueError(f"Segment missing keys: {missing_seg}")


if __name__ == "__main__":
    demo_idea = {
        "content_type": "survival",
        "idea_summary": (
            "A person wakes up at 3AM to the smell of smoke spreading fast "
            "through a small apartment with the only exit blocked by fire."
        ),
    }
    result = generate_script(demo_idea)
    print(json.dumps(result, indent=2, ensure_ascii=False))
