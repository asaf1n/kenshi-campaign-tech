"""Разовый интерактивный вход в Google (откроет браузер). Печатает только итог."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.gdrive_api import get_credentials  # noqa: E402

get_credentials(interactive=True)
print("токен сохранён")
