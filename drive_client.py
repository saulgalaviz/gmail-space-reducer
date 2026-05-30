from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/drive"]

_FILE_FIELDS = "nextPageToken, files(id, name, size, mimeType, modifiedTime, viewedByMeTime, trashed, md5Checksum, owners)"


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

    return build("drive", "v3", credentials=creds)


def _paginate(service, page_size: int, limit: int | None, q: str | None) -> list[dict]:
    files = []
    page_token = None

    while True:
        params: dict = {"pageSize": min(page_size, 1000), "fields": _FILE_FIELDS}
        if q:
            params["q"] = q
        if page_token:
            params["pageToken"] = page_token

        response = service.files().list(**params).execute()
        files.extend(response.get("files", []))

        if limit and len(files) >= limit:
            return files[:limit]

        page_token = response.get("nextPageToken")
        if not page_token:
            break

    return files


def list_files(service, page_size: int = 100, limit: int | None = None) -> list[dict]:
    """List non-trashed Drive files owned by the user."""
    return _paginate(service, page_size, limit, q=None)


def list_trashed_files(service) -> list[dict]:
    """List all files currently in Drive Trash."""
    return _paginate(service, page_size=1000, limit=None, q="trashed = true")


def trash_file(service, file_id: str) -> None:
    service.files().update(fileId=file_id, body={"trashed": True}).execute()


def permanently_delete_file(service, file_id: str) -> None:
    service.files().delete(fileId=file_id).execute()


def empty_trash(service) -> None:
    service.files().emptyTrash().execute()
