"""Cached, provenance-recording downloads.

Every file downloaded by the pipeline goes through :func:`fetch`.  The file is stored once in
``data/cache/<subdir>/<name>`` next to a ``<name>.meta.json`` sidecar recording the URL, size,
SHA-256 and time of download, plus the source id from the source registry.  Re-running the
pipeline never re-downloads a file that is already cached (pass ``refresh=True`` to force).

Sidecars (rather than one shared manifest) keep concurrent domain builds from clobbering
each other; :func:`manifest` collects them.
"""
from __future__ import annotations

import hashlib
import json
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, unquote

import requests

from .config import CACHE

USER_AGENT = "hex-atlas-pipeline/1.0 (open-data research; python-requests)"


def _default_name(url: str) -> str:
    name = unquote(Path(urlparse(url).path).name) or "download"
    return name


def fetch(
    url: str,
    source_id: str,
    subdir: str,
    name: str | None = None,
    *,
    refresh: bool = False,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: int = 300,
    retries: int = 4,
    method: str = "GET",
    data: dict | str | None = None,
) -> Path:
    """Download ``url`` into the cache (once) and return the local path."""
    dest_dir = CACHE / subdir
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / (name or _default_name(url))
    meta_path = dest.with_name(dest.name + ".meta.json")
    if dest.exists() and dest.stat().st_size > 0 and not refresh:
        return dest

    hdrs = {"User-Agent": USER_AGENT}
    if headers:
        hdrs.update(headers)
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            with requests.request(method, url, params=params, headers=hdrs, data=data,
                                  stream=True, timeout=timeout) as r:
                r.raise_for_status()
                tmp = dest.with_name(dest.name + ".part")
                h = hashlib.sha256()
                n = 0
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1 << 20):
                        if chunk:
                            f.write(chunk)
                            h.update(chunk)
                            n += len(chunk)
                tmp.replace(dest)
                meta = {
                    "url": r.url if params else url,
                    "source_id": source_id,
                    "bytes": n,
                    "sha256": h.hexdigest(),
                    "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "content_type": r.headers.get("content-type"),
                }
                meta_path.write_text(json.dumps(meta, indent=2))
                return dest
        except Exception as e:  # noqa: BLE001 - retry any transport error
            last_err = e
            if isinstance(e, requests.HTTPError) and e.response is not None and e.response.status_code in (401, 403, 404):
                break
            time.sleep(2 ** attempt)
    raise RuntimeError(f"fetch failed for {url}: {last_err}")


def fetch_json(url: str, source_id: str, subdir: str, name: str, **kw):
    return json.loads(fetch(url, source_id, subdir, name, **kw).read_text())


def unzip(path: Path, member_suffix: str | None = None) -> Path:
    """Extract ``path`` next to itself (once) and return the extraction directory or a member."""
    out = path.with_suffix("")
    if not out.exists() or not any(out.iterdir()):
        out.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path) as z:
            z.extractall(out)
    if member_suffix:
        hits = sorted(p for p in out.rglob("*") if p.name.lower().endswith(member_suffix.lower()))
        if not hits:
            raise FileNotFoundError(f"no member ending {member_suffix} in {path}")
        return hits[0]
    return out


def manifest() -> list[dict]:
    """All cached downloads with their provenance sidecars."""
    rows = []
    for meta in sorted(CACHE.rglob("*.meta.json")):
        d = json.loads(meta.read_text())
        d["path"] = str(meta.with_name(meta.name[: -len(".meta.json")]).relative_to(CACHE))
        rows.append(d)
    return rows
