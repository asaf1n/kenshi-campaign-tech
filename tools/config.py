"""Конфигурация из окружения; .env автоматически не загружается."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from tools.io_utils import ExtractionError

# Подкаталоги повторяют раскладку репозитория Kenshi-Compain, ссылки выгрузки совпадают с путями на Диске.
GDRIVE_SESSION_DIR = "published"
GDRIVE_CHRONICLE_DIR = "Campaign"
GDRIVE_INBOX_DIR = "tmp/inbox"
GDRIVE_CHECKPOINTS_DIR = "tmp/checkpoints"
GDRIVE_PROCESSED_DIR = "tmp/processed"
GDRIVE_CANDIDATES = (
    Path("G:/Мой диск/Kenshi Compain. Docs"),
    Path("G:/Kenshi Compain. Docs"),
    Path("G:/My Drive/Kenshi Compain. Docs"),
)


def find_gdrive_root(environ: Mapping[str, str] | None = None,
                     candidates: tuple[Path, ...] = GDRIVE_CANDIDATES) -> Path | None:
    """Явный KENSHI_GDRIVE_DIR или первый существующий кандидат; None означает, что Диск не подключён."""
    env = os.environ if environ is None else environ
    if env.get("KENSHI_GDRIVE_DIR"):
        return Path(env["KENSHI_GDRIVE_DIR"])
    return next((path for path in candidates if path.is_dir()), None)


@dataclass(frozen=True)
class Config:
    save_dirs: tuple[Path, ...]
    game: Path
    workshop: Path
    faction_sid: str
    screens_dir: Path | None = None
    gdrive_root: Path | None = None

    @classmethod
    def from_env(cls, faction_sid: str | None = None,
                 environ: Mapping[str, str] | None = None) -> Config:
        env = os.environ if environ is None else environ
        game = Path(env.get("KENSHI_DIR") or "C:/Program Files (x86)/Steam/steamapps/common/Kenshi")
        local = Path(env.get("LOCALAPPDATA") or Path.home() / "AppData/Local")
        roots = env.get("KENSHI_SAVE_DIRS")
        saves = tuple(Path(p.strip()) for p in roots.split(";") if p.strip()) if roots else (local / "kenshi/save", game / "save")
        faction = faction_sid if faction_sid is not None else env.get("KENSHI_FACTION_SID", "204-gamedata.base")
        if not faction.strip():
            raise ExtractionError("faction_sid не может быть пустым")
        workshop = Path(env.get("KENSHI_WORKSHOP_DIR") or game.parent.parent / "workshop/content/233860")
        screens_raw = env.get("KENSHI_SCREENS_DIR")
        screens = Path(screens_raw) if screens_raw else (game / "_screens")
        return cls(saves, game, workshop, faction, screens, find_gdrive_root(env))

    @property
    def screens(self) -> Path:
        return self.screens_dir if self.screens_dir is not None else self.game / "_screens"

    def protected(self) -> list[Path]:
        return [self.game, self.workshop, self.screens, *self.save_dirs]
