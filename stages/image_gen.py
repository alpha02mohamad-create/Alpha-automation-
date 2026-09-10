"""
stages/image_gen.py
يولّد صورة واحدة لكل segment عبر Cloudflare Workers AI / FLUX.1[schnell]،
مطبّقاً القسم الرابع من README:
- زاوية كاميرا + إضاءة محددتان صراحة لكل مشهد (لا تُترك فارغة أبداً)
- نمط بصري ثابت (style_lock) يُلحق بكل برومبت لضمان أن كل مشاهد الفيديو
  الواحد تبدو من نفس "الفيلم"
- برومبت سلبي (negative prompt) لتفادي العيوب الشائعة بالتوليد الآلي
- seed مشتق من style_lock لزيادة الاتساق البصري بين مشاهد نفس الفيديو (القسم 4.3)

عند فشل Cloudflare أو غياب المفاتيح، يستخدم صورة احتياطية عامة من
assets/fallback_images/ بدل إيقاف الـ pipeline بالكامل.
"""

import base64
import os
import random
from io import BytesIO

import requests
from PIL import Image, ImageOps

CLOUDFLARE_API_TOKEN = os.getenv("FLUX_API_KEY")
CLOUDFLARE_ACCOUNT_ID = os.getenv("FLUX_API_ID")

MODEL = "@cf/black-forest-labs/flux-1-schnell"

CLOUDFLARE_URL = (
    "https://api.cloudflare.com/client/v4/accounts/"
    f"{CLOUDFLARE_ACCOUNT_ID}/ai/run/{MODEL}"
    if CLOUDFLARE_ACCOUNT_ID
    else None
)

IMG_WIDTH = 1080
IMG_HEIGHT = 1920
TIMEOUT_SECONDS = 120

FALLBACK_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "assets", "fallback_images"
)

# القسم 4.2 بند 2 من README — عبارات زاوية الكاميرا
CAMERA_ANGLE_PHRASES = {
    "extreme_close_up": "extreme close-up shot",
    "close_up": "close-up shot",
    "medium_shot": "medium shot",
    "wide_shot": "wide establishing shot",
    "low_angle": "low angle shot looking up, conveys power and danger",
    "high_angle": "high angle bird's eye shot, conveys scale",
}

# القسم 4.2 بند 3 من README — عبارات الإضاءة
LIGHTING_PHRASES = {
    "cold_blue_night": "cold blue moonlight, tense night atmosphere",
    "warm_fire_glow": "warm orange fire glow, high contrast",
    "dramatic_side_light": "dramatic side lighting, high contrast chiaroscuro",
    "cosmic_glow": "otherworldly cosmic glow, unnatural light reflections",
    "neutral_daylight": "clear neutral daylight",
}

# القسم 4.2 بند 7 من README — برومبت سلبي لرفع الجودة
NEGATIVE_PROMPT = (
    "deformed hands, extra fingers, distorted face, warped anatomy, "
    "garbled text, watermark, logo, flat empty background, low detail"
)


def _fallback_image() -> str:
    candidates = [
        f for f in os.listdir(FALLBACK_DIR)
        if f.lower().endswith((".jpg", ".jpeg", ".png"))
    ]
    if not candidates:
        raise RuntimeError("No fallback images found in assets/fallback_images/")
    return os.path.join(FALLBACK_DIR, random.choice(candidates))


def _copy_fallback(out_path: str) -> str:
    fallback_path = _fallback_image()
    with open(fallback_path, "rb") as src, open(out_path, "wb") as dst:
        dst.write(src.read())
    return out_path


def _save_cloudflare_image(image_b64: str, out_path: str) -> str:
    raw = base64.b64decode(image_b64)
    image = Image.open(BytesIO(raw)).convert("RGB")
    image = ImageOps.fit(
        image, (IMG_WIDTH, IMG_HEIGHT),
        method=Image.Resampling.LANCZOS, centering=(0.5, 0.5),
    )
    image.save(out_path, format="JPEG", quality=95, optimize=True)
    return out_path


def build_full_prompt(segment: dict, style_lock: str) -> str:
    """يجمع الموضوع + زاوية الكاميرا + الإضاءة + المزاج + النمط الثابت — القسم 4.2 من README."""
    camera_phrase = CAMERA_ANGLE_PHRASES.get(segment.get("camera_angle", ""), "")
    lighting_phrase = LIGHTING_PHRASES.get(segment.get("lighting", ""), "")
    mood = segment.get("mood", "")

    parts = [
        segment["visual_prompt"],
        camera_phrase,
        lighting_phrase,
        f"{mood} mood" if mood else "",
        style_lock,
        "vertical 9:16 portrait framing",
        "no text overlay",
        "no watermark",
    ]
    return ", ".join(p for p in parts if p)


def generate_image(full_prompt: str, out_path: str, seed: int | None = None) -> str:
    if not CLOUDFLARE_API_TOKEN or not CLOUDFLARE_ACCOUNT_ID or not CLOUDFLARE_URL:
        print("[image_gen] Cloudflare credentials missing; using fallback image")
        return _copy_fallback(out_path)

    payload = {"prompt": full_prompt, "steps": 4, "negative_prompt": NEGATIVE_PROMPT}
    if seed is not None:
        payload["seed"] = int(seed)

    try:
        response = requests.post(
            CLOUDFLARE_URL,
            headers={
                "Authorization": f"Bearer {CLOUDFLARE_API_TOKEN}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()
        result = data.get("result") or {}
        image_b64 = result.get("image")
        if not image_b64:
            raise ValueError("Cloudflare response did not contain result.image")

        _save_cloudflare_image(image_b64, out_path)
        print("[image_gen] Cloudflare FLUX image generated successfully")
        return out_path

    except Exception as e:
        print(f"[image_gen] Cloudflare FLUX failed ({e}); using fallback image")
        return _copy_fallback(out_path)


def generate_images_for_segments(segments: list[dict], style_lock: str, workdir: str) -> list[str]:
    os.makedirs(workdir, exist_ok=True)
    paths = []

    # seed ثابت مشتق من style_lock لكل الفيديو، لزيادة الاتساق البصري بين المشاهد — القسم 4.3
    base_seed = abs(hash(style_lock)) % 100000

    for i, seg in enumerate(segments):
        out_path = os.path.join(workdir, f"img_{i:02d}.jpg")
        full_prompt = build_full_prompt(seg, style_lock)
        generate_image(full_prompt, out_path, seed=base_seed + i)
        paths.append(out_path)

    return paths


if __name__ == "__main__":
    demo_segment = {
        "visual_prompt": "a man bracing a door shut with his shoulder as it shakes violently",
        "camera_angle": "close_up",
        "lighting": "cold_blue_night",
        "mood": "tense",
    }
    demo_style_lock = "cinematic, photorealistic, hyper-detailed, dramatic lighting, film grain, 8k"
    print(generate_image(build_full_prompt(demo_segment, demo_style_lock), "/tmp/test_img.jpg", seed=42))
