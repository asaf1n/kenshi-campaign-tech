"""Вход в Google. Без аргументов: интерактивный вход (откроет браузер). `--check`: неинтерактивная
проверка OAuth-клиента (фиктивный code). Печатает только вердикт и источник, без значений."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.gdrive_api import check_client, get_credentials  # noqa: E402

if "--check" in sys.argv[1:]:
    ok, error, source = check_client()
    print(f"клиент принят (источник: {source})" if ok else f"клиент отклонён: {error} (источник: {source})")
    sys.exit(0 if ok else 1)

get_credentials(interactive=True)
print("токен сохранён")
