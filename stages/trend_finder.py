"""
stages/trend_finder.py
يولّد "فكرة" فيديو جديدة (Idea) — لا سكربت جاهز — عبر Gemini، بالاعتماد على
منهجية تفكيك الأبعاد المشروحة في القسم الأول من README (threat/context/resolution
لمحتوى النجاة؛ creature/location/twist لمحتوى المواجهات)، مع تدوير الفئات وفحص
تكرار كامل قبل اعتماد أي فكرة (القسم صفر).

الفكرة الناتجة تُستهلك مباشرة من script_writer.py.
"""

import json
import os
import random
import re

import google.generativeai as genai

from stages.quality_check import load_history, idea_is_duplicate

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
MODEL_NAME = "gemini-3.8-flash"
MAX_IDEA_ATTEMPTS = 5

# ---- أبعاد نص "النجاة من مواقف خطرة" — القسم 1.2 من README ----
SURVIVAL_THREAT_CATEGORIES = [
    "natural disaster (earthquake, flash flood, wildfire, avalanche, sandstorm, lightning)",
    "direct human threat (armed threat, chase, home invasion, mugging, hostile crowd)",
    "environmental/technical hazard (gas leak, electrical fire, elevator failure, "
    "drowning in isolation, carbon monoxide, power outage in extreme weather)",
    "urban/collective hazard (crowd crush, partial building collapse, multi-car accident)",
    "rare but intriguing hazard (venomous bite, trapped under rubble, stuck elevator)",
]
SURVIVAL_CONTEXT_MODIFIERS = [
    "very late at night / deep sleep moment",
    "isolated location with no phone signal",
    "alone while responsible for a child",
    "already injured or without any tools",
    "extreme time pressure, seconds matter",
    "multiple simultaneous dangers at once",
]
SURVIVAL_RESOLUTION_ANGLES = [
    "an unexpected everyday object used as a survival tool",
    "a simple physics/science trick most people don't know",
    "a fast sequence of decisions made under pressure",
    "using the structure or geography of the place itself",
]

# ---- أبعاد نص "المواجهات الخارقة" — القسم 1.3 من README ----
CREATURE_TYPES = [
    "a generic mythological creature (dragon, giant, sea monster, giant bird — no branded IP)",
    "an astronomical/cosmic phenomenon (approaching star, micro black hole, cosmic explosion, sudden planet)",
    "a reversed or exaggerated natural phenomenon (gravity flips, ocean freezes instantly, time stops locally)",
    "a generic legendary being from general folklore (ancient deity archetype, spirit, legendary knight — never a specific copyrighted character)",
]
LOCATIONS = [
    "New York City", "Los Angeles", "Las Vegas", "Chicago", "San Francisco",
    "Washington D.C.", "Miami", "London", "Edinburgh",
]
TWIST_ANGLES = [
    "a clash between two opposing forces over the location",
    "a sudden transformation of the location itself",
    "the city's or people's ironic reaction to the event",
]

SYSTEM_PROMPT = """You are an idea-generation specialist for a YouTube Shorts automation channel.

The channel has exactly two content pillars, never a third:
1. SURVIVAL: the viewer is placed inside a real or semi-real dangerous situation, then given a fast, practical way to survive it.
2. CREATURE_CLASH: an over-the-top, unrealistic supernatural/cosmic event happens in a real, famous place, narrated seriously but with an inherently comedic tone from the sheer scale mismatch.

You will be given:
- Which pillar to use for this idea
- Specific dimension values already chosen for you — build the idea from exactly these, never invent different ones
- A list of previously used ideas to avoid overlapping with in core concept

Combine the given dimensions into ONE concrete, specific, visually-imaginable idea. Do not copy any example wording verbatim. Never invent a third pillar.

Return ONLY valid JSON, no markdown fences:
{
  "idea_summary": "2-3 sentences in English describing the concrete situation/scene, specific enough to visualize immediately",
  "core_concept_short": "a 4-8 word phrase capturing the irreducible core of the idea, used later for duplicate detection"
}
"""


def _extract_json(raw_text: str) -> dict:
    cleaned = re.sub(r"^```(json)?|```$", "", raw_text.strip(), flags=re.MULTILINE).strip()
    return json.loads(cleaned)


