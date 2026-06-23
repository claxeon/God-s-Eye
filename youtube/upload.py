"""
Upload a finished video to YouTube with full metadata.
"""
import os
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from auth import get_credentials
from config import CATEGORY_ID, MADE_FOR_KIDS, DEFAULT_TAGS


def upload_video(video_path: str, thumbnail_path: str, script: dict) -> str:
    """
    Returns the YouTube video ID on success.
    """
    creds = get_credentials()
    youtube = build("youtube", "v3", credentials=creds)

    tags = list(set(DEFAULT_TAGS + script.get("tags", [])))

    body = {
        "snippet": {
            "title":       script["title"],
            "description": script["description"],
            "tags":        tags,
            "categoryId":  CATEGORY_ID,
        },
        "status": {
            "privacyStatus":          "public",
            "selfDeclaredMadeForKids": MADE_FOR_KIDS,
        }
    }

    media = MediaFileUpload(
        video_path,
        mimetype="video/mp4",
        resumable=True,
        chunksize=4 * 1024 * 1024  # 4 MB chunks
    )

    print(f"  Uploading: {script['title']}")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            pct = int(status.progress() * 100)
            print(f"  Upload: {pct}%", end="\r")

    video_id = response["id"]
    print(f"\n  ✓ Uploaded → https://youtu.be/{video_id}")

    # Set thumbnail (requires verified YouTube account)
    if os.path.exists(thumbnail_path):
        try:
            mime = "image/jpeg" if thumbnail_path.endswith((".jpg", ".jpeg")) else "image/png"
            youtube.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(thumbnail_path, mimetype=mime)
            ).execute()
            print(f"  ✓ Thumbnail set")
        except Exception as e:
            print(f"  ⚠ Thumbnail skipped (verify account at youtube.com/verify): {e}")

    return video_id
