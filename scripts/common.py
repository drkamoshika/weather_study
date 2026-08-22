#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"
SNAPSHOTS = ROOT / "data" / "snapshots"
JST = timezone(timedelta(hours=9))

def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))

def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def locations() -> list[dict]:
    airports = read_json(CONFIG / "airports.json")
    return [{"id": r[0], "prefecture": r[1], "city": r[2], "latitude": r[3], "longitude": r[4],
             "forecast_area_code": r[5], "airport_icao": airports[r[0]][0], "airport_name": airports[r[0]][1]}
            for r in read_json(CONFIG / "locations.json")]

def sources() -> list[dict]:
    return read_json(CONFIG / "sources.json")

def now() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")

def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()

def safe_source_id(source_id: str) -> str:
    return source_id.replace(":", "-").replace("/", "-")

def snapshot_dir(target_date: str, location_id: str) -> Path:
    return SNAPSHOTS / target_date / location_id

def empty_manifest(target_date: str, location: dict, requested: list[str]) -> dict:
    return {"schema_version": 1, "target_date": target_date, "location": location,
            "requested_sources": requested, "generated_at": now(), "sources": [],
            "llm": {"status": "not_requested", "model": None, "text_only_fallback": False, "error": None}}
