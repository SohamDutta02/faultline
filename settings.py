import os

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True), override=True)

# The runner resets and snapshots the world directly on the toolserver, while
# the agent's tool calls always go through the fault-injecting proxy.
TOOLSERVER_URL = os.getenv("TOOLSERVER_URL", "http://127.0.0.1:8100")
PROXY_URL = os.getenv("PROXY_URL", "http://127.0.0.1:8200")

# The model id picks its provider (agents/backends.py); FAULTLINE_PROVIDER
# overrides the guess. Both empty: the first provider with a key, at its default model.
MODEL = os.getenv("FAULTLINE_MODEL", "").strip() or None
PROVIDER = os.getenv("FAULTLINE_PROVIDER", "").strip().lower() or None


def gemini_key() -> str:
    return os.getenv("GEMINI_API_KEY", "").strip()


def anthropic_key() -> str:
    return os.getenv("ANTHROPIC_API_KEY", "").strip()


def openai_key() -> str:
    return os.getenv("OPENAI_API_KEY", "").strip()


def openai_base_url() -> str:
    """An OpenAI-compatible server (vLLM, SGLang, Ollama, OpenRouter...); empty means api.openai.com."""
    return os.getenv("OPENAI_BASE_URL", "").strip()


def app_login() -> tuple[str, str]:
    """Optional login for the web app (empty password = open), for hosts with no proxy in front."""
    return os.getenv("FAULTLINE_USER", "admin"), os.getenv("FAULTLINE_PASSWORD", "")
