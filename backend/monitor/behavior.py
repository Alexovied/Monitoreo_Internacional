"""Comportamiento raro: lo que le importa a un operador además del ingreso.

Cuatro señales, todas a partir de lo que ya informa adsb.fi:

    emergency       el transpondedor declara una emergencia (7500, 7600, 7700
                    o el estado de emergencia ADS-B)
    loiter          la aeronave da vueltas cerca de la zona en vez de pasar
    rapid_descent   baja mucho más rápido que un descenso normal
    signal_lost     estaba volando dentro de la zona y dejó de transmitir

Acá solo están las cuentas (funciones puras, fáciles de probar). El monitor
(``service``) guarda el recorrido de cada aeronave y decide cuándo avisar.
"""

from collections import deque

ENTRY = "entry"
EMERGENCY = "emergency"
LOITER = "loiter"
RAPID_DESCENT = "rapid_descent"
SIGNAL_LOST = "signal_lost"
SIGHTING = "sighting"

BEHAVIOR_KINDS = (EMERGENCY, LOITER, RAPID_DESCENT, SIGNAL_LOST)

KIND_TITLES = {
    ENTRY: "Ingreso a la zona",
    EMERGENCY: "Emergencia declarada",
    LOITER: "Da vueltas cerca de la zona",
    RAPID_DESCENT: "Descenso brusco",
    SIGNAL_LOST: "Dejó de transmitir dentro de la zona",
    SIGHTING: "Avistamiento sin transponder",
}

EMERGENCY_SQUAWKS = {
    "7500": "interferencia ilícita (secuestro)",
    "7600": "falla de comunicaciones",
    "7700": "emergencia general",
}
EMERGENCY_STATES = {
    "general": "emergencia general",
    "lifeguard": "emergencia médica",
    "minfuel": "combustible mínimo",
    "nordo": "falla de comunicaciones",
    "unlawful": "interferencia ilícita (secuestro)",
    "downed": "aeronave caída",
}

# Recorrido que se conserva de cada aeronave (para detectar y para la repetición).
HISTORY_S = 20 * 60
MAX_POINTS = 400

# Vueltas: giro acumulado (con signo) en la ventana. Un avión que pasa de largo
# casi no gira; uno que orbita suma 360° por vuelta. 540° = una vuelta y media.
LOITER_WINDOW_S = 12 * 60
LOITER_TURN_DEG = 540.0
LOITER_MIN_GS_KT = 40.0  # más lento, el rumbo informado no es confiable
LOITER_MAX_GAP_S = 90.0  # un hueco más largo corta la cuenta

# Descenso: los de línea bajan a 1.500-2.500 ft/min; más de 4.000 sostenido no es normal.
RAPID_DESCENT_FPM = -4000.0

# "Volando": para distinguir un transpondedor que se apaga de un aterrizaje.
LOST_MIN_GS_KT = 80.0
LOST_MAX_SINK_FPM = -500.0
# Y se la venía recibiendo bien: en zonas con poca cobertura las aeronaves
# aparecen y desaparecen todo el tiempo, y eso no es apagar el transpondedor.
LOST_STEADY_POINTS = 6
LOST_STEADY_GAP_S = 30.0


def watch_radius_nm(zone):
    """Hasta dónde se vigila el comportamiento alrededor de la zona."""
    return max(zone.radius_nm * 3, zone.radius_nm + 10)


def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def point_of(aircraft):
    """Punto del recorrido: [hora, lat, lon, altitud ft | "ground" | None, kt, rumbo]."""
    return [
        round(aircraft.timestamp, 1), round(aircraft.lat, 5), round(aircraft.lon, 5),
        aircraft.alt_baro, aircraft.gs, aircraft.track if aircraft.track is not None else aircraft.true_heading,
    ]


class Tracks:
    """Recorrido reciente de cada aeronave, en memoria."""

    def __init__(self, history_s=HISTORY_S, max_points=MAX_POINTS):
        self.history_s = history_s
        self.max_points = max_points
        self.points = {}  # hex -> deque de puntos
        self.rates = {}  # hex -> deque de las últimas velocidades verticales

    def record(self, aircraft):
        """Agrega la posición si es más nueva que la última guardada."""
        track = self.points.get(aircraft.hex)
        if track is None:
            track = self.points[aircraft.hex] = deque(maxlen=self.max_points)
            self.rates[aircraft.hex] = deque(maxlen=2)
        point = point_of(aircraft)
        if track and track[-1][0] >= point[0]:
            return False
        track.append(point)
        self.rates[aircraft.hex].append(aircraft.baro_rate)
        return True

    def get(self, hex_code):
        return list(self.points.get(hex_code, ()))

    def prune(self, now, keep=()):
        """Olvida lo viejo. ``keep``: aeronaves con un incidente todavía abierto."""
        for hex_code in list(self.points):
            track = self.points[hex_code]
            while track and now - track[0][0] > self.history_s:
                track.popleft()
            if not track and hex_code not in keep:
                del self.points[hex_code]
                self.rates.pop(hex_code, None)


