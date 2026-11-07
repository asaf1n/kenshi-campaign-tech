"""Клиенты Google Drive v3 / Sheets v4 / Docs v1 (OAuth «Desktop app»).

Scope `drive` (полный): `drive.file` видит только файлы, созданные самим приложением, а
документы на Диске созданы Drive for desktop и ему недоступны. Секреты не хранятся в коде:
client_secret*.json и token.json лежат в %LOCALAPPDATA%/kenshi-campaign-tech/ (вне репозитория).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

SCOPES = ["https://www.googleapis.com/auth/drive"]
AUTH_HINT = "запустите scripts/gdrive_auth.py"


class GdriveAuthError(RuntimeError):
    """Нет токена или он не обновляется."""


def config_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "kenshi-campaign-tech"


def token_path() -> Path:
    return config_dir() / "token.json"


def find_client_secret() -> Path:
    for pattern in ("client_secret*.json", "credentials.json"):
        found = sorted(config_dir().glob(pattern))
        if found:
            return found[0]
    raise GdriveAuthError(f"не найден client_secret*.json / credentials.json в {config_dir()}")


def get_credentials(interactive: bool = False) -> Any:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    path = token_path()
    creds = Credentials.from_authorized_user_file(str(path), SCOPES) if path.exists() else None
    if creds and creds.valid:
        return creds
    if creds and creds.refresh_token:
        try:
            creds.refresh(Request())
            path.write_text(creds.to_json(), encoding="utf-8")
            return creds
        except Exception as exc:  # отозванный/просроченный refresh-токен
            if not interactive:
                raise GdriveAuthError(f"токен не обновляется ({type(exc).__name__}): {AUTH_HINT}") from exc
    if not interactive:
        raise GdriveAuthError(f"токен не найден: {AUTH_HINT}")
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(str(find_client_secret()), SCOPES)
    creds = flow.run_local_server(port=0)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(creds.to_json(), encoding="utf-8")
    return creds


def _build(name: str, version: str) -> Any:
    from googleapiclient.discovery import build

    return build(name, version, credentials=get_credentials(), cache_discovery=False)


def drive_service() -> Any:
    return _build("drive", "v3")


def sheets_service() -> Any:
    return _build("sheets", "v4")


def docs_service() -> Any:
    return _build("docs", "v1")
