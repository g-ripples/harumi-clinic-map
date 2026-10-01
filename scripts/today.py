#!/usr/bin/env python3
"""指定日（既定: 今日 JST）に開いている医院の一覧を A4 印刷用 HTML で出力する。

    python scripts/today.py                      # 今日（日本時間）
    python scripts/today.py --date 2026-11-03    # 日付を指定
    python scripts/today.py --summary out.md     # Markdown 版も出力（Actions のジョブサマリー用）

平日 / 土曜 / 日曜 / 祝日 を判定し、その日の hours で開いている医院を近い順に並べる。
土日祝が3日以上続く「連休」中は、holiday_note に「連休は休診」とある医院を除外する。
special_days（年末年始など）に当たる日は、通常の曜日の時間よりそちらを優先する。
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from zoneinfo import ZoneInfo

import jpholiday

from build import env
from common import DAY_JA, DAYS, ROOT, DataError, fmt_day, load

DIST = ROOT / "dist"


def is_day_off(d: dt.date) -> bool:
    return d.weekday() >= 5 or jpholiday.is_holiday(d)


def renkyu_span(d: dt.date) -> tuple[dt.date, dt.date] | None:
    """d を含む土日祝の連続が3日以上なら (初日, 最終日)。"""
    if not is_day_off(d):
        return None
    start = end = d
    while is_day_off(start - dt.timedelta(days=1)):
        start -= dt.timedelta(days=1)
    while is_day_off(end + dt.timedelta(days=1)):
        end += dt.timedelta(days=1)
    return (start, end) if (end - start).days >= 2 else None


def classify(d: dt.date) -> dict:
    holiday = jpholiday.is_holiday_name(d)
    weekday = DAYS[d.weekday()]
    key = "PH" if holiday else weekday
    if holiday:
        kind = f"祝日（{holiday}）"
    elif weekday == "Sa":
        kind = "土曜日"
    elif weekday == "Su":
        kind = "日曜日"
    else:
        kind = "平日"
    return {
        "date": d, "key": key, "kind": kind, "holiday": holiday,
        "label": f"{d.year}年{d.month}月{d.day}日（{DAY_JA[weekday]}）",
        "renkyu": renkyu_span(d),
        "nenmatsu": (d.month, d.day) >= (12, 29) or (d.month, d.day) <= (1, 3),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", default="", help="YYYY-MM-DD（空なら今日 JST）")
    ap.add_argument("--summary", help="Markdown 版の出力先（追記）")
    args = ap.parse_args()

    target = (dt.date.fromisoformat(args.date) if args.date
              else dt.datetime.now(ZoneInfo("Asia/Tokyo")).date())
    day = classify(target)
    try:
        data = load(today=target)
    except DataError as e:
        print(f"✗ {e}", file=sys.stderr)
        return 1

    open_, unknown, renkyu_closed, special_closed = [], [], [], []
    for c in data["clinics"]:
        h, special = c.hours_on(target, day["key"])
        if h is None:
            unknown.append(c)
        elif special and not h.open:
            special_closed.append((c, special.label))
        elif h.open:
            if day["renkyu"] and c.renkyu_trap:
                renkyu_closed.append(c)
            else:
                open_.append((c, fmt_day(h, "<br>")))
    open_.sort(key=lambda t: t[0].sort_key)
    unknown.sort(key=lambda c: c.sort_key)

    ctx = dict(day=day, open=open_, unknown=unknown, renkyu_closed=renkyu_closed,
               special_closed=special_closed,
               origin=data["origin"], total=len(data["clinics"]))
    DIST.mkdir(exist_ok=True)
    out = DIST / f"today-{target}.html"
    out.write_text(env(True).get_template("today-a4.html.j2").render(**ctx), encoding="utf-8")
    print(f"✓ {out.relative_to(ROOT)}  {day['label']} {day['kind']}"
          f"{' 連休中' if day['renkyu'] else ''}: 開いている {len(open_)}件 / 不明 {len(unknown)}件")

    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as f:
            f.write(env(False).get_template("today.md.j2").render(**ctx))

    if gh := os.environ.get("GITHUB_OUTPUT"):
        with open(gh, "a", encoding="utf-8") as f:
            f.write(f"date={target}\nhtml={out.relative_to(ROOT)}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
