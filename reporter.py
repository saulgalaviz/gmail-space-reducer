from collections import defaultdict

from classifier import ClassificationResult


def print_summary(results: list[ClassificationResult]) -> None:
    trash = [r for r in results if r.decision == "TRASH"]
    keep = [r for r in results if r.decision == "KEEP"]

    total_bytes = sum(r.size_estimate for r in trash)
    total_mb = total_bytes / 1_048_576

    print(f"\n{'='*60}")
    print(f"  DRY RUN SUMMARY")
    print(f"{'='*60}")
    print(f"  Total scanned : {len(results):,} emails")
    print(f"  To trash      : {len(trash):,} emails (~{total_mb:.1f} MB)")
    print(f"  To keep       : {len(keep):,} emails")

    if trash:
        # Group TRASH results by reason
        by_reason: dict[str, list[ClassificationResult]] = defaultdict(list)
        for r in trash:
            by_reason[r.reason].append(r)

        print(f"\n  Breakdown (emails to trash):")
        for reason, group in sorted(by_reason.items(), key=lambda x: -len(x[1])):
            group_mb = sum(r.size_estimate for r in group) / 1_048_576
            print(f"    {reason:<40} {len(group):>6,} emails  ~{group_mb:.1f} MB")

    print(f"{'='*60}\n")


def confirm_execution(trash_count: int, estimated_mb: float) -> bool:
    try:
        answer = input(f"Proceed with trashing {trash_count:,} emails (~{estimated_mb:.1f} MB freed)? [y/N]: ").strip().lower()
        return answer in ("y", "yes")
    except (KeyboardInterrupt, EOFError):
        print()
        return False
