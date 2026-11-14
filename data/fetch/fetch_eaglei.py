"""Source 7: EAGLE-I county outages (15-min), figshare. Streams each national CSV and keeps only our counties.

Idempotent: skips years whose filtered file exists. Raw national files are never stored (they are 1+ GB each).
"""
from __future__ import annotations

import sys

import requests

from common import RAW, UA, log, region

L = log("eaglei")
ARTICLE = "https://api.figshare.com/v2/articles/24237376"
YEARS = [int(y) for y in (sys.argv[1:] or range(2018, 2026))]


def main():
    reg = region()
    fips = list(reg["counties"])
    prefixes = tuple(p.encode() for f in fips for p in (f + ",", f.lstrip("0") + ","))
    files = {f["name"]: f["download_url"] for f in requests.get(ARTICLE, headers=UA, timeout=60).json()["files"]}
    outdir = RAW / "eaglei"
    outdir.mkdir(parents=True, exist_ok=True)
    for y in YEARS:
        out = outdir / f"eaglei_{reg['name']}_{y}.csv"
        if out.exists() and out.stat().st_size > 0:
            L.info("%s exists, skipping", out.name)
            continue
        url = files.get(f"eaglei_outages_{y}.csv")
        if not url:
            L.error("no EAGLE-I file for %s", y)
            continue
        L.info("streaming %s ...", url)
        tmp = out.with_suffix(".part")
        kept = total = 0
        with requests.get(url, headers=UA, stream=True, timeout=300) as r, open(tmp, "wb") as fh:
            r.raise_for_status()
            buf = b""
            header_done = False
            for chunk in r.iter_content(chunk_size=1 << 22):
                buf += chunk
                lines = buf.split(b"\n")
                buf = lines.pop()
                for ln in lines:
                    if not header_done:
                        fh.write(ln + b"\n")
                        header_done = True
                        continue
                    total += 1
                    if ln.startswith(prefixes):
                        fh.write(ln + b"\n")
                        kept += 1
            if buf.startswith(prefixes):
                fh.write(buf + b"\n")
        tmp.rename(out)
        L.info("%s: kept %d of %d rows", out.name, kept, total)


if __name__ == "__main__":
    main()
