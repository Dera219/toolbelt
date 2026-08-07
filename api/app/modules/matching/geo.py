"""Haversine geo helpers for the MVP. Swapped for PostGIS ST_DWithin at scale
(ARCHITECTURE.md §4) — callers only depend on this module's interface."""

import math

EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def bounding_box(lat: float, lng: float, radius_km: float) -> tuple[float, float, float, float]:
    """(min_lat, max_lat, min_lng, max_lng) prefilter box for an indexed SQL query.
    Slightly over-inclusive by design; exact filtering is done with haversine_km."""
    dlat = math.degrees(radius_km / EARTH_RADIUS_KM)
    # Clamp cos to avoid division blowup near the poles.
    cos_lat = max(math.cos(math.radians(lat)), 1e-6)
    dlng = math.degrees(radius_km / (EARTH_RADIUS_KM * cos_lat))
    return (
        max(lat - dlat, -90.0),
        min(lat + dlat, 90.0),
        max(lng - dlng, -180.0),
        min(lng + dlng, 180.0),
    )
