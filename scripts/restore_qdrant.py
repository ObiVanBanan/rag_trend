"""Restore the `steel_products_active` collection from a bundled Qdrant snapshot.

The snapshot file lives on the host (repo: `qdrant/*.snapshot`, delivered via Git LFS)
and is mounted into the Qdrant container at `/qdrant/snapshots` (see docker-compose.yml).

Usage:
    python scripts/restore_qdrant.py
    python scripts/restore_qdrant.py --url http://localhost:6333

Skips restoration when the collection already exists (idempotent).
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

from qdrant_client import QdrantClient

DEFAULT_COLLECTION = "steel_products_active"
DEFAULT_SNAPSHOT = Path("qdrant/steel_products_active.snapshot")
DEFAULT_CONTAINER_DIR = "/qdrant/snapshots"


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:6333", help="Qdrant base URL")
    parser.add_argument("--collection", default=DEFAULT_COLLECTION)
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=DEFAULT_SNAPSHOT,
        help="Path to the .snapshot file bundled via Git LFS (host path)",
    )
    parser.add_argument(
        "--container-dir",
        default=DEFAULT_CONTAINER_DIR,
        help="Path where ./qdrant is mounted inside the Qdrant container",
    )
    parser.add_argument(
        "--expected-checksum",
        default=None,
        help="Optional sha256 to verify snapshot integrity before restoring",
    )
    args = parser.parse_args()

    if not args.snapshot.is_file():
        print(f"Snapshot file not found: {args.snapshot}")
        print("Run `git lfs pull` first, then retry.")
        return 1

    checksum = sha256_of(args.snapshot)
    print(f"Snapshot: {args.snapshot} ({args.snapshot.stat().st_size / 1e6:.1f} MB)")
    print(f"sha256: {checksum}")
    if args.expected_checksum and checksum != args.expected_checksum:
        print(f"Checksum mismatch! Expected {args.expected_checksum}")
        return 1

    client = QdrantClient(url=args.url, timeout=600)

    existing = [c.name for c in client.get_collections().collections]
    if args.collection in existing:
        info = client.get_collection(args.collection)
        print(f"Collection `{args.collection}` already exists ({info.points_count} points). Nothing to do.")
        return 0

    snapshot_name = args.snapshot.name
    location = f"file://{args.container_dir.rstrip('/')}/{snapshot_name}"
    print(f"Restoring `{args.collection}` from {location} ...")

    client.recover_snapshot(
        collection_name=args.collection,
        location=location,
        wait=True,
    )

    info = client.get_collection(args.collection)
    if info.status != "green":
        print(f"Collection status is `{info.status}`, waiting for it to become green...")
        for _ in range(120):
            info = client.get_collection(args.collection)
            if info.status == "green":
                break
    print(f"Done. Points: {info.points_count}, status: {info.status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
