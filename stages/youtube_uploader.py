"""
stages/youtube_uploader.py
يرفع الفيديو النهائي على يوتيوب عبر YouTube Data API v3، مع علامة "AI-generated"
وإعادة محاولة تلقائية (حد أقصى 3 محاولات) عند الفشل.

يتطلب YOUTUBE_CREDENTIALS (JSON لتوكن OAuth مُولّد مسبقاً) كـ GitHub Secret.
"""

import json
import os
import time

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 15


def _get_credentials() -> Credentials:
    raw = os.environ.get("YOUTUBE_CREDENTIALS")
    if not raw:
        raise RuntimeError("YOUTUBE_CREDENTIALS environment variable is not set")
    info = json.loads(raw)
    return Credentials.from_authorized_user_info(info)


def upload_video(video_path: str, title: str, description: str, tags: list[str]) -> str:
    creds = _get_credentials()
    youtube = build("youtube", "v3", credentials=creds)

    body = {
        "snippet": {
            "title": title[:100],
            "description": description,
            "tags": tags,
            "categoryId": "22",
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False,
        },
        "containsSyntheticMedia": True,
    }

    media = MediaFileUpload(video_path, chunksize=-1, resumable=True, mimetype="video/mp4")

    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
            response = request.execute()
            return response["id"]
        except HttpError as e:
            last_error = e
            print(f"[youtube_uploader] Upload attempt {attempt} failed: {e}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)

    raise RuntimeError(f"Upload failed after {MAX_RETRIES} attempts: {last_error}")
