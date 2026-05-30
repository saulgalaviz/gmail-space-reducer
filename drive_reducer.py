import argparse
import sys
from collections import defaultdict

from dotenv import load_dotenv
load_dotenv()

from config import load_config
from drive_client import (
    authenticate,
    empty_trash,
    list_files,
    list_trashed_files,
    permanently_delete_file,
    trash_file,
)
from drive_classifier import DriveFileResult, classify_files


def _fmt(n: int) -> str:
    if n >= 1_073_741_824:
        return f"{n / 1_073_741_824:.1f} GB"
    return f"{n / 1_048_576:.1f} MB"


def print_drive_summary(results: list[DriveFileResult], empty_trash_enabled: bool) -> None:
    perm = [r for r in results if r.decision == "PERMANENT_DELETE"]
    to_trash = [r for r in results if r.decision.startswith("TRASH_")]
    keep = [r for r in results if r.decision == "KEEP"]

    perm_bytes = sum(r.size_bytes for r in perm)
    trash_bytes = sum(r.size_bytes for r in to_trash)

    print(f"\n{'='*60}")
    print(f"  DRY RUN SUMMARY — Google Drive")
    print(f"{'='*60}")
    print(f"  Total scanned      : {len(results):,} files")
    print(f"  Permanent delete   : {len(perm):,} files ({_fmt(perm_bytes)} freed immediately)")
    if empty_trash_enabled:
        print(f"    (empty_trash=true: all Drive Trash will be cleared on --execute)")
    print(f"  Move to Trash      : {len(to_trash):,} files ({_fmt(trash_bytes)} freed after 30 days)")
    print(f"  Keep               : {len(keep):,} files")

    if to_trash:
        label_map = {
            "TRASH_DUPLICATE": "Duplicates",
            "TRASH_LARGE":     "Large files",
            "TRASH_OLD":       "Old/untouched",
        }
        by_decision: dict[str, list[DriveFileResult]] = defaultdict(list)
        for r in to_trash:
            by_decision[r.decision].append(r)

        print(f"\n  Breakdown (files to move to Trash):")
        for decision, group in sorted(by_decision.items()):
            label = label_map.get(decision, decision)
            print(f"    {label:<24} {len(group):>6,} files  ~{_fmt(sum(r.size_bytes for r in group))}")

    print(f"{'='*60}\n")


def confirm_execution(perm_count: int, perm_bytes: int, trash_count: int, trash_bytes: int) -> bool:
    print("This will:")
    if perm_count:
        print(f"  - Permanently delete {perm_count:,} already-trashed files ({_fmt(perm_bytes)} freed)")
    if trash_count:
        print(f"  - Move {trash_count:,} files to Trash ({_fmt(trash_bytes)} freed after 30 days)")
    try:
        answer = input("Proceed? [y/N]: ").strip().lower()
        return answer in ("y", "yes")
    except (KeyboardInterrupt, EOFError):
        print()
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Drive Space Reducer — classify and clean up Google Drive files."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", default=False, help="Show summary only, do not modify (default)")
    mode.add_argument("--execute", action="store_true", default=False, help="Show summary and prompt to confirm before modifying")
    parser.add_argument("--limit", type=int, default=None, metavar="N", help="Cap total non-trashed files processed")
    parser.add_argument("--config", type=str, default="config.toml", help="Path to config file")
    args = parser.parse_args()

    if not args.execute:
        args.dry_run = True

    try:
        config = load_config(args.config)
    except (FileNotFoundError, ValueError) as e:
        print(f"Error: {e}")
        sys.exit(1)

    print("Authenticating with Google Drive...")
    try:
        service = authenticate(config.credentials_file, config.drive.drive_token_file)
    except FileNotFoundError as e:
        print(f"Error: {e}")
        sys.exit(1)
    print("Authenticated.\n")

    print("Fetching Drive files...")
    non_trashed = list_files(service, page_size=100, limit=args.limit)
    trashed = list_trashed_files(service)
    all_files = non_trashed + trashed
    print(f"Found {len(non_trashed):,} files ({len(trashed):,} already in Trash).\n")

    print("Classifying files...")
    results: list[DriveFileResult] = classify_files(all_files, config)

    print_drive_summary(results, empty_trash_enabled=config.drive.empty_trash)

    perm = [r for r in results if r.decision == "PERMANENT_DELETE"]
    to_trash = [r for r in results if r.decision.startswith("TRASH_")]

    if not perm and not to_trash:
        print("Nothing to clean up based on current configuration.")
        sys.exit(0)

    if args.dry_run:
        print("Run with --execute to proceed.")
        sys.exit(0)

    perm_bytes = sum(r.size_bytes for r in perm)
    trash_bytes = sum(r.size_bytes for r in to_trash)
    if not confirm_execution(len(perm), perm_bytes, len(to_trash), trash_bytes):
        print("Aborted.")
        sys.exit(0)

    # Permanently delete already-trashed items
    if perm:
        print(f"\nPermanently deleting {len(perm):,} already-trashed files...")
        if config.drive.empty_trash:
            empty_trash(service)
            print("Drive Trash emptied.")
        else:
            failed = 0
            for r in perm:
                try:
                    permanently_delete_file(service, r.file_id)
                except Exception as e:
                    print(f"  Warning: could not delete {r.file_name!r}: {e}")
                    failed += 1
            print(f"Deleted {len(perm) - failed:,} files.")

    # Move large/old/duplicate files to Trash
    if to_trash:
        print(f"\nMoving {len(to_trash):,} files to Trash...")
        failed = 0
        for r in to_trash:
            try:
                trash_file(service, r.file_id)
            except Exception as e:
                print(f"  Warning: could not trash {r.file_name!r}: {e}")
                failed += 1
        succeeded = len(to_trash) - failed
        print(f"Moved {succeeded:,} files to Trash. Google Drive will permanently delete them after 30 days.")

    print("\nDrive cleanup complete.")


if __name__ == "__main__":
    main()
