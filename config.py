import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DriveConfig:
    large_file_threshold_mb: int
    old_file_days: int
    check_duplicates: bool
    empty_trash: bool
    drive_token_file: str


@dataclass
class Config:
    use_ai_classification: bool
    trash_categories: dict[str, bool]
    protected_senders: list[str]
    blocked_senders: list[str]
    protected_labels: list[str]
    batch_size: int
    trash_delay_seconds: float
    ai_model: str
    credentials_file: str
    token_file: str
    drive: DriveConfig


def load_config(path: str = "config.toml") -> Config:
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(
            f"Config file not found: {path}\n"
            "Copy config.toml to this directory and edit it before running."
        )

    with open(config_path, "rb") as f:
        data = tomllib.load(f)

    classification = data.get("classification", {})
    protection = data.get("protection", {})
    api = data.get("api", {})
    paths = data.get("paths", {})
    drive_section = data.get("drive", {})

    trash_categories = classification.get("trash_categories", {})
    if not isinstance(trash_categories, dict):
        raise ValueError("config.toml: [classification.trash_categories] must be a table")

    return Config(
        use_ai_classification=classification.get("use_ai_classification", True),
        trash_categories={
            "promotions": trash_categories.get("promotions", True),
            "social": trash_categories.get("social", True),
            "updates": trash_categories.get("updates", True),
            "forums": trash_categories.get("forums", True),
        },
        protected_senders=[s.lower() for s in protection.get("protected_senders", [])],
        blocked_senders=[s.lower() for s in protection.get("blocked_senders", [])],
        protected_labels=protection.get("protected_labels", ["STARRED", "IMPORTANT"]),
        batch_size=api.get("batch_size", 100),
        trash_delay_seconds=api.get("trash_delay_seconds", 1.0),
        ai_model=api.get("ai_model", "claude-haiku-4-5-20251001"),
        credentials_file=paths.get("credentials_file", "credentials.json"),
        token_file=str(Path(paths.get("token_file", "~/.gmail-space-reducer/token.json")).expanduser()),
        drive=DriveConfig(
            large_file_threshold_mb=drive_section.get("large_file_threshold_mb", 50),
            old_file_days=drive_section.get("old_file_days", 365),
            check_duplicates=drive_section.get("check_duplicates", True),
            empty_trash=drive_section.get("empty_trash", True),
            drive_token_file=str(
                Path(paths.get("drive_token_file", "~/.gmail-space-reducer/drive_token.json")).expanduser()
            ),
        ),
    )
