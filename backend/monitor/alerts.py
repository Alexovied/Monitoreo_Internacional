"""Contenido de una alerta de ingreso a la zona (Telegram y web)."""

import re
from datetime import datetime
from html import escape
from ipaddress import ip_address
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo

from django.conf import settings

from .zone import feet_to_meters

ARGENTINA_TZ = "America/Argentina/Buenos_Aires"


def alert_clock(epoch):
    """Hora de la zona; si la zona no está en Argentina, también la hora argentina."""
    def hhmmss(tz):
        return datetime.fromtimestamp(epoch, ZoneInfo(tz)).strftime("%H:%M:%S")

    if settings.TIME_ZONE == ARGENTINA_TZ:
        return hhmmss(ARGENTINA_TZ)
    return f"{hhmmss(settings.TIME_ZONE)} (hora local) · {hhmmss(ARGENTINA_TZ)} en Argentina"


def build_alert(aircraft, zone):
    """Datos de la alerta. Se envían tal cual al navegador."""
    alt_ft = aircraft.alt_baro if isinstance(aircraft.alt_baro, (int, float)) else None
    return {
        "hex": aircraft.hex,
        "label": aircraft.label,
        "flight": aircraft.flight,
        "registration": aircraft.registration,
        "type": aircraft.type,
        "alt_ft": alt_ft,
        "alt_m": feet_to_meters(alt_ft) if alt_ft is not None else None,
        "gs": aircraft.gs,
        "track": aircraft.track,
        "true_heading": aircraft.true_heading,
        "dst": aircraft.dst,
        "lat": aircraft.lat,
        "lon": aircraft.lon,
        "zone": zone.name,
        "time": aircraft.timestamp,
    }


FR24_URL = "https://www.flightradar24.com"
_CALLSIGN = re.compile(r"^[A-Z0-9]{2,8}$")


def flightradar_url(alert):
    """Enlace a Flightradar24: vuelo, ficha de aeronave o mapa en su posición."""
    flight = (alert.get("flight") or "").upper()
    if _CALLSIGN.match(flight):
        return f"{FR24_URL}/{flight}"
    if alert.get("registration"):
        return f"{FR24_URL}/data/aircraft/{alert['registration'].lower()}"
    return f"{FR24_URL}/{alert['lat']:.2f},{alert['lon']:.2f}/12"


def _is_local_host(host):
    if host == "localhost" or host.endswith(".local"):
        return True
    try:
        return ip_address(host).is_private or ip_address(host).is_loopback
    except ValueError:
        return False


def public_base_url(raw):
    """SITE_URL listo para enlazar: sin barra final y siempre https.

    La web pública se sirve por HTTPS (Tailscale, puerto 8443): un http:// en el
    .env deja el enlace de Telegram roto, así que se corrige acá. Solo se respeta
    http:// para direcciones locales (localhost o IP de la red), útiles al probar.
    """
    url = (raw or "").strip().rstrip("/")
    if url.lower().startswith("http://") and not _is_local_host(urlsplit(url).hostname or ""):
        url = "https://" + url[len("http://"):]
    return url


def site_url(alert):
    """Enlace a nuestra web con la aeronave seleccionada, o None si no hay SITE_URL."""
    base = public_base_url(settings.SITE_URL)
    if not base:
        return None
    return f"{base}/{quote(str(alert['hex']))}"


def _integer(value):
    return f"{value:,.0f}".replace(",", ".")


def _decimal(value, digits=1):
    return f"{value:.{digits}f}".replace(".", ",")


_POINTS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSO", "SO", "OSO", "O", "ONO", "NO", "NNO"]


def _compass(deg):
    return _POINTS[round(deg / 22.5) % 16]


def format_alert_message(alert):
    """Genera el mensaje HTML de una alerta para Telegram (va como pie de la foto: ≤ 1024 caracteres)."""
    title = escape(str(alert["label"] or alert["hex"]).upper())
    details = escape(" · ".join(
        str(part) for part in (alert["registration"], alert["type"]) if part and str(part).upper() != title
    ))

    def value(main, extra=""):
        return f"<b>{main}</b> {extra}".rstrip()

    altitude = (
        value(f"{_integer(alert['alt_ft'])} ft", f"({_integer(alert['alt_m'])} m)")
        if alert["alt_ft"] is not None else "sin dato"
    )
    speed = (
        value(f"{_integer(alert['gs'])} kt", f"({_integer(alert['gs'] * 1.852)} km/h)")
        if alert["gs"] is not None else "sin dato"
    )
    track = (
        value(f"{alert['track']:03.0f}°", _compass(alert["track"]))
        if alert["track"] is not None else "sin dato"
    )
    distance = (
        value(f"{_decimal(alert['dst'])} NM", f"({_decimal(alert['dst'] * 1.852)} km)")
        if alert["dst"] is not None else "sin dato"
    )

    links = [f'<a href="{escape(flightradar_url(alert), quote=True)}">Flightradar24</a>']
    ours = site_url(alert)
    if ours:
        links.insert(0, f'<a href="{escape(ours, quote=True)}">Ver en nuestro mapa</a>')

    if alert.get("eta_min") is not None:
        # Distinto desde la primera palabra: es lo que muestra la vista previa de Telegram.
        header = ("<b>PREAVISO: TODAVÍA NO ENTRÓ</b>\n"
                  f"<i>Podría entrar en ~{max(1, round(alert['eta_min']))} min si mantiene rumbo y velocidad</i>")
    else:
        header = "<b>INGRESO A ZONA DE ALERTA</b>"
    return "\n".join([
        header,
        f"Zona: {escape(str(alert['zone']))}",
        "",
        f"<b>{title}</b>" + (f"\n<i>{details}</i>" if details else ""),
        "",
        "<blockquote>"
        f"Altitud: {altitude}\n"
        f"Velocidad: {speed}\n"
        f"Rumbo: {track}\n"
        f"Distancia al centro: {distance}"
        "</blockquote>",
        f"Hora: {alert_clock(alert['time'])}",
        "",
        "   ·   ".join(links),
    ])
