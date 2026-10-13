import os

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True), override=True)

# The runner resets and snapshots the world directly on the toolserver, while
# the agent's tool calls always go through the fault-injecting proxy.
TOOLSERVER_URL = os.getenv("TOOLSERVER_URL", "http://127.0.0.1:8100")
PROXY_URL = os.getenv("PROXY_URL", "http://127.0.0.1:8200")

MODEL = os.getenv("FAULTLINE_MODEL", "gemini-2.0-flash")


def gemini_key() -> str:
    return os.getenv("GEMINI_API_KEY", "").strip()


def app_login() -> tuple[str, str]:
    """Optional login for the web app (empty password = open), for hosts with no proxy in front."""
    return os.getenv("FAULTLINE_USER", "admin"), os.getenv("FAULTLINE_PASSWORD", "")
