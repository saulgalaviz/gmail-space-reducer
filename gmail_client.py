import os
import time
from pathlib import Path
from typing import Generator

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import BatchHttpRequest

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

CATEGORY_LABEL_MAP = {
    "promotions": "CATEGORY_PROMOTIONS",
    "social": "CATEGORY_SOCIAL",
    "updates": "CATEGORY_UPDATES",
    "forums": "CATEGORY_FORUMS",
}


def authenticate(credentials_path: str, token_path: str):
    token_file = Path(token_path)
    token_file.parent.mkdir(parents=True, exist_ok=True)

    creds = None
    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not Path(credentials_path).exists():
                raise FileNotFoundError(
                    f"credentials.json not found at: {credentials_path}\n"
                    "Download it from Google Cloud Console > APIs & Services > Credentials."
                )
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(token_file, "w") as f:
            f.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def list_messages_by_label(
    service,
    label_ids: list[str],
    batch_size: int = 100,
    limit: int | None = None,
) -> Generator[dict, None, None]:
    yielded = 0
    page_token = None

    while True:
        params = {
            "userId": "me",
            "labelIds": label_ids,
            "maxResults": min(batch_size, 500),
        }
        if page_token:
            params["pageToken"] = page_token

        response = service.users().messages().list(**params).execute()
        messages = response.get("messages", [])

        for msg in messages:
            yield msg
            yielded += 1
            if limit and yielded >= limit:
                return

        page_token = response.get("nextPageToken")
        if not page_token:
            break


def fetch_message_metadata(service, message_ids: list[str]) -> list[dict]:
    results = {}

    def handle_response(request_id, response, exception):
        if exception:
            return
        msg_id = response["id"]
        headers = {h["name"].lower(): h["value"] for h in response.get("payload", {}).get("headers", [])}
        results[msg_id] = {
            "id": msg_id,
            "subject": headers.get("subject", "(no subject)"),
            "sender": headers.get("from", ""),
            "snippet": response.get("snippet", ""),
            "size_estimate": response.get("sizeEstimate", 0),
            "label_ids": response.get("labelIds", []),
            "has_unsubscribe": "list-unsubscribe" in headers,
        }

    # Process in chunks of 100 (Gmail batch limit)
    for i in range(0, len(message_ids), 100):
        chunk = message_ids[i : i + 100]
        batch = service.new_batch_http_request(callback=handle_response)
        for msg_id in chunk:
            batch.add(
                service.users().messages().get(
                    userId="me",
                    id=msg_id,
                    format="metadata",
                    metadataHeaders=["Subject", "From", "List-Unsubscribe"],
                )
            )
        batch.execute()

    # Return in original order, skipping any that errored
    return [results[mid] for mid in message_ids if mid in results]


def trash_messages(
    service,
    message_ids: list[str],
    batch_size: int = 1000,
    delay_seconds: float = 1.0,
) -> int:
    chunks = [message_ids[i : i + batch_size] for i in range(0, len(message_ids), batch_size)]
    trashed = 0

    for idx, chunk in enumerate(chunks):
        service.users().messages().batchModify(
            userId="me",
            body={"ids": chunk, "addLabelIds": ["TRASH"], "removeLabelIds": ["INBOX"]},
        ).execute()
        trashed += len(chunk)
        if idx < len(chunks) - 1:
            time.sleep(delay_seconds)

    return trashed
