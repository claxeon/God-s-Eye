"""
Assemble video from local scene images + audio using ffmpeg.
Output: output/<slug>/final.mp4
"""
import json
import subprocess
import tempfile
import shutil
from pathlib import Path
from config import OUTPUT_DIR


def _get_audio_duration(audio_path: str) -> float:
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", audio_path],
        capture_output=True, text=True, check=True
    )
    return float(json.loads(probe.stdout)["format"]["duration"])


def assemble_from_paths(slug: str, script: dict, scene_image_paths: list,
                        audio_path: str, thumb_path: str) -> str:
    """
    Assembles video from local file paths (no URL downloads).
    Works around ffmpeg's issues with special chars in paths by using a clean tmpdir.
    Returns path to final.mp4 in the output directory.
    """
    video_dir = Path(OUTPUT_DIR) / slug
    video_dir.mkdir(parents=True, exist_ok=True)
    output_path = video_dir / "final.mp4"

    audio_duration = _get_audio_duration(audio_path)
    print(f"  Audio: {audio_duration:.1f}s")

    total_declared = sum(s.get("duration_seconds", 30) for s in script["scenes"])

    # Use a clean tmpdir to avoid special chars (apostrophes, spaces) in paths
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)

        # Copy assets to clean paths
        clean_audio = tmp / "narration.mp3"
        shutil.copy2(audio_path, clean_audio)

        clean_images = []
        for i, img in enumerate(scene_image_paths):
            ext = Path(img).suffix
            dest = tmp / f"scene_{i+1:02d}{ext}"
            shutil.copy2(img, dest)
            clean_images.append(dest)

        # Write concat file
        concat_file = tmp / "concat.txt"
        with open(concat_file, "w") as f:
            for img, scene in zip(clean_images, script["scenes"]):
                dur = (scene.get("duration_seconds", 30) / total_declared) * audio_duration
                f.write(f"file '{img}'\n")
                f.write(f"duration {dur:.2f}\n")
            f.write(f"file '{clean_images[-1]}'\n")

        # Build slideshow
        slideshow = tmp / "slideshow.mp4"
        subprocess.run([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", str(concat_file),
            "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,"
                   "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=white,format=yuv420p",
            "-r", "24", "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            str(slideshow)
        ], check=True, capture_output=True)

        # Merge audio
        final_tmp = tmp / "final.mp4"
        subprocess.run([
            "ffmpeg", "-y",
            "-i", str(slideshow),
            "-i", str(clean_audio),
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            str(final_tmp)
        ], check=True, capture_output=True)

        shutil.copy2(final_tmp, output_path)

    size_mb = output_path.stat().st_size / 1e6
    print(f"  ✓ {output_path} ({size_mb:.1f} MB)")
    return str(output_path)
