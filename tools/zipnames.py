"""Корректное декодирование имён в zip со смешанными флагами (UTF-8 и без флага)."""
import zipfile

def real_name(i: zipfile.ZipInfo) -> tuple[str, str]:
    if i.flag_bits & 0x800:
        return i.filename, "utf8-flag"
    raw = i.filename.encode("cp437")
    for enc in ("utf-8", "cp866", "cp1251"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            pass
    return i.filename, "cp437"
