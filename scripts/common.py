#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"
SNAPSHOTS = ROOT / "data" / "snapshots"
ARCHIVE_INDEX = ROOT / "data" / "archive_index.json"
JST = timezone(timedelta(hours=9))
COLLECTOR_VERSION = "2.0.0"

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

def empty_manifest(target_date: str, location: dict, requested: list[str], collection_mode: str = "manual") -> dict:
    if collection_mode not in ("manual", "scheduled"):
        raise ValueError("collection_modeはmanualまたはscheduledです")
    return {"schema_version": 2, "target_date": target_date, "collection_mode": collection_mode,
            "generated_at": now(), "collector_version": COLLECTOR_VERSION, "location": location,
            "requested_sources": requested, "sources": [],
            "llm": {"status": "not_requested", "model": None, "text_only_fallback": False, "error": None}}

def build_archive_index(snapshots: Path = SNAPSHOTS, output: Path = ARCHIVE_INDEX) -> dict:
    """Rebuild the derived archive catalog without modifying any snapshot."""
    entries = []
    if snapshots.exists():
        for manifest_path in snapshots.glob("*/*/manifest.json"):
            manifest = read_json(manifest_path)
            location = manifest.get("location") or {}
            successful = [item.get("id") for item in manifest.get("sources", [])
                          if item.get("id") and item.get("status") in ("success", "cached")]
            relative_root = manifest_path.parent.relative_to(snapshots).as_posix()
            llm_path = manifest_path.parent / "llm-analysis.md"
            entries.append({
                "date": manifest.get("target_date") or manifest_path.parents[1].name,
                "location": location,
                "collection_mode": manifest.get("collection_mode", "manual"),
                "generated_at": manifest.get("generated_at"),
                "sources": successful,
                "source_count": len(successful),
                "llm_analysis": manifest.get("llm", {}).get("status") == "success" and llm_path.is_file(),
                "llm_model": manifest.get("llm", {}).get("model"),
                "manifest": f"snapshots/{relative_root}/manifest.json",
            })
    entries.sort(key=lambda item: (item["date"] or "", item["location"].get("id", "")), reverse=True)
    # Derived and deterministic: an unchanged archive must not create a needless commit.
    generated_at = max((item.get("generated_at") or "" for item in entries), default="") or None
    value = {"schema_version": 1, "generated_at": generated_at, "snapshots": entries}
    write_json(output, value)
    return value