def emergency_reason(aircraft):
    """Texto de la emergencia que declara la aeronave, o None."""
    if aircraft.squawk in EMERGENCY_SQUAWKS:
        return f"código {aircraft.squawk}: {EMERGENCY_SQUAWKS[aircraft.squawk]}"
    state = (aircraft.emergency or "").lower()
    if state in EMERGENCY_STATES:
        return EMERGENCY_STATES[state]
    return None


def turned_degrees(points, now, window_s=LOITER_WINDOW_S):
    """Giro acumulado con signo (grados; + horario) en los últimos ``window_s``."""
    total = 0.0
    previous = None
    for t, _lat, _lon, alt, gs, track in points:
        if now - t > window_s:
            continue
        if alt == "ground" or not is_number(track) or not is_number(gs) or gs < LOITER_MIN_GS_KT:
            previous = None
            continue
        if previous is not None and t - previous[0] <= LOITER_MAX_GAP_S:
            total += (track - previous[1] + 540) % 360 - 180
        previous = (t, track)
    return total


def is_rapid_descent(rates):
    """True si las dos últimas velocidades verticales son de descenso brusco."""
    rates = list(rates)
    return len(rates) >= 2 and all(is_number(r) and r <= RAPID_DESCENT_FPM for r in rates[-2:])


def detect(aircraft, points, rates, zone, now):
    """Señales activas de una aeronave que sigue transmitiendo: {tipo: detalle}."""
    found = {}
    reason = emergency_reason(aircraft)
    if reason:
        found[EMERGENCY] = {"reason": reason, "squawk": aircraft.squawk}
    if aircraft.alt_baro == "ground" or aircraft.dst > watch_radius_nm(zone):
        return found
    turn = turned_degrees(points, now)
    if abs(turn) >= LOITER_TURN_DEG:
        found[LOITER] = {
            "turns": round(abs(turn) / 360, 1),
            "direction": "horario" if turn > 0 else "antihorario",
            "minutes": round(min(LOITER_WINDOW_S, now - points[0][0]) / 60) if points else None,
        }
    if is_rapid_descent(rates):
        found[RAPID_DESCENT] = {"rate_fpm": round(list(rates)[-1])}
    return found


def received_steadily(points):
    """True si las últimas posiciones llegaron seguidas, sin huecos."""
    recent = list(points)[-LOST_STEADY_POINTS:]
    if len(recent) < LOST_STEADY_POINTS:
        return False
    return all(b[0] - a[0] <= LOST_STEADY_GAP_S for a, b in zip(recent, recent[1:]))


def lost_while_flying(aircraft, points):
    """True si se cortó de golpe la señal de una aeronave en vuelo (no aterrizando)."""
    if not received_steadily(points):
        return False
    if not is_number(aircraft.alt_baro):
        return False
    if not is_number(aircraft.gs) or aircraft.gs < LOST_MIN_GS_KT:
        return False
    return not (is_number(aircraft.baro_rate) and aircraft.baro_rate < LOST_MAX_SINK_FPM)


def summary(kind, detail, zone_name):
    """Una línea para el registro, el aviso de la web y Telegram."""
    detail = detail or {}
    if kind == ENTRY:
        return f"Ingresó a {zone_name}"
    if kind == EMERGENCY:
        return f"Declara emergencia ({detail.get('reason', 'sin detalle')})"
    if kind == LOITER:
        turns = str(detail.get("turns", "")).replace(".", ",")
        return f"Dio {turns} vueltas ({detail.get('direction', '')}) cerca de {zone_name}"
    if kind == RAPID_DESCENT:
        rate = f"{abs(detail.get('rate_fpm', 0)):,.0f}".replace(",", ".")
        return f"Baja a {rate} ft/min cerca de {zone_name}"
    if kind == SIGNAL_LOST:
        return f"Dejó de transmitir mientras volaba dentro de {zone_name}"
    return KIND_TITLES.get(kind, kind)
