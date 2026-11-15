"""Shared helpers for the data fetchers: paths, region config, logging, cached downloads."""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"
UA = {"User-Agent": "rewire-hackathon-research/0.1 (grid risk validation)"}

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass


def region(name: str | None = None) -> dict:
    name = name or os.environ.get("REWIRE_REGION") or "la"
    return yaml.safe_load((ROOT / "config" / f"region_{name}.yaml").read_text())


def log(name: str) -> logging.Logger:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    return logging.getLogger(name)


def get(url: str, params: dict | None = None, retries: int = 4, timeout: int = 120, **kw) -> requests.Response:
    """GET with polite retries/backoff (429/5xx)."""
    last = None
    for i in range(retries):
        try:
            r = requests.get(url, params=params, timeout=timeout, headers={**UA, **kw.pop("headers", {})}, **kw)
            if r.status_code in (429, 500, 502, 503, 504):
                last = RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
                time.sleep(5 * (i + 1) ** 2)
                continue
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            last = e
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"GET failed after {retries} tries: {url} ({last})")


def cached_json(path: Path, fetch):
    """Return JSON at path, fetching (and saving) only if missing."""
    if path.exists() and path.stat().st_size > 0:
        return json.loads(path.read_text())
    path.parent.mkdir(parents=True, exist_ok=True)
    data = fetch()
    path.write_text(json.dumps(data))
    return data
