"""
Free TTS via edge-tts (Microsoft neural voices, no API key needed).
Voice: en-US-AriaNeural — warm, friendly, great for kids content.
"""
import asyncio
import edge_tts

VOICE = "en-US-AriaNeural"  # warm female voice, great for kids


async def _generate(text: str, output_path: str):
    communicate = edge_tts.Communicate(text, VOICE)
    await communicate.save(output_path)


def generate_audio(text: str, output_path: str):
    asyncio.run(_generate(text, output_path))
    print(f"  ✓ Audio saved → {output_path}")
