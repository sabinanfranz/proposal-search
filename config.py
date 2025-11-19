import os
from dotenv import load_dotenv

load_dotenv()

# Slack Configuration
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET")

# Gemini Configuration
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
FILE_SEARCH_STORE_NAME = os.getenv("FILE_SEARCH_STORE_NAME")

# Bot Behavior Configuration
AUTO_REPLY_CHANNELS = os.getenv("AUTO_REPLY_CHANNELS", "").split(",") if os.getenv("AUTO_REPLY_CHANNELS") else []
BOT_TRIGGER_KEYWORDS = os.getenv("BOT_TRIGGER_KEYWORDS", "제안서,proposal").split(",")

# Server Configuration
PORT = int(os.getenv("PORT", 8000))
HOST = os.getenv("HOST", "0.0.0.0")
