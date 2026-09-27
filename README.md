# Harumi Clinic Map

Which clinics near Harumi are actually open — including Sundays,
public holidays, and consecutive-holiday periods, which is exactly
when the usual list stops being useful.

Covers the Harumi, Kachidoki and Tsukishima area of Chuo City, Tokyo.
Pediatrics, general internal medicine, after-hours emergency clinics,
and the municipal holiday clinic.

## Why this exists

Most clinic lists record weekday hours and leave holidays implicit.
The failure mode is predictable: you need a doctor on the third day
of a long weekend, and the one clinic you remember turns out to close
precisely on those days. This repository records holiday coverage as
a first-class field and flags the clinics that close during
consecutive holidays.

## What's in here

- `data/clinics.yaml` — the single source of truth. Hours use
  OpenStreetMap opening_hours syntax, where `PH` means public holiday.
- `README.md` — generated. Summary table of holiday coverage first,
  full listing by specialty below, sorted by walking distance.
- `dist/card-a4.html` — a printable A6 card, four-up on A4. Fold it
  into a first-aid kit or tape it inside a cupboard door.
- `dist/maps.csv` — importable into Google My Maps.

Distances are measured from a fixed reference point in the area, not
from any residence.

## Accuracy

Every entry carries its own `verified` date. Clinics change their
hours without announcing it, so treat anything older than a year as
unconfirmed — `scripts/check_stale.py` lists those. **Always call
ahead.** Nothing here is medical advice, and the authoritative source
is always the clinic's own website.

## Emergency numbers (Japan)

- `119` — ambulance and fire
- `#7119` — Tokyo emergency consultation (adults), 24h
- `#8000` — pediatric after-hours consultation

## License

Clinic data compiled from publicly available sources. Code under MIT;
the compiled data under CC BY 4.0.
