"""clinics.yaml の読み込み・検証と、opening_hours 最小パーサ。"""

from __future__ import annotations

import datetime as dt
import math
import re
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "clinics.yaml"
CARD = ROOT / "data" / "card.yaml"

STALE_DAYS = 365
WALK_M_PER_MIN = 80  # 不動産の表示規約に倣い 80m = 徒歩1分

DAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
ALL_DAYS = DAYS + ["PH"]
DAY_JA = {"Mo": "月", "Tu": "火", "We": "水", "Th": "木", "Fr": "金",
          "Sa": "土", "Su": "日", "PH": "祝"}

# README の全件リストの科グループ（clinic の 科 の先頭要素で振り分け）
GROUPS = [
    ("小児科", "小児科"),
    ("内科", "内科（大人）"),
    ("外科", "外科・整形外科"),
    ("皮膚科", "皮膚科"),
    ("耳鼻咽喉科", "耳鼻咽喉科"),
]


class DataError(Exception):
    pass


# ───────────────────────── opening_hours ─────────────────────────

@dataclass
class DayHours:
    intervals: list[tuple[int, int]]  # 分単位 (start, end)。空 = 休診
    comment: str | None = None

    @property
    def open(self) -> bool:
        return bool(self.intervals)

    def key(self):
        return (tuple(self.intervals), self.comment)


_RULE = re.compile(r'^(?P<days>[A-Za-z,\-]+)\s+(?P<times>[^"]+?)\s*(?:"(?P<comment>[^"]*)")?$')
_TIME = re.compile(r"^(\d{2}):(\d{2})-(\d{2}):(\d{2})$")


def _parse_days(sel: str, src: str) -> list[str]:
    out: list[str] = []
    for tok in sel.split(","):
        if tok == "PH":
            out.append("PH")
        elif "-" in tok:
            a, _, b = tok.partition("-")
            if a not in DAYS or b not in DAYS or DAYS.index(a) > DAYS.index(b):
                raise DataError(f"曜日範囲 {tok!r} を解釈できない: {src!r}")
            out.extend(DAYS[DAYS.index(a):DAYS.index(b) + 1])
        elif tok in DAYS:
            out.append(tok)
        else:
            raise DataError(f"曜日 {tok!r} を解釈できない: {src!r}")
    return out


def _parse_times(times: str, src: str) -> list[tuple[int, int]]:
    if times == "off":
        return []
    out = []
    for part in times.split(","):
        m = _TIME.match(part.strip())
        if not m:
            raise DataError(f"時間帯 {part!r} を解釈できない: {src!r}")
        h1, m1, h2, m2 = map(int, m.groups())
        s, e = h1 * 60 + m1, h2 * 60 + m2
        if m1 >= 60 or m2 >= 60 or not (0 <= s < e <= 24 * 60):
            raise DataError(f"時間帯 {part!r} が不正: {src!r}")
        if out and s < out[-1][1]:
            raise DataError(f"時間帯が重複または逆順: {src!r}")
        out.append((s, e))
    return out


def parse_hours(src: str) -> dict[str, DayHours]:
    """最小限の opening_hours パーサ。記載のない曜日はキー自体が無い（=不明）。"""
    if not isinstance(src, str) or not src.strip():
        raise DataError("hours が空")
    result: dict[str, DayHours] = {}
    for rule in src.split(";"):
        rule = rule.strip()
        if not rule:
            continue
        m = _RULE.match(rule)
        if not m:
            raise DataError(f"ルール {rule!r} を解釈できない: {src!r}")
        days = _parse_days(m["days"], src)
        intervals = _parse_times(m["times"].strip(), src)
        for d in days:
            result[d] = DayHours(intervals, m["comment"])
    return result


def fmt_min(m: int) -> str:
    return f"{m // 60}:{m % 60:02d}"


def fmt_day(h: DayHours | None, sep: str = ", ") -> str:
    if h is None:
        return "不明"
    if not h.open:
        text = "休診"
    elif h.intervals == [(0, 24 * 60)]:
        text = "24時間"
    else:
        text = sep.join(f"{fmt_min(s)}-{fmt_min(e)}" for s, e in h.intervals)
    if h.comment:
        text += f"（{h.comment}）"
    return text


def day_label(days: list[str]) -> str:
    s = set(days)
    named = [
        ({"Mo", "Tu", "We", "Th", "Fr", "Sa", "Su", "PH"}, "毎日"),
        ({"Mo", "Tu", "We", "Th", "Fr"}, "平日"),
        ({"Sa", "Su", "PH"}, "土日祝"),
        ({"Su", "PH"}, "日祝"),
        ({"Sa", "Su"}, "土日"),
    ]
    for group, label in named:
        if s == group:
            return label
    # 平日が丸ごと含まれていれば「平日・土」のようにまとめる
    for group, label in named[1:2]:
        if group <= s:
            rest = [d for d in days if d not in group]
            return label + "・" + "".join(DAY_JA[d] for d in rest)
    return "・".join(DAY_JA[d] for d in days)


