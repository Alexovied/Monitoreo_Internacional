"""Aeronaves que no generan alerta (p. ej. los aviones propios de un aeródromo).

Cada aeronave se indica por su código ICAO de 24 bits (el ``hex`` de adsb.fi,
6 caracteres hexadecimales, p. ej. ``E0B390``) o por su matrícula (p. ej.
``PR-ABC`` o ``PH-ABC``). Al comparar no importan las mayúsculas, los espacios
ni los guiones: ``pr-abc``, ``PRABC`` y ``PR-ABC`` son la misma.

Estas aeronaves se siguen viendo en el mapa (marcadas como "sin alerta"), pero
al entrar en la zona no suenan en la web ni se mandan a Telegram.
"""

import re

MAX_EXCLUDED = 200

# adsb.fi antepone "~" a las direcciones que no son ICAO (TIS-B/ADS-R).
HEX_RE = re.compile(r"^~?[0-9A-F]{6}$")
REGISTRATION_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]{1,9}$")


def normalize(value):
    """Forma en que se guarda: sin espacios y en mayúsculas."""
    return re.sub(r"\s+", "", str(value)).upper()


def is_valid(value):
    value = normalize(value)
    return bool(HEX_RE.match(value) or REGISTRATION_RE.match(value))


def _key(value):
    return normalize(value).replace("-", "")


def excluded_keys(identifiers):
    """Conjunto para comparar rápido en cada ciclo del monitor."""
    return frozenset(_key(i) for i in identifiers or () if str(i).strip())


def is_excluded(aircraft, keys):
    if not keys:
        return False
    if _key(aircraft.hex) in keys:
        return True
    return bool(aircraft.registration) and _key(aircraft.registration) in keys


def parse_list(text):
    """Lista desde un texto separado por comas, espacios o renglones (para el .env)."""
    return [normalize(part) for part in re.split(r"[,;\s]+", text or "") if part]
