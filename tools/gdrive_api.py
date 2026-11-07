"""Клиенты Google Drive v3 / Sheets v4 / Docs v1 (OAuth «Desktop app»).

Scope `drive` (полный): `drive.file` видит только файлы, созданные самим приложением, а
документы на Диске созданы Drive for desktop и ему недоступны. Секреты не хранятся в коде.
Источник OAuth-клиента по порядку: (1) переменные окружения процесса
GOOGLE_CLOUD_OAUTH_CLIENT_ID / GOOGLE_CLOUD_OAUTH_CLIENT_SECRET; (2) на Windows те же
пользовательские переменные из реестра HKCU/Environment (только чтение); (3) файл
client_secret*.json / credentials.json в %LOCALAPPDATA%/kenshi-campaign-tech/. Там же token.json
(вне репозитория).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

SCOPES = ["https://www.googleapis.com/auth/drive"]
AUTH_HINT = "запустите scripts/gdrive_auth.py"
ENV_CLIENT_ID = "GOOGLE_CLOUD_OAUTH_CLIENT_ID"
ENV_CLIENT_SECRET = "GOOGLE_CLOUD_OAUTH_CLIENT_SECRET"
AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"


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


def _registry_user_env(name: str) -> str | None:
    """Пользовательская переменная Windows из HKCU/Environment (только чтение)."""
    if os.name != "nt":
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _ = winreg.QueryValueEx(key, name)
    except OSError:
        return None
    return value if isinstance(value, str) and value else None


def _installed_config(client_id: str, client_secret: str) -> dict[str, Any]:
    return {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": AUTH_URI,
            "token_uri": TOKEN_URI,
            "redirect_uris": ["http://localhost"],
        }
    }


def load_client_config() -> tuple[dict[str, Any], str]:
    """Возвращает (конфигурация «installed», источник: env | реестр | файл)."""
    pair = (os.environ.get(ENV_CLIENT_ID), os.environ.get(ENV_CLIENT_SECRET))
    if all(pair):
        return _installed_config(*pair), "env"  # type: ignore[arg-type]
    pair = (_registry_user_env(ENV_CLIENT_ID), _registry_user_env(ENV_CLIENT_SECRET))
    if all(pair):
        return _installed_config(*pair), "реестр"  # type: ignore[arg-type]
    import json

    cfg = json.loads(find_client_secret().read_text(encoding="utf-8"))
    return cfg, "файл"


def check_client() -> tuple[bool, str, str]:
    """Неинтерактивная проверка клиента без согласия пользователя.

    Фиктивный code: invalid_grant = клиент и секрет приняты; invalid_client /
    unauthorized_client = отклонены. Возвращает (принят, код ошибки, источник).
    """
    import json
    import urllib.error
    import urllib.parse
    import urllib.request

    cfg, source = load_client_config()
    inst = cfg.get("installed") or cfg.get("web") or {}
    body = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": "dummy-code-for-client-check",
        "redirect_uri": "http://localhost",
        "client_id": inst.get("client_id", ""),
        "client_secret": inst.get("client_secret", ""),
    }).encode()
    try:
        urllib.request.urlopen(urllib.request.Request(inst.get("token_uri", TOKEN_URI), data=body), timeout=30)
        return False, "неожиданный успешный ответ", source
    except urllib.error.HTTPError as exc:
        try:
            error = str(json.loads(exc.read().decode("utf-8")).get("error", f"http_{exc.code}"))
        except (ValueError, UnicodeDecodeError):
            error = f"http_{exc.code}"
    return error == "invalid_grant", error, source


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

    flow = InstalledAppFlow.from_client_config(load_client_config()[0], SCOPES)
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
