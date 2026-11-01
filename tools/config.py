"""Конфигурация из окружения; .env автоматически не загружается."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from tools.io_utils import ExtractionError


@dataclass(frozen=True)
class Config:
    converter: Path
    save_dirs: tuple[Path, ...]
    game: Path
    workshop: Path
    faction_sid: str

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
        return cls(Path(env.get("KENSHI_CONVERTER_DIR") or Path.home() / "kenshi-platoon-export"),
                   saves, game, Path(env.get("KENSHI_WORKSHOP_DIR") or game.parent.parent / "workshop/content/233860"), faction)

    def protected(self) -> list[Path]:
        return [self.converter, self.game, self.workshop, *self.save_dirs]
