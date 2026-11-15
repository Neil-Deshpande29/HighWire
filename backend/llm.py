"""One place that decides which LLM the app talks to (Google Gemini via LangChain).

Reads the repo's .env on import. The key is GEMINI_API_KEY (or GOOGLE_API_KEY); get one free at
https://aistudio.google.com/apikey. Without a key every LLM feature falls back to deterministic code, so the app
and all validation numbers work identically offline. The LLM never computes a number shown in the UI: physics does.
"""
from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
except ImportError:  # python-dotenv is in requirements; keep the app importable without it
    pass

DEFAULT_MODEL = "gemini-3.5-flash"  # override with GEMINI_MODEL; see https://ai.google.dev/gemini-api/docs/models


def api_key() -> str | None:
    return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or None


def enabled() -> bool:
    return api_key() is not None


def model_name() -> str:
    return os.environ.get("GEMINI_MODEL", DEFAULT_MODEL).removeprefix("google_genai:").removeprefix("models/")


def chat_model(timeout: float = 45, max_retries: int = 1, max_tokens: int = 4096):
    """LangChain chat model for Gemini. Every call goes to the Gemini API generateContent endpoint
    (https://generativelanguage.googleapis.com) through the google-genai SDK."""
    from langchain_google_genai import ChatGoogleGenerativeAI

    return ChatGoogleGenerativeAI(model=model_name(), api_key=api_key(), temperature=0.2, max_tokens=max_tokens,
                                  timeout=timeout, max_retries=max_retries)
