"""GMDCSA-24 целиком (160 роликов): строки videos.md из CSV авторов набора.

Запуск из папки ml/ (архив Zenodo берётся из кэша demo/videos/_src или качается один раз):
py -m eval.gmdcsa sync   пересобрать строки gmdcsa-* в videos.md (SHA256 - по архиву)

Источник: Zenodo 10.5281/zenodo.13354453, CC BY 4.0, Ekram Alam и др.
Разметка - Subject N/Fall.csv и ADL.csv, колонка Classes:
«Falling (SW)[3.4 to 6]; Sitting[0 to 3.4]».
Нарушение - только класс Falling/Fall: интервал [с, по] как у авторов (с лежанием).
Бытовые сценарии (ADL: лёг на кровать, сел, ходьба) - ролики без меток: проверка ложных падений.
"""

from __future__ import annotations

import hashlib
import io
import re
import sys
import zipfile
from pathlib import Path

from eval import videos

ZENODO = (
    "https://zenodo.org/api/records/13354453/files/ekramalam/"
    "GMDCSA24-A-Dataset-for-Human-Fall-Detection-in-Videos-v2.1.zip/content"
)
PREFIX = "ekramalam-GMDCSA24-A-Dataset-for-Human-Fall-Detection-in-Videos-5abac76/"
AUTHOR = "Ekram Alam и др., GMDCSA24 (Zenodo 10.5281/zenodo.13354453)"
LICENSE = "CC BY 4.0 (страница Zenodo)"
RELEASE = "да, с указанием автора"
SOURCE = "CSV авторов набора"
CLASS_RE = re.compile(
    r"([A-Za-z][A-Za-z ]*?)\s*(?:\(\s*[A-Z]+\s*\)?)?"
    r"\s*\[\s*(\d+(?:\.\d+)?)\s*(?:to)?\s*(\d+(?:\.\d+)?)\s*\]"
)


def parse_classes(text: str) -> list[tuple[str, float, float]]:
    """«Falling (SW)[3.4 to 6]; Sitting[0 to 3.4]» -> [(Falling, 3.4, 6.0), (Sitting, 0.0, 3.4)]."""
    return [(m[1].strip(), float(m[2]), float(m[3])) for m in CLASS_RE.finditer(text)]


def parse_row(line: str) -> dict | None:
    """Строка CSV: File Name,Length,Time,Attire,Description,Classes.

    В описании бывают запятые: Classes - всё после последней запятой."""
    parts = line.strip().split(",")
    if len(parts) < 6 or not parts[0].strip().endswith(".mp4"):
        return None
    length = float(parts[1]) if parts[1].strip().replace(".", "").isdigit() else None
    classes = parse_classes(parts[-1])
    # опечатка в CSV (Subject 4/Fall/15): Falling без интервала - от конца прочих классов до конца
    if length and not fall_intervals(classes) and re.search(r"\bFall", parts[-1].split(";")[0]):
        classes.append(("Falling", max([b for _, _, b in classes], default=0.0), length))
    return {
        "name": parts[0].strip(),
        "length": length,
        "description": ",".join(parts[4:-1]).strip().replace("|", "/"),
        "classes": classes,
    }


def fall_intervals(classes: list[tuple[str, float, float]]) -> list[tuple[float, float]]:
    return [(a, b) for c, a, b in classes if c.lower().startswith("fall")]


def fmt(x: float) -> str:
    return f"{x:g}"


def intervals_text(classes: list[tuple[str, float, float]]) -> str:
    ivs = fall_intervals(classes)
    if not ivs:
        return f"нет ({SOURCE}; бытовой сценарий - проверка ложных падений)"
    return "; ".join(f"fall {fmt(a)}-{fmt(b)}" for a, b in ivs) + f" ({SOURCE})"


def rows_from_zip(zpath: Path) -> list[dict[str, str]]:
    out = []
    with zipfile.ZipFile(zpath) as z:
        names = set(z.namelist())
        for subj in range(1, 5):
            for kind, folder in (("fall", "Fall"), ("adl", "ADL")):
                base = f"{PREFIX}Subject {subj}/"
                text = z.read(f"{base}{folder}.csv").decode("utf-8-sig")
                for line in io.StringIO(text).read().splitlines()[1:]:
                    r = parse_row(line)
                    if not r:
                        continue
                    member = f"{base}{folder}/{r['name']}"
                    if member not in names:
                        raise SystemExit(f"в архиве нет {member}")
                    h = hashlib.sha256(z.read(member)).hexdigest()
                    stem = r["name"].removesuffix(".mp4")
                    scen = r["description"] or folder
                    if kind == "adl":
                        scen = f"бытовой сценарий (не падение): {scen}"
                    out.append(
                        {
                            "file": f"gmdcsa-s{subj}-{kind}-{stem}.mp4",
                            "url": f"{ZENODO}#{member}",
                            "sha256": h,
                            "author": AUTHOR,
                            "license": LICENSE,
                            "release": RELEASE,
                            "scenario": scen,
                            "intervals": intervals_text(r["classes"]),
                        }
                    )
    return out


def sync(rows: list[dict[str, str]], md: str) -> str:
    """Заменяет строки gmdcsa-* в videos.md сгенерированными (на месте первой старой)."""
    lines = md.splitlines()
    new = ["| " + " | ".join(r[c] for c in videos.COLUMNS) + " |" for r in rows]
    out, done = [], False
    for ln in lines:
        if ln.startswith("| gmdcsa-"):
            if not done:
                out.extend(new)
                done = True
            continue
        out.append(ln)
    if not done:
        raise SystemExit("в videos.md нет места для строк gmdcsa-*")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv != ["sync"]:
        print(__doc__)
        return 2
    zurl = ZENODO
    cache = videos.VIDEOS / "_src"
    zpath = cache / hashlib.sha256(zurl.encode()).hexdigest()[:16]
    if not zpath.exists():
        cache.mkdir(parents=True, exist_ok=True)
        videos.download(zurl, zpath)
    rows = rows_from_zip(zpath)
    videos.CATALOG.write_text(
        sync(rows, videos.CATALOG.read_text(encoding="utf-8")), encoding="utf-8"
    )
    print(f"gmdcsa: {len(rows)} роликов, падений {sum('fall ' in r['intervals'] for r in rows)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
