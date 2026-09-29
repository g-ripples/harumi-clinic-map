#!/usr/bin/env python3
"""data/clinics.yaml から README.md・dist/card-a4.html・dist/maps*.csv を生成する。

    python scripts/build.py                     # 通常（距離はキャッシュを使用）
    python scripts/build.py --refresh-distance  # 徒歩距離を再計算
    python scripts/build.py --refresh-geo       # 自動補完した座標を取り直し、距離も再計算
    python scripts/build.py --offline           # 外部 API を一切呼ばない
"""

from __future__ import annotations

import argparse
import base64
import csv
import datetime as dt
import fnmatch
import subprocess
import sys
import urllib.parse

import jinja2
import qrcode
import qrcode.image.svg
import yaml

import geo
from common import (CARD, DATA, DAYS, GROUPS, ROOT, STALE_DAYS, DataError,
                    fmt_day, group_days, load, tel_href)

DIST = ROOT / "dist"
README = ROOT / "README.md"

# git に追跡されてはいけないファイル（元の xlsx は旧居の個人メモを含む）
FORBIDDEN_TRACKED = ["*.xlsx", "*.xls", "*.xlsm", "personal*.yaml", "*.local.yaml", "dist/*"]


def badge(label: str, message: str, color: str) -> str:
    q = lambda s: urllib.parse.quote(s.replace("-", "--").replace("_", "__"), safe="")
    return f"![{label} {message}](https://img.shields.io/badge/{q(label)}-{q(message)}-{color})"


def env(autoescape: bool) -> jinja2.Environment:
    e = jinja2.Environment(
        loader=jinja2.FileSystemLoader(ROOT / "templates"),
        autoescape=autoescape, trim_blocks=True, lstrip_blocks=True,
        undefined=jinja2.StrictUndefined, keep_trailing_newline=True)
    e.globals.update(fmt_day=fmt_day, group_days=group_days, tel_href=tel_href, DAYS=DAYS)
    return e


def holiday_text(c, sep=", ") -> str:
    text = fmt_day(c.hours.get("PH"), sep)
    return text + (" ⚠️" if c.renkyu_trap else "")


def render_readme(data) -> str:
    clinics = data["clinics"]
    stale = [c for c in clinics if c.stale]
    holiday_open = [c for c in clinics if c.open_holiday]
    summary = sorted([c for c in clinics if c.open_sunday or c.open_holiday], key=lambda c: c.sort_key)
    groups = []
    for key, label in GROUPS:
        members = sorted([c for c in clinics if c.group == key], key=lambda c: c.sort_key)
        if members:
            groups.append((key, label, members))
    return env(False).get_template("README.md.j2").render(
        today=data["today"], origin=data["origin"], summary=summary, groups=groups,
        badges=[badge("要確認", f"{len(stale)}件", "orange" if stale else "brightgreen"),
                badge("祝日可", f"{len(holiday_open)}件", "blue")],
        stale_days=STALE_DAYS, no_distance=[c for c in clinics if not c.walk_m],
        estimated=any(c.walk_m_source == "estimated" for c in clinics),
        holiday_text=holiday_text)


def qr_data_uri(url: str) -> str:
    img = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
    return "data:image/svg+xml;base64," + base64.b64encode(img.to_string()).decode()


def render_card(data) -> str:
    card = yaml.safe_load(CARD.read_text(encoding="utf-8"))
    by_id = {c.id: c for c in data["clinics"]}
    routes = []
    for r in card["routes"]:
        items = []
        for ref in r.get("clinics", []):
            if ref["id"] not in by_id:
                raise DataError(f"card.yaml: 未知の id {ref['id']!r}")
            c = by_id[ref["id"]]
            if not c.tel:
                raise DataError(f"card.yaml: {c.name} に電話番号がない")
            items.append({"c": c, "hours": group_days(c.hours, ref.get("show_days", ["Su", "PH"]))})
        routes.append({"title": r["title"], "items": items, "note": r.get("note"),
                       "phones": r.get("phones", [])})
    return env(True).get_template("card-a4.html.j2").render(
        card=card, routes=routes, origin=data["origin"], today=data["today"],
        qr=qr_data_uri(card["repo_url"]))


