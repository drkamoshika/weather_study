#!/usr/bin/env python3
"""Rebuild data/archive_index.json from immutable snapshot manifests."""
from common import ARCHIVE_INDEX, build_archive_index

if __name__ == "__main__":
    result = build_archive_index()
    print(f"indexed {len(result['snapshots'])} snapshots into {ARCHIVE_INDEX}")
