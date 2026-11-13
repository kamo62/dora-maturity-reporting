#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from devops_metrics.config.access import checked_path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Clean up old export directories by age")
    p.add_argument("--path", default=str(Path("exports").resolve()), help="Exports root path (default: ./exports)")
    p.add_argument("--retention-days", type=int, default=int(os.getenv("EXPORTS_RETENTION_DAYS", "30")), help="Retention window in days (default: 30)")
    p.add_argument("--dry-run", action="store_true", help="List what would be deleted without removing")
    p.add_argument("--interval-hours", type=int, default=0, help="If >0, run continuously with this interval in hours")
    return p.parse_args()


def cleanup_once(root: Path, retention_days: int, dry_run: bool = False) -> int:
    root = checked_path(root)
    now = datetime.now()
    cutoff = now - timedelta(days=retention_days)
    if not root.exists():
        print(f"[cleanup] Path does not exist: {root}")
        return 0

    removed = 0
    for entry in root.iterdir():
        try:
            checked_path(entry)
            if entry.is_symlink():
                continue
            if not entry.is_dir():
                continue
            # Check directory modified/created time
            mtime = datetime.fromtimestamp(entry.stat().st_mtime)
            if mtime > cutoff:
                continue
            # Safety: ensure directory name resembles timestamped export (YYYYMMDD or similar)
            # but do not solely rely on naming; we use mtime cutoff as decision.
            if dry_run:
                print(f"[cleanup] DRY RUN: would remove {entry} (last modified {mtime.isoformat()})")
            else:
                print(f"[cleanup] Removing {entry} (last modified {mtime.isoformat()})")
                for rootdir, dirs, files in os.walk(entry, topdown=False):
                    for f in files:
                        try:
                            os.remove(Path(rootdir) / f)
                        except Exception as e:  # noqa: BLE001
                            print(f"[cleanup] Warning: failed to remove file {f}: {e}")
                    for d in dirs:
                        try:
                            Path(rootdir, d).rmdir()
                        except Exception:
                            pass
                try:
                    entry.rmdir()
                except Exception as e:
                    print(f"[cleanup] Warning: failed to remove dir {entry}: {e}")
                removed += 1
        except Exception as e:  # noqa: BLE001
            print(f"[cleanup] Warning: skipping {entry}: {e}")
            continue
    print(f"[cleanup] Done. Removed {removed} directorie(s). Retention={retention_days}d Path={root}")
    return removed


def main() -> int:
    args = parse_args()
    root = Path(args.path).resolve()
    if args.interval_hours and args.interval_hours > 0:
        interval_sec = max(60, args.interval_hours * 3600)
        print(f"[cleanup] Starting loop. Path={root} Retention={args.retention_days}d Interval={args.interval_hours}h DryRun={args.dry_run}")
        try:
            while True:
                cleanup_once(root, args.retention_days, args.dry_run)
                time.sleep(interval_sec)
        except KeyboardInterrupt:
            print("[cleanup] Exiting on Ctrl+C")
            return 0
    else:
        cleanup_once(root, args.retention_days, args.dry_run)
        return 0


if __name__ == "__main__":
    sys.exit(main())
