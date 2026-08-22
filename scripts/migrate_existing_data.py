#!/usr/bin/env python3
"""Copy the legacy state/assets into immutable date/location snapshots."""
from __future__ import annotations

import argparse
import copy
import mimetypes
import shutil
from pathlib import Path

from common import ROOT, empty_manifest, locations, now, read_json, safe_source_id, sha256, snapshot_dir, sources, write_json

LEGACY_STATE = ROOT / "data" / "state.json"
LEGACY_ASSETS = ROOT / "data" / "assets"

def payload_date(payload: dict) -> str | None:
    return payload.get("display_date") or str(payload.get("collected_at", ""))[:10] or None

def payload_location(payload: dict) -> str | None:
    return (payload.get("location") or {}).get("id")

def pick_latest(items: list[dict], source_id: str, target_date: str, location_id: str) -> dict | None:
    candidates = []
    for item in items:
        payload = item.get("payload", item)
        if payload.get("source_id") != source_id or payload_date(payload) != target_date:
            continue
        owner = payload_location(payload)
        if owner and owner != location_id:
            continue
        candidates.append(payload)
    return copy.deepcopy(candidates[-1]) if candidates else None

def migrate(force: bool = False) -> dict:
    state = read_json(LEGACY_STATE)
    location_map = {item["id"]: item for item in locations()}
    source_map = {item["id"]: item for item in sources()}
    runs = list(state.get("runs", []))
    runs += [{"payload": item} for item in state.get("observations", [])]
    runs += [{"payload": item} for item in state.get("forecasts", [])]
    raw_collections = state.get("collections", []) or [{"date": str(state.get("saved_at", ""))[:10], "location_id": state.get("current_location", "tokyo"), "sources": [item.get("source_id") for item in state.get("runs", [])]}]
    report = {"snapshots": 0, "sources_migrated": 0, "duplicates": 0, "metadata_incomplete": 0, "missing_references": 0}
    grouped = {}
    for collection in raw_collections:
        identity = (collection.get("date"), collection.get("location_id", "tokyo"))
        if identity in grouped:
            report["duplicates"] += 1
            grouped[identity]["sources"] = list(dict.fromkeys(grouped[identity].get("sources", []) + collection.get("sources", [])))
        else:
            grouped[identity] = copy.deepcopy(collection)

    for collection in grouped.values():
        target_date, location_id = collection.get("date"), collection.get("location_id", "tokyo")
        location = location_map.get(location_id)
        if not target_date or not location:
            report["metadata_incomplete"] += 1
            continue
        out = snapshot_dir(target_date, location_id)
        manifest_file = out / "manifest.json"
        if manifest_file.exists() and not force:
            report["duplicates"] += 1
            continue
        requested = list(dict.fromkeys(collection.get("sources", [])))
        manifest = empty_manifest(target_date, location, requested)
        manifest["provenance"] = {"type": "legacy_migration", "source": "data/state.json", "migrated_at": now(), "target_date_inferred": False}
        out.mkdir(parents=True, exist_ok=True)

        for source_id in requested:
            definition = source_map.get(source_id, {"id": source_id, "name": source_id, "category": "不明", "abbreviation": None, "view": None})
            payload = pick_latest(runs, source_id, target_date, location_id)
            entry = {"id": source_id, "name": definition["name"], "category": definition.get("category"),
                     "abbreviation": definition.get("abbreviation"), "original_url": (payload or {}).get("official_url") or definition.get("view"),
                     "local_path": None, "data_path": None, "mime_type": None, "fetched_at": (payload or {}).get("collected_at"),
                     "issued_at": (payload or {}).get("published_at"), "valid_from": None, "valid_to": None,
                     "content_hash": None, "cache_hit": False, "status": "unavailable", "error": None,
                     "used_by_llm": False, "provenance_note": "Legacy data; issue/valid times may be unknown."}
            if payload is None:
                entry["error"] = {"type": "legacy_missing", "message": "旧stateにこの日付・地点の参照可能なデータがありません。"}
                report["metadata_incomplete"] += 1
                manifest["sources"].append(entry)
                continue

            image_url = payload.pop("image_url", None)
            if source_id == "amedas":
                for row in payload.get("rows", []):
                    if "temperature" in row:
                        row["temperature_max_c"] = row.pop("temperature")
                    if "wind" in row:
                        row["wind_speed_max_m_s"] = row.pop("wind")
                payload["measurement_note"] = "旧実装が取得した日最高気温・最大風速。平均値ではありません。"
            data_name = safe_source_id(source_id) + ".json"
            write_json(out / "data" / data_name, payload)
            entry["data_path"] = "data/" + data_name
            entry["mime_type"] = "application/json"
            entry["content_hash"] = sha256(out / entry["data_path"])
            entry["status"] = "success"
            report["sources_migrated"] += 1

            if image_url:
                legacy = LEGACY_ASSETS / Path(image_url).name
                if legacy.is_file():
                    suffix = legacy.suffix.lower() or ".bin"
                    asset_name = safe_source_id(source_id) + suffix
                    destination = out / "assets" / asset_name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(legacy, destination)
                    entry["local_path"] = "assets/" + asset_name
                    entry["mime_type"] = mimetypes.guess_type(asset_name)[0] or "application/octet-stream"
                    entry["content_hash"] = sha256(destination)
                else:
                    entry["status"] = "failed"
                    entry["error"] = {"type": "missing_asset", "message": "旧画像ファイルを参照できません。"}
                    report["missing_references"] += 1
            manifest["sources"].append(entry)

        write_json(manifest_file, manifest)
        report["snapshots"] += 1
    return report

def main() -> None:
    parser = argparse.ArgumentParser(description="既存state/assetsをsnapshotへコピーします（元ファイルは変更しません）。")
    parser.add_argument("--force", action="store_true", help="既存の移行snapshotを作り直す")
    args = parser.parse_args()
    report = migrate(args.force)
    print("migration:", ", ".join(f"{key}={value}" for key, value in report.items()))

if __name__ == "__main__":
    main()
