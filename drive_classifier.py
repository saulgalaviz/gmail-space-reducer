from dataclasses import dataclass
from datetime import datetime, timezone

from config import Config

WORKSPACE_MIME_TYPES = {
    "application/vnd.google-apps.document",
    "application/vnd.google-apps.spreadsheet",
    "application/vnd.google-apps.presentation",
    "application/vnd.google-apps.form",
    "application/vnd.google-apps.drawing",
    "application/vnd.google-apps.site",
    "application/vnd.google-apps.map",
}


@dataclass
class DriveFileResult:
    file_id: str
    decision: str  # PERMANENT_DELETE | TRASH_DUPLICATE | TRASH_LARGE | TRASH_OLD | KEEP
    reason: str
    size_bytes: int
    file_name: str


def _owned_by_me(file: dict) -> bool:
    return any(o.get("me", False) for o in file.get("owners", []))


def _parse_dt(ts: str | None) -> datetime | None:
    if not ts:
        return None
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def classify_files(files: list[dict], config: Config) -> list[DriveFileResult]:
    now = datetime.now(timezone.utc)
    large_threshold = config.drive.large_file_threshold_mb * 1_048_576

    # Build duplicate set: for each md5Checksum group, keep newest-modified; flag the rest
    duplicate_ids: set[str] = set()
    if config.drive.check_duplicates:
        checksum_groups: dict[str, list[dict]] = {}
        for f in files:
            checksum = f.get("md5Checksum")
            if checksum and not f.get("trashed", False):
                checksum_groups.setdefault(checksum, []).append(f)

        for group in checksum_groups.values():
            if len(group) > 1:
                sorted_group = sorted(
                    group,
                    key=lambda f: _parse_dt(f.get("modifiedTime")) or datetime.min.replace(tzinfo=timezone.utc),
                    reverse=True,
                )
                for dup in sorted_group[1:]:
                    duplicate_ids.add(dup["id"])

    results = []
    for f in files:
        file_id = f["id"]
        name = f.get("name", "(unnamed)")
        size_bytes = int(f.get("size") or 0)
        mime_type = f.get("mimeType", "")
        is_trashed = f.get("trashed", False)
        is_workspace = mime_type in WORKSPACE_MIME_TYPES

        if not _owned_by_me(f):
            results.append(DriveFileResult(file_id, "KEEP", "not owned by me", size_bytes, name))
            continue

        # Priority 1: already in Drive Trash → permanently delete
        if is_trashed:
            results.append(DriveFileResult(file_id, "PERMANENT_DELETE", "already in Drive Trash", size_bytes, name))
            continue

        # Priority 2: duplicate content → trash
        if file_id in duplicate_ids:
            results.append(DriveFileResult(file_id, "TRASH_DUPLICATE", "duplicate file", size_bytes, name))
            continue

        # Priority 3: large file (Workspace types have no byte size, skip)
        if not is_workspace and size_bytes >= large_threshold:
            mb = size_bytes / 1_048_576
            results.append(DriveFileResult(file_id, "TRASH_LARGE", f"large file ({mb:.0f} MB)", size_bytes, name))
            continue

        # Priority 4: not touched in old_file_days
        modified = _parse_dt(f.get("modifiedTime"))
        viewed = _parse_dt(f.get("viewedByMeTime"))
        candidates = [t for t in (modified, viewed) if t is not None]
        last_activity = max(candidates) if candidates else None
        if last_activity and (now - last_activity).days >= config.drive.old_file_days:
            age_days = (now - last_activity).days
            results.append(DriveFileResult(file_id, "TRASH_OLD", f"not touched in {age_days} days", size_bytes, name))
            continue

        results.append(DriveFileResult(file_id, "KEEP", "recent or small", size_bytes, name))

    return results
