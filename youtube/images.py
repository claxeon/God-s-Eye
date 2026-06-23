"""
Free image generation via Pollinations.ai (Flux model, no API key).
Returns 1920x1080 images — just a URL fetch, completely free.
"""
import urllib.request
import urllib.parse
import time
from pathlib import Path


BASE_URL = "https://image.pollinations.ai/prompt"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}


def generate_image(prompt: str, output_path: str, width: int = 1920, height: int = 1080, seed: int = None) -> str:
    encoded = urllib.parse.quote(prompt)
    url = f"{BASE_URL}/{encoded}?width={width}&height={height}&nologo=true&model=flux"
    if seed is not None:
        url += f"&seed={seed}"

    print(f"  Generating image → {Path(output_path).name}")
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=60) as resp, open(output_path, "wb") as f:
        f.write(resp.read())
    size_kb = Path(output_path).stat().st_size / 1e3
    print(f"  ✓ {size_kb:.0f} KB")
    return output_path


def generate_scene_images(script: dict, output_dir: str) -> list[str]:
    """Generate one image per scene. Returns list of file paths."""
    paths = []
    for i, scene in enumerate(script["scenes"]):
        path = str(Path(output_dir) / f"scene_{i+1:02d}.jpg")
        generate_image(scene["visual_prompt"], path, seed=42 + i)
        paths.append(path)
        time.sleep(1)  # be polite to the free service
    return paths


def generate_thumbnail(script: dict, output_dir: str) -> str:
    path = str(Path(output_dir) / "thumbnail.jpg")
    generate_image(script["thumbnail_prompt"], path, seed=99)
    return path
