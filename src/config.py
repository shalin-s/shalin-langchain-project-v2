import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def _get(name: str, required: bool = True) -> str:
    val = os.environ.get(name, "")
    if required and not val:
        raise RuntimeError(f"missing required env var: {name}")
    return val


OPENAI_API_KEY = _get("OPENAI_API_KEY")
SLACK_BOT_TOKEN = _get("SLACK_BOT_TOKEN", required=False)
SLACK_APP_TOKEN = _get("SLACK_APP_TOKEN", required=False)
SLACK_SIGNING_SECRET = _get("SLACK_SIGNING_SECRET", required=False)

OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o")