CSV_COLS = ["医院名", "住所", "電話", "診療科", "祝日", "備考", "徒歩距離(m)", "徒歩(分)"]


def csv_row(c) -> list:
    notes = [n for n in (c.holiday_note, c.note) if n]
    return [c.name, c.address or "", c.tel or "", "・".join(c.depts), holiday_text(c),
            " / ".join(notes), c.walk_m or "", c.walk_min or ""]


def write_csv(path, rows) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(CSV_COLS)
        w.writerows(rows)


def write_maps(data) -> list[str]:
    # 住所がないとマイマップで位置を決められないので除外
    clinics = [c for c in sorted(data["clinics"], key=lambda c: c.sort_key) if c.address]
    o = data["origin"]
    origin_row = [o["name"], o["address"], "", "基準点", "", o.get("note", ""), 0, 0]
    write_csv(DIST / "maps.csv", [csv_row(c) for c in clinics] + [origin_row])
    written = ["dist/maps.csv"]
    tags = sorted({t for c in clinics for t in c.tags})
    for t in tags:
        name = f"maps-{t}.csv"
        write_csv(DIST / name, [csv_row(c) for c in clinics if t in c.tags])
        written.append(f"dist/{name}")
    return written


def git_self_check() -> None:
    try:
        git = lambda *a: subprocess.run(["git", "ls-files", "-z", *a], cwd=ROOT, check=True,
                                        capture_output=True, text=True).stdout.split("\0")
        tracked, untracked = git(), git("--others", "--exclude-standard")
    except (OSError, subprocess.CalledProcessError):
        print("  (git リポジトリ外のため追跡ファイルの自己チェックを省略)")
        return
    bad = [f for f in tracked if any(fnmatch.fnmatch(f, p) for p in FORBIDDEN_TRACKED)]
    if bad:
        sys.exit("✗ 公開してはいけないファイルが git に追跡されています:\n  " + "\n  ".join(bad)
                 + "\n  git rm --cached <file> で追跡を外し、.gitignore を確認してください。")
    risky = [f for f in untracked if any(fnmatch.fnmatch(f, p) for p in FORBIDDEN_TRACKED)]
    if risky:
        sys.exit("✗ .gitignore されていない要注意ファイルがあります（git add -A で公開されます）:\n  "
                 + "\n  ".join(risky))
    print("✓ git 自己チェック OK（xlsx・dist/ などは追跡されていません）")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refresh-distance", action="store_true", help="徒歩距離をすべて再計算する")
    ap.add_argument("--refresh-geo", action="store_true",
                    help="自動補完した座標（geo_source あり）を住所から取り直す")
    ap.add_argument("--offline", action="store_true", help="Nominatim / OSRM を呼ばない")
    args = ap.parse_args()

    try:
        data = load()
        net = geo.Net(enabled=not args.offline)
        updates = geo.update_distances(data["clinics"], data["origin"], net,
                                          args.refresh_distance, args.refresh_geo)
        if updates:
            geo.patch_yaml(DATA, updates)
            data = load()  # 書き戻した YAML を読み直して検証
            print(f"✓ data/clinics.yaml に座標・距離を書き戻し（{len(updates)}件）")
        missing = [c.name for c in data["clinics"] if not c.walk_m]
        if missing:
            print(f"  ※ 徒歩距離が未計算: {len(missing)}件（住所なし、または座標が取得できない）")

        README.write_text(render_readme(data), encoding="utf-8")
        print("✓ README.md")
        DIST.mkdir(exist_ok=True)
        (DIST / "card-a4.html").write_text(render_card(data), encoding="utf-8")
        print("✓ dist/card-a4.html")
        for p in write_maps(data):
            print(f"✓ {p}")
    except DataError as e:
        sys.exit(f"✗ {e}")

    git_self_check()


if __name__ == "__main__":
    main()
