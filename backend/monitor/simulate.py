"""Simulacros: incidentes de mentira para probar y mostrar el registro.

No pasan por el monitor ni por Telegram: se guardan marcados como simulacro,
con un recorrido inventado alrededor de la zona, y se difunden a los
navegadores como cualquier otro incidente.
"""

import math

from . import behavior

STEP_S = 5.0

# tipo -> (identificación, matrícula, tipo de aeronave, categoría, velocidad kt)
PROFILES = {
    behavior.ENTRY: ("SIM101", "LV-SIM", "C172", "A1", 110.0),
    behavior.LOITER: ("SIM202", "PR-SIM", "EC35", "A7", 90.0),
    behavior.EMERGENCY: ("SIM7700", "PH-SIM", "B738", "A3", 250.0),
    behavior.RAPID_DESCENT: ("SIM404", "PT-SIM", "E190", "A3", 280.0),
    behavior.SIGNAL_LOST: ("SIM505", "PP-SIM", "PA34", "A1", 150.0),
}


def _position(zone, east_nm, north_nm):
    lat = zone.lat + north_nm / 60
    lon = zone.lon + east_nm / 60 / max(math.cos(math.radians(zone.lat)), 0.01)
    return lat, lon


def _fly(path, speed_kt):
    """Recorre una lista de puntos (este, norte) en NM a velocidad constante: [(x, y, rumbo)] cada STEP_S."""
    step_nm = speed_kt * STEP_S / 3600
    out = []
    for (x1, y1), (x2, y2) in zip(path, path[1:]):
        length = math.hypot(x2 - x1, y2 - y1)
        heading = math.degrees(math.atan2(x2 - x1, y2 - y1)) % 360
        for i in range(max(1, round(length / step_nm))):
            f = i * step_nm / length if length else 0
            if f >= 1:
                break
            out.append((x1 + (x2 - x1) * f, y1 + (y2 - y1) * f, heading))
    return out


def _orbit(cx, cy, radius, start_deg, turns, points_per_turn=48):
    """Puntos de una órbita horaria alrededor de (cx, cy)."""
    return [
        (cx + radius * math.sin(math.radians(start_deg + i * 360 / points_per_turn)),
         cy + radius * math.cos(math.radians(start_deg + i * 360 / points_per_turn)))
        for i in range(int(turns * points_per_turn) + 1)
    ]


def build(kind, zone, now):
    """Devuelve ``(alert, detail, track, occurred_at)`` de un simulacro del tipo pedido."""
    if kind not in PROFILES:
        raise ValueError(f"Tipo de simulacro desconocido: {kind!r}")
    label, registration, aircraft_type, category, speed = PROFILES[kind]
    r = zone.radius_nm
    inside_alt = min(3000.0, zone.ceiling_ft * 0.6)
    if not zone.is_surface:
        inside_alt = max(inside_alt, (zone.floor_ft + zone.ceiling_ft) / 2)
    detail = {}
    event_index = None  # punto del recorrido donde "pasa" el incidente

    if kind == behavior.ENTRY:
        steps = _fly([(-2.6 * r, -1.2 * r), (0.2 * r, 0.1 * r), (1.8 * r, 1.4 * r)], speed)
        alts = [inside_alt] * len(steps)
        event_index = next(i for i, (x, y, _h) in enumerate(steps) if math.hypot(x, y) < r)
    elif kind == behavior.LOITER:
        orbit = _orbit(1.7 * r, 0.6 * r, 0.45 * r, 250, 2.2)
        steps = _fly([(3.2 * r, -1.5 * r), *orbit, (3.4 * r, 2.2 * r)], speed)
        alts = [inside_alt] * len(steps)
        detail = {"turns": 1.5, "direction": "horario", "minutes": 8}
        event_index = int(len(steps) * 0.62)
    elif kind == behavior.EMERGENCY:
        steps = _fly([(-4.0 * r, 3.0 * r), (-1.3 * r, 0.4 * r), (2.5 * r, -2.0 * r)], speed)
        alts = [max(inside_alt, 14000 - i * 45) for i in range(len(steps))]
        detail = {"reason": "código 7700: emergencia general", "squawk": "7700"}
        event_index = int(len(steps) * 0.35)
    elif kind == behavior.RAPID_DESCENT:
        steps = _fly([(-3.5 * r, -2.2 * r), (-0.4 * r, -1.4 * r), (3.0 * r, -0.6 * r)], speed)
        start = int(len(steps) * 0.45)
        alts = [18000.0 if i < start else max(2500.0, 18000 - (i - start) * 5200 * STEP_S / 60)
                for i in range(len(steps))]
        detail = {"rate_fpm": -5200}
        event_index = start + 3
    else:  # SIGNAL_LOST: entra y deja de verse adentro
        steps = _fly([(2.4 * r, 2.0 * r), (0.3 * r, 0.25 * r)], speed)
        alts = [inside_alt] * len(steps)
        event_index = len(steps) - 1
        detail = {}

    start_time = now - (len(steps) - 1) * STEP_S
    track = []
    for i, ((x, y, heading), alt) in enumerate(zip(steps, alts)):
        lat, lon = _position(zone, x, y)
        track.append([round(start_time + i * STEP_S, 1), round(lat, 5), round(lon, 5), round(alt), speed, round(heading, 1)])
    occurred_at = track[event_index][0]
    if kind == behavior.SIGNAL_LOST:
        detail = {"last_seen": occurred_at}

    _t, lat, lon, alt, gs, heading = track[event_index]
    x, y, _h = steps[event_index]
    alert = {
        "hex": "", "label": label, "flight": label, "registration": registration, "type": aircraft_type,
        "category": category, "alt_ft": alt, "alt_m": alt * 0.3048, "gs": gs, "track": heading,
        "true_heading": None, "dst": round(math.hypot(x, y), 2), "lat": lat, "lon": lon,
        "zone": zone.name, "time": occurred_at, "kind": kind, "title": behavior.KIND_TITLES[kind],
        "summary": behavior.summary(kind, detail, zone.name), "detail": detail,
    }
    return alert, detail, track, occurred_at
