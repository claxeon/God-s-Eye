"""
Generate structured video scripts for "Why? Science for Kids"
Output: JSON with narration text + per-scene visual prompts + title + description + tags
"""
import json
import anthropic
from config import ANTHROPIC_KEY, SCRIPT_MODEL, TARGET_AGE, TARGET_WORDS, VISUAL_STYLE

SYSTEM_PROMPT = f"""You are a writer for a children's educational YouTube channel called "Why? Science for Kids".
Your audience is {TARGET_AGE}. Write with wonder, warmth, and simple language.
Use short sentences. Avoid jargon. Include one surprising fun fact per video.
Keep total narration to ~{TARGET_WORDS} words ({TARGET_WORDS // 130} minutes at 130 wpm)."""

SCRIPT_SCHEMA = """Return ONLY valid JSON with this exact structure:
{
  "title": "YouTube title (max 60 chars, starts with Why, How, or What, ends with ?)",
  "description": "2-3 sentence channel description for YouTube. Kid-friendly. Mention the fun fact.",
  "tags": ["tag1", "tag2", ...],  // 10 relevant tags
  "thumbnail_prompt": "Higgsfield image prompt for thumbnail: bright, eye-catching, colorful, relevant to topic",
  "scenes": [
    {
      "id": 1,
      "name": "Hook",
      "narration": "...",
      "visual_prompt": "Higgsfield image prompt: [STYLE] [SCENE DESCRIPTION]",
      "duration_seconds": 20
    },
    ... (5-7 scenes total)
  ],
  "fun_fact": "One surprising fact to highlight as on-screen text overlay"
}

Scene names must follow this arc: Hook → Question → Explanation → Example 1 → Example 2 → Fun Fact → Recap"""


def generate_script(topic: str) -> dict:
    """
    topic: e.g. "Why is the sky blue?"
    Returns parsed script dict.
    """
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

    prompt = f"""Create a complete educational video script for kids about: {topic}

{SCRIPT_SCHEMA}

Visual style to use in all visual_prompt fields: {VISUAL_STYLE}"""

    print(f"  Generating script for: {topic}")
    msg = client.messages.create(
        model=SCRIPT_MODEL,
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = msg.content[0].text.strip()
    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip().rstrip("```").strip()

    script = json.loads(raw)
    total_words = sum(len(s["narration"].split()) for s in script["scenes"])
    print(f"  ✓ Script: {len(script['scenes'])} scenes, ~{total_words} words, ~{total_words//130} min")
    return script


if __name__ == "__main__":
    import sys
    topic = sys.argv[1] if len(sys.argv) > 1 else "Why is the sky blue?"
    script = generate_script(topic)
    print(json.dumps(script, indent=2))
