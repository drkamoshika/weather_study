#!/usr/bin/env python3
"""Build a self-contained GitHub Pages site from saved snapshots."""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from common import ROOT, SNAPSHOTS, locations, read_json, sources, write_json

WEB = ROOT / "web"
DIST = ROOT / "dist"

def build(output: Path = DIST) -> int:
    if output.exists():
        shutil.rmtree(output)
    shutil.copytree(WEB, output)
    entries = []
    if SNAPSHOTS.exists():
        shutil.copytree(SNAPSHOTS, output / "snapshots")
        for manifest_path in sorted(SNAPSHOTS.glob("*/*/manifest.json")):
            manifest = read_json(manifest_path)
            relative = manifest_path.relative_to(SNAPSHOTS).as_posix()
            entries.append({"date": manifest.get("target_date"), "location": manifest.get("location"),
                            "manifest": "snapshots/" + relative,
                            "statuses": {item["id"]: item["status"] for item in manifest.get("sources", [])}})
    entries.sort(key=lambda item: (item["date"], item["location"]["id"]), reverse=True)
    public_sources = [{key: source.get(key) for key in ("id", "name", "category", "abbreviation", "view")}
                      for source in sources()]
    write_json(output / "index.json", {"schema_version": 1, "snapshots": entries,
                                        "locations": locations(), "sources": public_sources,
                                        "glossary": read_json(ROOT / "config" / "glossary.json")})
    (output / ".nojekyll").touch()
    return len(entries)

def main() -> None:
    parser = argparse.ArgumentParser(description="data/snapshotsから静的サイトを生成します。")
    parser.add_argument("--output", type=Path, default=DIST)
    args = parser.parse_args()
    print(f"built {build(args.output)} snapshots into {args.output}")

if __name__ == "__main__":
    main()
