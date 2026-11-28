"""Avistamientos: ¿hay alguna aeronave con transponder donde apunta la cámara?

Son las mismas cuentas del Modo cielo (``frontend/src/sky.js``), del lado del
servidor: el navegador dice desde dónde se mira y hacia dónde apunta, y acá se
compara con lo que el monitor está viendo en ese momento. Si no hay nada en
esa dirección, lo que la persona ve vuela sin transponder (o no lo capta la
red de adsb.fi): se guarda como avistamiento.
"""

import math

from .zone import METERS_PER_FOOT

EARTH_R = 6371000.0
EFFECTIVE_R = EARTH_R * 4 / 3  # refracción estándar, como en sky.js
# Margen para considerar que "es ese": error de brújula del celular más el pulso.
MATCH_DEG = 12.0
MAX_LIFT = 8


def distance_bearing(lat1, lon1, lat2, lon2):
    """Distancia sobre la superficie (m) y rumbo inicial (grados desde el norte)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    dist = 2 * EARTH_R * math.asin(min(1.0, math.sqrt(a)))
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return dist, math.degrees(math.atan2(y, x)) % 360


def elevation_deg(dist, height_m, observer_alt_m=0.0, lift=1.0):
    """Grados sobre el horizonte. ``lift``: el "cielo levantado" del Modo cielo."""
    drop = dist * dist / (2 * EFFECTIVE_R)
    return math.degrees(math.atan2(height_m - observer_alt_m - drop, max(dist / lift, 1.0)))


def direction(azimuth, elevation):
    az, el = math.radians(azimuth), math.radians(elevation)
    return (math.cos(el) * math.sin(az), math.cos(el) * math.cos(az), math.sin(el))


def separation_deg(a, b):
    """Ángulo entre dos direcciones (azimut, elevación), en grados."""
    da, db = direction(*a), direction(*b)
    dot = sum(x * y for x, y in zip(da, db))
    return math.degrees(math.acos(max(-1.0, min(1.0, dot))))


def look_at(aircraft, observer, lift=1.0):
    """Dónde se ve una aeronave desde el observador: (distancia m, azimut, elevación) o None."""
    alt = aircraft.alt_baro
    if alt == "ground":
        height = observer["alt"]
    elif isinstance(alt, (int, float)) and not isinstance(alt, bool):
        height = alt * METERS_PER_FOOT
    else:
        return None
    dist, bearing = distance_bearing(observer["lat"], observer["lon"], aircraft.lat, aircraft.lon)
    return dist, bearing, elevation_deg(dist, height, observer["alt"], lift)


def candidates(aircraft_list, observer, azimuth, elevation, lift=1.0, limit=3):
    """Las aeronaves más cercanas a la dirección apuntada, de menor a mayor separación."""
    found = []
    for aircraft in aircraft_list:
        seen = look_at(aircraft, observer, lift)
        if seen is None:
            continue
        dist, az, el = seen
        found.append({
            "hex": aircraft.hex,
            "label": aircraft.label,
            "registration": aircraft.registration,
            "type": aircraft.type,
            "alt_ft": aircraft.alt_baro if aircraft.alt_baro != "ground" else 0,
            "distance_m": round(dist),
            "azimuth": round(az, 1),
            "elevation": round(el, 1),
            "separation": round(separation_deg((azimuth, elevation), (az, el)), 1),
        })
    found.sort(key=lambda item: item["separation"])
    return found[:limit]


def evaluate(aircraft_list, observer, azimuth, elevation, lift=1.0):
    """``{"match": aeronave o None, "candidates": [...], "threshold": grados}``."""
    nearest = candidates(aircraft_list, observer, azimuth, elevation, lift)
    match = nearest[0] if nearest and nearest[0]["separation"] <= MATCH_DEG else None
    return {"match": match, "candidates": nearest, "threshold": MATCH_DEG}
