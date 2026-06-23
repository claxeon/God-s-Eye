"""
God's Eye — YouTube Kids Pipeline (Free Stack)
================================================
Images: Pollinations.ai (Flux, free, no API key)
Audio:  edge-tts (Microsoft neural voices, free, local)
Upload: YouTube Data API v3

Usage:
    python3 pipeline.py --topic "Why is the sky blue?"
"""
import argparse
import json
import re
import os
from datetime import datetime
from pathlib import Path
from script_gen import generate_script
from tts import generate_audio
from images import generate_scene_images, generate_thumbnail
from assemble import assemble_from_paths
from upload import upload_video
from config import OUTPUT_DIR


def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    return text[:50]


def run_pipeline(topic: str, skip_upload: bool = False) -> dict:
    slug = slugify(topic)
    video_dir = Path(OUTPUT_DIR) / slug
    video_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"  Why? Science for Kids — Pipeline")
    print(f"  Topic: {topic}")
    print(f"{'='*60}\n")

    # 1. Script (skip if already written by Claude session)
    script_path = video_dir / "script.json"
    if script_path.exists():
        script = json.loads(script_path.read_text())
        print(f"[ 1/4 ] Script loaded from disk ({len(script['scenes'])} scenes)")
    else:
        print("[ 1/4 ] Generating script via API...")
        script = generate_script(topic)
        script_path.write_text(json.dumps(script, indent=2))
        print(f"  ✓ Script: {len(script['scenes'])} scenes")

    # 2. Audio (edge-tts, free)
    print("\n[ 2/4 ] Generating voiceover (edge-tts)...")
    full_narration = "\n\n".join(s["narration"] for s in script["scenes"])
    audio_path = str(video_dir / "narration.mp3")
    generate_audio(full_narration, audio_path)

    # 3. Images (Pollinations.ai, free)
    print("\n[ 3/4 ] Generating scene images (Pollinations.ai)...")
    scene_image_paths = generate_scene_images(script, str(video_dir))
    thumb_path = generate_thumbnail(script, str(video_dir))

    # 4. Assemble
    print("\n[ 4/4 ] Assembling video...")
    video_path = assemble_from_paths(slug, script, scene_image_paths, audio_path, thumb_path)

    # 5. Upload
    if skip_upload:
        print("\n  Skipping upload (--skip-upload)")
        return {"video_path": video_path, "script": script}

    print("\n[ 5/5 ] Uploading to YouTube...")
    video_id = upload_video(video_path, thumb_path, script)

    result = {
        "video_id":    video_id,
        "url":         f"https://youtu.be/{video_id}",
        "title":       script["title"],
        "video_path":  video_path,
        "produced_at": datetime.utcnow().isoformat(),
    }
    print(f"\n{'='*60}")
    print(f"  DONE → {result['url']}")
    print(f"{'='*60}\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--topic", required=True)
    parser.add_argument("--skip-upload", action="store_true")
    args = parser.parse_args()
    run_pipeline(topic=args.topic, skip_upload=args.skip_upload)
