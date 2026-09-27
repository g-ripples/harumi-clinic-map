"""座標の補完（Nominatim）と徒歩距離（OSRM / Haversine 推定）の計算、YAML への書き戻し。"""

from __future__ import annotations

import json
import math
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

USER_AGENT = "harumi-clinic-map/1.0 (+https://github.com/g-ripples/harumi-clinic-map)"
TIMEOUT = 10
DETOUR_FACTOR = 1.25  # 直線距離から徒歩距離を推定する係数

NOMINATIM = "https://nominatim.openstreetmap.org/search"
# 上から順に試す。router.project-osrm.org のデモは profile 指定に関わらず
# 車のルートを返すことがあるため、徒歩プロファイルを持つ FOSSGIS のサーバを先に試す。
OSRM_ROUTERS = [
    "https://routing.openstreetmap.de/routed-foot/route/v1/foot/{lon1},{lat1};{lon2},{lat2}?overview=false",
    "https://router.project-osrm.org/route/v1/foot/{lon1},{lat1};{lon2},{lat2}?overview=false",
]


class Net:
    """外部 API 呼び出し。1度でも接続に失敗したらその実行中は以後ネットワークを使わない。"""

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self._last_nominatim = 0.0
        self.failure: str | None = None

    def get_json(self, url: str):
        if not self.enabled:
            return None
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            print(f"  ! HTTP {e.code}: {url[:80]}")
            return None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            self.enabled = False
            self.failure = str(getattr(e, "reason", e))
            print(f"  ! ネットワークに接続できないため、以降の外部問い合わせを省略: {self.failure}")
            return None

    def nominatim(self, query: str):
        # 利用規約: 最大 1 req/s、識別可能な User-Agent
        wait = self._last_nominatim + 1.1 - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_nominatim = time.monotonic()
        url = NOMINATIM + "?" + urllib.parse.urlencode(
            {"q": query, "format": "jsonv2", "limit": 1, "countrycodes": "jp"})
        return self.get_json(url)


def _address_queries(address: str) -> list[tuple[str, bool]]:
    """住所 → (問い合わせ文字列, 近似か) のリスト。建物名を落とし、番地を後ろから削っていく。"""
    a = unicodedata.normalize("NFKC", address).replace("−", "-").replace("ー", "-")
    a = a.split(" ")[0]
    out = [(a, False)]
    while re.search(r"-\d+$", a):
        a = re.sub(r"-\d+$", "", a)
        out.append((a, True))
    m = re.match(r"(.*?\D)(\d+)$", a)  # 「勝どき3」→「勝どき3丁目」
    if m:
        out.append((f"{m[1]}{m[2]}丁目", True))
    return out


def geocode(net: Net, address: str):
    """(lat, lon, source) か None。"""
    for q, approx in _address_queries(address):
        if not net.enabled:
            return None
        res = net.nominatim(q)
        if res:
            return float(res[0]["lat"]), float(res[0]["lon"]), \
                ("nominatim-approx" if approx else "nominatim")
    return None


def haversine_m(lat1, lon1, lat2, lon2) -> float:
    r = 6371008.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def walk_distance(net: Net, origin, lat, lon):
    """(walk_m, source)。OSRM が使えなければ直線距離 × 1.25 の推定値。"""
    for tmpl in OSRM_ROUTERS:
        if not net.enabled:
            break
        res = net.get_json(tmpl.format(lon1=origin["lon"], lat1=origin["lat"], lon2=lon, lat2=lat))
        if res and res.get("code") == "Ok" and res.get("routes"):
            return round(res["routes"][0]["distance"]), "osrm"
    d = haversine_m(origin["lat"], origin["lon"], lat, lon) * DETOUR_FACTOR
    return round(d / 10) * 10, "estimated"


def update_distances(clinics, origin, net: Net, refresh: bool) -> dict[str, dict]:
    """書き戻すべきフィールドを {id: {key: value}} で返す。"""
    updates: dict[str, dict] = {}
    for c in clinics:
        upd: dict = {}
        lat, lon = c.lat, c.lon
        if (lat is None or lon is None) and c.address:
            if net.enabled:
                print(f"  座標を検索: {c.name}")
                g = geocode(net, c.address)
                if g:
                    lat, lon, src = g
                    upd.update(lat=round(lat, 7), lon=round(lon, 7), geo_source=src)
        if lat is not None and lon is not None and (refresh or c.walk_m is None):
            walk_m, src = walk_distance(net, origin, lat, lon)
            if walk_m != c.walk_m or src != c.walk_m_source:
                upd.update(walk_m=walk_m, walk_m_source=src)
        if upd:
            updates[c.id] = upd
            c.raw.update(upd)
    return updates


# ───────────────────────── YAML 書き戻し ─────────────────────────
# PyYAML で dump し直すとコメントや並びが崩れるので、該当行だけを書き換える。

FIELD_ORDER = ["lat", "lon", "geo_source", "walk_m", "walk_m_source"]


def _fmt(v) -> str:
    if isinstance(v, float):
        return f"{v:.7f}"
    return str(v)


def patch_yaml(path: Path, updates: dict[str, dict]) -> None:
    lines = path.read_text(encoding="utf-8").split("\n")
    for cid, fields in updates.items():
        start = next((i for i, l in enumerate(lines) if re.fullmatch(rf"  - id: {re.escape(cid)}\s*", l)), None)
        if start is None:
            raise RuntimeError(f"書き戻し先 {cid} が見つからない")
        end = start + 1
        while end < len(lines) and not re.match(r"  - id: |\S", lines[end]):
            end += 1
        block = lines[start:end]
        for key in FIELD_ORDER:
            if key not in fields:
                continue
            new = f"    {key}: {_fmt(fields[key])}"
            idx = next((i for i, l in enumerate(block) if l.startswith(f"    {key}:")), None)
            if idx is not None:
                block[idx] = new
                continue
            # 並び順に従って直前のフィールド（なければ address）の後ろに挿入
            anchor = None
            for prev in reversed(["address"] + FIELD_ORDER[:FIELD_ORDER.index(key)]):
                anchor = next((i for i, l in enumerate(block) if l.startswith(f"    {prev}:")), None)
                if anchor is not None:
                    break
            if anchor is None:
                anchor = 0
            block.insert(anchor + 1, new)
        lines[start:end] = block
    path.write_text("\n".join(lines), encoding="utf-8")