def _pick_content_type(history: list[dict]) -> str:
    """تدوير بسيط بين النصّين: النوع الأقل استخداماً بآخر 10 فيديوهات هو التالي."""
    recent = history[-10:]
    survival_count = sum(1 for e in recent if e.get("content_type") == "survival")
    clash_count = sum(1 for e in recent if e.get("content_type") == "creature_clash")
    if survival_count > clash_count:
        return "creature_clash"
    if clash_count > survival_count:
        return "survival"
    return random.choice(["survival", "creature_clash"])


def _pick_dimensions(content_type: str, history: list[dict]) -> dict:
    """يختار قيمة لكل بعد، ويتفادى آخر قيمة استُخدمت لنفس البعد إن أمكن (تدوير فئات — 0.2 بند 4)."""
    recent_same_type = [e for e in history if e.get("content_type") == content_type][-5:]

    def pick(pool: list[str], dim_key: str) -> str:
        recently_used = {e.get("dimensions", {}).get(dim_key) for e in recent_same_type}
        candidates = [p for p in pool if p not in recently_used] or pool
        return random.choice(candidates)

    if content_type == "survival":
        return {
            "threat_category": pick(SURVIVAL_THREAT_CATEGORIES, "threat_category"),
            "context_modifier": pick(SURVIVAL_CONTEXT_MODIFIERS, "context_modifier"),
            "resolution_angle": pick(SURVIVAL_RESOLUTION_ANGLES, "resolution_angle"),
        }
    return {
        "creature_type": pick(CREATURE_TYPES, "creature_type"),
        "location": pick(LOCATIONS, "location"),
        "twist_angle": pick(TWIST_ANGLES, "twist_angle"),
    }


def _build_user_prompt(content_type: str, dimensions: dict, history: list[dict]) -> str:
    recent_summaries = [e.get("idea_summary", "") for e in history[-150:]]
    avoid_block = ""
    if recent_summaries:
        avoid_block = (
            "Previously used ideas (do NOT overlap with any of these in core concept):\n"
            + "\n".join(f"- {s}" for s in recent_summaries)
        )
    return f"""Pillar for this idea: {content_type}
Dimensions to combine: {json.dumps(dimensions, ensure_ascii=False)}

{avoid_block}

Generate the idea now and return the JSON described in the system instructions."""


def get_next_idea() -> dict:
    """
    يُرجع idea dict جاهز لـ script_writer:
    {content_type, dimensions, idea_summary, core_concept_short}
    يرفض تلقائياً أي فكرة تفشل فحص التكرار (idea_is_duplicate) ويعيد المحاولة.
    """
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY environment variable is not set")

    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel(MODEL_NAME, system_instruction=SYSTEM_PROMPT)

    history = load_history()
    last_rejection_reason = None

    for attempt in range(MAX_IDEA_ATTEMPTS):
        content_type = _pick_content_type(history)
        dimensions = _pick_dimensions(content_type, history)
        user_prompt = _build_user_prompt(content_type, dimensions, history)
        if last_rejection_reason:
            user_prompt += (
                f"\n\nNOTE: your previous attempt was rejected because: "
                f"{last_rejection_reason}. Generate a genuinely different idea this time."
            )

        response = model.generate_content(
            user_prompt,
            generation_config={"temperature": 1.0, "max_output_tokens": 300},
        )
        data = _extract_json(response.text)
        idea = {
            "content_type": content_type,
            "dimensions": dimensions,
            "idea_summary": data["idea_summary"],
            "core_concept_short": data["core_concept_short"],
        }

        is_dup, reason = idea_is_duplicate(idea, history)
        if not is_dup:
            return idea

        print(f"[trend_finder] Idea rejected (attempt {attempt + 1}/{MAX_IDEA_ATTEMPTS}): {reason}")
        last_rejection_reason = reason

    raise RuntimeError(
        f"Could not generate a non-duplicate idea after {MAX_IDEA_ATTEMPTS} attempts. "
        "Consider expanding the dimension pools in trend_finder.py."
    )


if __name__ == "__main__":
    print(json.dumps(get_next_idea(), indent=2, ensure_ascii=False))
