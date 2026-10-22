"""Folium route map. Straight lines join stops in itinerary order — not real routes."""

from __future__ import annotations

import folium

from .models import Destination


def mapped_stops(destinations: list[Destination]) -> list[tuple[int, Destination]]:
    """(itinerary number, stop) for stops that have both coordinates."""
    return [
        (i, d) for i, d in enumerate(destinations, start=1)
        if d.latitude is not None and d.longitude is not None
    ]


def build_route_map(destinations: list[Destination]) -> folium.Map | None:
    """Returns None when no stop has coordinates."""
    points = mapped_stops(destinations)
    if not points:
        return None
    coords = [(d.latitude, d.longitude) for _, d in points]
    fmap = folium.Map(location=coords[0], zoom_start=5, tiles="OpenStreetMap")
    for number, dest in points:
        nights = ""
        if dest.arrival and dest.departure:
            nights = f"<br>{dest.arrival:%d %b} – {dest.departure:%d %b}"
        folium.Marker(
            location=(dest.latitude, dest.longitude),
            tooltip=f"{number}. {dest.name}",
            popup=folium.Popup(f"<b>{number}. {dest.name}</b><br>{dest.country}{nights}", max_width=250),
            icon=folium.DivIcon(
                html=(
                    '<div style="background:#1f6feb;color:white;border-radius:50%;width:24px;height:24px;'
                    'text-align:center;line-height:24px;font-weight:bold;font-size:12px;'
                    f'border:2px solid white;box-shadow:0 0 3px #333;">{number}</div>'
                ),
                icon_size=(24, 24),
                icon_anchor=(12, 12),
            ),
        ).add_to(fmap)
    if len(coords) > 1:
        folium.PolyLine(coords, weight=3, color="#1f6feb", opacity=0.7, dash_array="6 6",
                        tooltip="Itinerary order (straight lines, not actual routes)").add_to(fmap)
        fmap.fit_bounds([[min(c[0] for c in coords), min(c[1] for c in coords)],
                         [max(c[0] for c in coords), max(c[1] for c in coords)]], padding=(20, 20))
    return fmap
