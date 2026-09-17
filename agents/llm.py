import os
import time

from dotenv import find_dotenv, load_dotenv
from google import genai
from google.genai import types

load_dotenv(find_dotenv(usecwd=True), override=True)

_key = os.getenv("GEMINI_API_KEY", "").strip()
if not _key:
    raise RuntimeError("GEMINI_API_KEY missing")

_client = genai.Client(api_key=_key)
MODEL = os.getenv("FAULTLINE_MODEL", "gemini-2.0-flash")

MAX_RETRIES = 4
BASE_DELAY = 8


def to_gemini_tools(tools: list[dict]) -> list[types.Tool]:
    decls = [
        types.FunctionDeclaration(
            name=t["name"],
            description=t["description"],
            parameters=t["input_schema"],
        )
        for t in tools
    ]
    return [types.Tool(function_declarations=decls)]


def complete(contents: list, tools: list[dict], system: str, max_tokens: int = 1024):
    config = types.GenerateContentConfig(
        system_instruction=system,
        tools=to_gemini_tools(tools),
        max_output_tokens=max_tokens,
        temperature=0,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )

    for attempt in range(MAX_RETRIES + 1):
        try:
            return _client.models.generate_content(
                model=MODEL,
                contents=contents,
                config=config,
            )
        except Exception as exc:
            transient = "RESOURCE_EXHAUSTED" in str(exc) or "503" in str(exc)
            if transient and attempt < MAX_RETRIES:
                time.sleep(BASE_DELAY * (2 ** attempt))
                continue
            raise