def group_days(hours: dict[str, DayHours], days: list[str]) -> list[tuple[str, str]]:
    """同じ時間の曜日をまとめて [(ラベル, 時間文字列)] を返す。"""
    buckets: dict = {}
    order = []
    for d in days:
        h = hours.get(d)
        k = h.key() if h else None
        if k not in buckets:
            buckets[k] = []
            order.append(k)
        buckets[k].append(d)
    return [(day_label(buckets[k]), fmt_day(hours.get(buckets[k][0]))) for k in order]


# ───────────────────────── 読み込み ─────────────────────────

@dataclass
class Clinic:
    raw: dict
    hours: dict[str, DayHours]
    today: dt.date
    extra: dict = field(default_factory=dict)

    def __getattr__(self, name):
        raw = self.__dict__.get("raw", {})
        if name in raw:
            return raw[name]
        if name in ("tel", "address", "nearest", "holiday_note", "note", "url",
                    "lat", "lon", "walk_m", "walk_m_source", "tel_alt", "last_entry"):
            return None
        raise AttributeError(name)

    @property
    def depts(self) -> list[str]:
        return self.raw["科"]

    @property
    def group(self) -> str:
        return self.depts[0]

    @property
    def verified(self) -> dt.date:
        return self.raw["verified"]

    @property
    def age_days(self) -> int:
        return (self.today - self.verified).days

    @property
    def stale(self) -> bool:
        return self.age_days > STALE_DAYS

    @property
    def open_holiday(self) -> bool:
        return bool(self.hours.get("PH") and self.hours["PH"].open)

    @property
    def open_sunday(self) -> bool:
        return bool(self.hours.get("Su") and self.hours["Su"].open)

    @property
    def renkyu_trap(self) -> bool:
        n = self.holiday_note or ""
        return "連休" in n and "休診" in n

    @property
    def walk_min(self) -> int | None:
        return math.ceil(self.walk_m / WALK_M_PER_MIN) if self.walk_m else None

    @property
    def distance(self) -> str:
        """例: '850m / 徒歩11分'。推定値は数値の後ろに * を付ける。"""
        if not self.walk_m:
            return "—"
        star = "*" if self.walk_m_source == "estimated" else ""
        return f"{self.walk_m:,}m{star} / 徒歩{self.walk_min}分"

    @property
    def sort_key(self):
        return (self.walk_m is None, self.walk_m or 0)

    @property
    def tel_href(self) -> str | None:
        return tel_href(self.tel) if self.tel else None

    @property
    def map_url(self) -> str | None:
        if not self.address:
            return None
        return ("https://www.google.com/maps/search/?api=1&query="
                + urllib.parse.quote(self.address, safe=""))


def tel_href(tel: str) -> str:
    if tel.startswith("#"):
        return "tel:" + urllib.parse.quote(tel)
    return "tel:" + re.sub(r"\D", "", tel)


REQUIRED = ["id", "name", "科", "hours", "tags", "verified", "source"]
_TEL = re.compile(r"^(0\d{1,4}-\d{1,4}-\d{3,4}|0120-\d{2,3}-\d{3})$")


def load(today: dt.date | None = None, path: Path = DATA) -> dict:
    today = today or dt.date.today()
    with open(path, encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    if not isinstance(doc, dict) or "origin" not in doc or "clinics" not in doc:
        raise DataError(f"{path}: トップレベルに origin と clinics が必要")

    errors: list[str] = []
    clinics: list[Clinic] = []
    seen: set[str] = set()
    for i, raw in enumerate(doc["clinics"]):
        cid = raw.get("id", f"#{i}")
        missing = [k for k in REQUIRED if not raw.get(k)]
        if missing:
            errors.append(f"{cid}: 必須項目がない: {', '.join(missing)}")
            continue
        if cid in seen:
            errors.append(f"{cid}: id が重複")
        seen.add(cid)
        if not isinstance(raw["verified"], dt.date):
            errors.append(f"{cid}: verified は YYYY-MM-DD で書く")
            continue
        for t in [raw.get("tel")] + [a.get("tel") for a in raw.get("tel_alt") or []]:
            if t is not None and not _TEL.match(str(t)):
                errors.append(f"{cid}: 電話番号 {t!r} の形式が不正（市外局番から 03-1234-5678 の形で）")
        try:
            hours = parse_hours(raw["hours"])
        except DataError as e:
            errors.append(f"{cid}: {e}")
            continue
        c = Clinic(raw, hours, today)
        if ("祝日可" in raw["tags"]) != c.open_holiday:
            errors.append(f"{cid}: tags の「祝日可」と hours の PH が一致しない")
        if c.group not in {g for g, _ in GROUPS}:
            errors.append(f"{cid}: 科の先頭 {c.group!r} が README のグループにない")
        clinics.append(c)

    if errors:
        raise DataError("clinics.yaml の検証に失敗:\n  " + "\n  ".join(errors))
    return {"origin": doc["origin"], "clinics": clinics, "today": today}
