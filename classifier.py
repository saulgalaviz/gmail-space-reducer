import json
from dataclasses import dataclass

import anthropic

from config import Config
from gmail_client import CATEGORY_LABEL_MAP


@dataclass
class ClassificationResult:
    message_id: str
    decision: str       # "KEEP" or "TRASH"
    reason: str
    size_estimate: int  # bytes


_SYSTEM_PROMPT = """\
You are an email classifier for a personal Gmail inbox cleanup tool.
Classify each email as KEEP or TRASH based on the sender, subject, and snippet.

TRASH if: promotional, newsletter, marketing, automated notification,
          social media alert, shipping update, app notification, survey,
          or anything the user clearly does not need to act on or reference later.

KEEP if: personal message from a real person, financial statement,
         legal document, job-related, government communication,
         account security alert, receipt the user may need,
         or anything that may require future action.

Respond with a JSON array ONLY. No text outside the JSON.
Format: [{"id": "...", "decision": "KEEP", "reason": "short phrase"}, ...]"""


def classify_by_rules(message: dict, config: Config) -> ClassificationResult | None:
    sender = message.get("sender", "").lower()
    label_ids = message.get("label_ids", [])

    # Protected sender or domain
    for protected in config.protected_senders:
        if protected.startswith("@"):
            if protected in sender:
                return ClassificationResult(message["id"], "KEEP", f"protected domain {protected}", message["size_estimate"])
        else:
            if protected in sender:
                return ClassificationResult(message["id"], "KEEP", f"protected sender", message["size_estimate"])

    # Starred
    if "STARRED" in label_ids:
        return ClassificationResult(message["id"], "KEEP", "starred", message["size_estimate"])

    # Sent or Draft
    if "SENT" in label_ids or "DRAFT" in label_ids:
        return ClassificationResult(message["id"], "KEEP", "sent/draft", message["size_estimate"])

    # Other protected labels from config
    for label in config.protected_labels:
        if label in label_ids and label not in ("STARRED", "IMPORTANT"):
            return ClassificationResult(message["id"], "KEEP", f"protected label {label}", message["size_estimate"])

    # Gmail IMPORTANT system label
    if "IMPORTANT" in label_ids and "IMPORTANT" in config.protected_labels:
        return ClassificationResult(message["id"], "KEEP", "marked important", message["size_estimate"])

    # Trash by category
    for category_key, gmail_label in CATEGORY_LABEL_MAP.items():
        if gmail_label in label_ids and config.trash_categories.get(category_key, False):
            return ClassificationResult(message["id"], "TRASH", gmail_label, message["size_estimate"])

    return None  # ambiguous — needs AI


def classify_by_ai(messages: list[dict], config: Config) -> list[ClassificationResult]:
    if not messages:
        return []

    client = anthropic.Anthropic()
    results = []

    # Batch 20 emails per API call
    for i in range(0, len(messages), 20):
        batch = messages[i : i + 20]
        payload = [
            {
                "id": m["id"],
                "sender": m["sender"],
                "subject": m["subject"],
                "snippet": m["snippet"][:200],  # cap snippet length
            }
            for m in batch
        ]

        try:
            response = client.messages.create(
                model=config.ai_model,
                max_tokens=1024,
                system=_SYSTEM_PROMPT,
                messages=[
                    {
                        "role": "user",
                        "content": f"Classify these emails:\n{json.dumps(payload, indent=2)}",
                    }
                ],
            )
            raw = response.content[0].text.strip()
            # Strip markdown code fences if present
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            classifications = json.loads(raw)
            id_to_result = {c["id"]: c for c in classifications}

            for m in batch:
                c = id_to_result.get(m["id"])
                if c and c.get("decision") in ("KEEP", "TRASH"):
                    results.append(
                        ClassificationResult(m["id"], c["decision"], c.get("reason", "AI decision"), m["size_estimate"])
                    )
                else:
                    # Fail safe: default to KEEP
                    results.append(ClassificationResult(m["id"], "KEEP", "AI parse error (defaulted to keep)", m["size_estimate"]))

        except (json.JSONDecodeError, anthropic.APIError, KeyError, IndexError) as e:
            print(f"  Warning: AI classification failed for batch ({e}). Defaulting to KEEP.")
            for m in batch:
                results.append(ClassificationResult(m["id"], "KEEP", "AI error (defaulted to keep)", m["size_estimate"]))

    return results


def classify_emails(messages: list[dict], config: Config, rules_only: bool = False) -> list[ClassificationResult]:
    results = []
    ambiguous = []

    for msg in messages:
        rule_result = classify_by_rules(msg, config)
        if rule_result is not None:
            results.append(rule_result)
        else:
            ambiguous.append(msg)

    if ambiguous and config.use_ai_classification and not rules_only:
        print(f"  Sending {len(ambiguous)} ambiguous emails to Claude for classification...")
        ai_results = classify_by_ai(ambiguous, config)
        results.extend(ai_results)
    else:
        # No AI: ambiguous emails default to KEEP
        for msg in ambiguous:
            results.append(ClassificationResult(msg["id"], "KEEP", "no category label (kept by default)", msg["size_estimate"]))

    return results
