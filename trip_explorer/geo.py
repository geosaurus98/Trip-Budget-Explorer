"""Parse coordinates pasted from a map app or website. No online lookups.

Accepted examples:
    -8.5069, 115.2625
    -8.5069 115.2625
    8.5069° S, 115.2625° E        (hemisphere letters, any order: "115.26 E 8.5 S" works too)
    https://www.google.com/maps/place/Ubud/@-8.5069,115.2625,14z
    https://www.openstreetmap.org/?mlat=-8.5069&mlon=115.2625
"""

from __future__ import annotations

import re

_NUMBER = re.compile(r"([-+]?\d+(?:\.\d+)?)\s*°?\s*([NSEW])?(?![\w.])", re.IGNORECASE)


def parse_lat_lon(text: str) -> tuple[float, float]:
    """Return (latitude, longitude) in decimal degrees. Raises ValueError with a helpful message."""
    t = (text or "").strip()
    if not t:
        raise ValueError("Paste coordinates such as '-8.5069, 115.2625'")
    if "@" in t:  # map URLs put the centre after "@": .../@lat,lon,zoom
        t = t.split("@", 1)[1]
    elif "mlat=" in t and "mlon=" in t:  # OpenStreetMap marker links
        lat = re.search(r"mlat=([-+]?\d+(?:\.\d+)?)", t)
        lon = re.search(r"mlon=([-+]?\d+(?:\.\d+)?)", t)
        if lat and lon:
            t = f"{lat.group(1)}, {lon.group(1)}"
    elif "q=" in t:
        t = t.split("q=", 1)[1]

    found = _NUMBER.findall(t)[:2]
    if len(found) < 2:
        raise ValueError("Couldn't find two numbers (latitude and longitude) in what you pasted")
    (a, a_hemi), (b, b_hemi) = found
    a_hemi, b_hemi = a_hemi.upper(), b_hemi.upper()
    if a_hemi in ("E", "W") or b_hemi in ("N", "S"):  # written longitude first
        (a, a_hemi), (b, b_hemi) = (b, b_hemi), (a, a_hemi)

    lat, lon = float(a), float(b)
    if a_hemi == "S":
        lat = -abs(lat)
    elif a_hemi == "N":
        lat = abs(lat)
    if b_hemi == "W":
        lon = -abs(lon)
    elif b_hemi == "E":
        lon = abs(lon)

    if not -90 <= lat <= 90:
        raise ValueError(f"Latitude {lat} is outside −90 to 90 — are latitude and longitude swapped?")
    if not -180 <= lon <= 180:
        raise ValueError(f"Longitude {lon} is outside −180 to 180")
    return lat, lon
