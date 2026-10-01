#!/usr/bin/env python3
"""verified が 365 日より古い（または空欄＝未確認の）医院を一覧する。該当があれば終了コード 1。

    python scripts/check_stale.py
"""

import datetime as dt
import sys

from common import STALE_DAYS, DataError, load


def main() -> int:
    try:
        data = load()
    except DataError as e:
        print(f"✗ {e}", file=sys.stderr)
        return 2
    stale = sorted((c for c in data["clinics"] if c.stale), key=lambda c: c.verified or dt.date.min)
    if not stale:
        print(f"✓ すべて {STALE_DAYS} 日以内に確認済み（{len(data['clinics'])}件）")
        return 0
    print(f"要確認: {len(stale)}件（verified が {STALE_DAYS} 日より前、基準日 {data['today']}）")
    for c in stale:
        age = f"{c.age_days:>4}日前" if c.verified else "  未確認"
        print(f"  {c.verified_text:<10}  {age}  {c.id:<32} {c.name}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
