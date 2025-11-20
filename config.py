import os
from dotenv import load_dotenv

load_dotenv()

def _parse_csv_env(var_name: str, default: str = "") -> list[str]:
    """Return a list of non-empty, trimmed values from a comma-separated env var."""

    raw = os.getenv(var_name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


# Slack Configuration
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET")
ALLOWED_CHANNELS = _parse_csv_env("ALLOWED_CHANNELS")

# Gemini Configuration
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
FILE_SEARCH_STORE_NAME = os.getenv("FILE_SEARCH_STORE_NAME")

# Bot Behavior Configuration
AUTO_REPLY_CHANNELS = _parse_csv_env("AUTO_REPLY_CHANNELS")
BOT_TRIGGER_KEYWORDS = _parse_csv_env("BOT_TRIGGER_KEYWORDS", "제안서,proposal")

# Server Configuration
PORT = int(os.getenv("PORT", 8000))
HOST = os.getenv("HOST", "0.0.0.0")
