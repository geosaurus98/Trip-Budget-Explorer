from trip_explorer.mapping import build_route_map, mapped_stops
from trip_explorer.models import Destination


def stop(name, lat=None, lon=None):
    return Destination(trip_id=1, name=name, latitude=lat, longitude=lon)


def test_no_coordinates_gives_no_map():
    assert build_route_map([]) is None
    assert build_route_map([stop("A"), stop("B", lat=1.0)]) is None


def test_missing_coordinates_are_skipped_but_numbering_is_kept():
    stops = [stop("A", -8.5, 115.2), stop("B"), stop("C", 1.35, 103.8)]
    assert [(n, d.name) for n, d in mapped_stops(stops)] == [(1, "A"), (3, "C")]
    html = build_route_map(stops).get_root().render()
    assert "3. C" in html and "L.polyline" in html


def test_single_stop_map_has_no_line():
    html = build_route_map([stop("A", -8.5, 115.2)]).get_root().render()
    assert "L.polyline" not in html
