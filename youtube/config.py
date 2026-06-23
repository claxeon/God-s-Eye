"""
God's Eye — YouTube Automation Config
Kids educational channel: "Why? Science for Kids"
"""
import os

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
CLIENT_SECRET  = "/Users/leehutton/Desktop/client_secret_1079051446217-8gth5trq13l7t7nme3imb2tr3l2vr1ri.apps.googleusercontent.com.json"
TOKEN_FILE     = os.path.join(BASE_DIR, "token.json")
OUTPUT_DIR     = os.path.join(BASE_DIR, "output")
ASSETS_DIR     = os.path.join(BASE_DIR, "assets")

os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(ASSETS_DIR, exist_ok=True)

# ── Channel ───────────────────────────────────────────────────────────────────
CHANNEL_NAME   = "Why? Science for Kids"
CHANNEL_DESC   = "Simple answers to big questions! New videos every week."
CATEGORY_ID    = "27"        # YouTube category: Education
MADE_FOR_KIDS  = True        # COPPA compliance — required for kids content
DEFAULT_TAGS   = ["kids", "science", "education", "why", "for kids",
                  "learning", "educational", "kids science", "stem kids"]

# ── Voice ─────────────────────────────────────────────────────────────────────
VOICE_ID       = "fa64fba4-ad02-405e-99d0-1f085d87c706"   # Mabel — warm, friendly
VOICE_NAME     = "Mabel"

# ── Video style ───────────────────────────────────────────────────────────────
# Higgsfield image prompt prefix for consistent visual style
VISUAL_STYLE   = (
    "bright colorful children's educational illustration, "
    "friendly cartoon style, vibrant colors, soft lines, "
    "age 6-10, clean background, no text, high quality"
)

# ── Script ────────────────────────────────────────────────────────────────────
TARGET_AGE     = "6-10 year olds"
VIDEO_LENGTH   = "5-7 minutes"
WORDS_PER_MIN  = 130          # comfortable narration pace for kids
TARGET_WORDS   = 750          # ~5.75 min at 130 wpm

# ── Anthropic ─────────────────────────────────────────────────────────────────
ANTHROPIC_KEY  = os.environ.get("ANTHROPIC_API_KEY", "")
SCRIPT_MODEL   = "claude-haiku-4-5-20251001"   # fast + cheap for script gen

# ── YouTube scopes ────────────────────────────────────────────────────────────
YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
                  "https://www.googleapis.com/auth/youtube"]
