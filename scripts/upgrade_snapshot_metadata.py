#!/usr/bin/env python3
"""Upgrade legacy manifests in place without removing snapshot artifacts."""
from common import SNAPSHOTS, build_archive_index, read_json, write_json

def upgrade() -> int:
    changed = 0
    for path in SNAPSHOTS.glob("*/*/manifest.json"):
        manifest = read_json(path)
        dirty = False
        if manifest.get("schema_version") != 2 or not manifest.get("collection_mode"):
            manifest["schema_version"] = 2
            manifest.setdefault("collection_mode", "manual")
            manifest.setdefault("collector_version", "legacy-migration")
            dirty = True
        markdown = path.parent / "llm-analysis.md"
        metadata = path.parent / "llm-analysis.json"
        llm = manifest.get("llm", {})
        if markdown.exists() and llm.get("status") == "success" and not metadata.exists():
            used = [item["id"] for item in manifest.get("sources", []) if item.get("used_by_llm")]
            generated_at = llm.get("generated_at") or manifest.get("generated_at")
            write_json(metadata, {"schema_version": 1, "model": llm.get("model"),
                                  "generated_at": generated_at, "input_sources": used,
                                  "markdown_path": "llm-analysis.md",
                                  "text_only_fallback": llm.get("text_only_fallback", False)})
            llm.update({"generated_at": generated_at, "input_sources": used,
                        "markdown_path": "llm-analysis.md", "metadata_path": "llm-analysis.json"})
            dirty = True
        if dirty:
            write_json(path, manifest)
            changed += 1
    build_archive_index()
    return changed

if __name__ == "__main__":
    print(f"upgraded {upgrade()} manifests")
