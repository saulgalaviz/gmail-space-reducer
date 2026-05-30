import argparse
import sys

from dotenv import load_dotenv
load_dotenv()

from classifier import ClassificationResult, classify_emails
from config import load_config
from gmail_client import (
    CATEGORY_LABEL_MAP,
    authenticate,
    fetch_message_metadata,
    list_messages_by_label,
    trash_messages,
)
from reporter import confirm_execution, print_summary


def build_label_queries(config, category_filter: str | None) -> list[list[str]]:
    """Return a list of label-id lists to query. Each inner list is one query."""
    queries = []

    category_to_gmail = {k: v for k, v in CATEGORY_LABEL_MAP.items()}

    if category_filter:
        key = category_filter.lower()
        if key == "inbox":
            queries.append(["INBOX"])
        elif key not in category_to_gmail:
            print(f"Unknown category '{category_filter}'. Valid: promotions, social, updates, forums, inbox")
            sys.exit(1)
        else:
            queries.append([category_to_gmail[key]])
    else:
        for key, gmail_label in category_to_gmail.items():
            if config.trash_categories.get(key, False):
                queries.append([gmail_label])

        # Also query plain INBOX (no category label) if AI classification is enabled
        if config.use_ai_classification:
            queries.append(["INBOX"])

    return queries


def main():
    parser = argparse.ArgumentParser(
        description="Gmail Space Reducer — classify and trash non-important emails."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", default=False, help="Show summary only, do not trash (default)")
    mode.add_argument("--execute", action="store_true", default=False, help="Show summary and prompt to confirm before trashing")
    parser.add_argument("--rules-only", action="store_true", help="Skip Claude AI classification")
    parser.add_argument("--limit", type=int, default=None, metavar="N", help="Cap total emails processed")
    parser.add_argument("--category", type=str, default=None, help="Restrict to one category: promotions, social, updates, forums")
    parser.add_argument("--config", type=str, default="config.toml", help="Path to config file")
    args = parser.parse_args()

    # Default to dry-run if neither flag is passed
    if not args.execute:
        args.dry_run = True

    # Load config
    try:
        config = load_config(args.config)
    except (FileNotFoundError, ValueError) as e:
        print(f"Error: {e}")
        sys.exit(1)

    # Authenticate
    print("Authenticating with Gmail...")
    try:
        service = authenticate(config.credentials_file, config.token_file)
    except FileNotFoundError as e:
        print(f"Error: {e}")
        sys.exit(1)
    print("Authenticated.\n")

    # Determine which label queries to run
    label_queries = build_label_queries(config, args.category)
    if not label_queries:
        print("No categories enabled for trashing in config.toml. Nothing to do.")
        sys.exit(0)

    # Fetch candidate message stubs
    print("Fetching candidate emails...")
    seen_ids: set[str] = set()
    all_stubs: list[dict] = []

    remaining = args.limit

    for label_ids in label_queries:
        label_name = ", ".join(label_ids)
        for stub in list_messages_by_label(service, label_ids, config.batch_size, limit=remaining):
            if stub["id"] not in seen_ids:
                seen_ids.add(stub["id"])
                all_stubs.append(stub)
                if remaining is not None:
                    remaining -= 1
                    if remaining <= 0:
                        break
        if remaining is not None and remaining <= 0:
            break

    print(f"Found {len(all_stubs):,} candidate emails. Fetching metadata...")

    # Fetch metadata
    all_ids = [s["id"] for s in all_stubs]
    messages = fetch_message_metadata(service, all_ids)
    print(f"Metadata fetched for {len(messages):,} emails.\n")

    # Classify
    print("Classifying emails...")
    results: list[ClassificationResult] = classify_emails(messages, config, rules_only=args.rules_only)

    # Print summary
    print_summary(results)

    trash_results = [r for r in results if r.decision == "TRASH"]
    if not trash_results:
        print("Nothing to trash based on current configuration.")
        sys.exit(0)

    if args.dry_run:
        print("Run with --execute to proceed with trashing.")
        sys.exit(0)

    # Confirm and execute
    total_mb = sum(r.size_estimate for r in trash_results) / 1_048_576
    if not confirm_execution(len(trash_results), total_mb):
        print("Aborted.")
        sys.exit(0)

    print(f"\nTrashing {len(trash_results):,} emails...")
    trash_ids = [r.message_id for r in trash_results]
    count = trash_messages(service, trash_ids, batch_size=1000, delay_seconds=config.trash_delay_seconds)
    print(f"Done. Trashed {count:,} emails. Gmail will permanently delete them after 30 days.")


if __name__ == "__main__":
    main()
