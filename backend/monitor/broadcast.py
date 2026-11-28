"""Mensajes hacia los navegadores conectados por WebSocket.

Todos los mensajes son JSON con un campo ``type``:
    snapshot  al conectarse: aeronaves, configuración pública y estado
    aircraft  en cada ciclo del monitor: lista completa de aeronaves y estado
    alert     una aeronave ingresó a la zona
    incident  comportamiento raro, avistamiento sin transponder o simulacro
    config    el administrador cambió la configuración
"""

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings

GROUP = "aircraft"
# Conexiones de visitantes (sin cuenta), para cerrarlas si se apaga el acceso.
GUEST_GROUP = "guests"


def user_group(user_id):
    return f"user-{user_id}"


def public_config(config):
    """Configuración que puede ver cualquier usuario registrado (sin el token)."""
    return {
        "zone": {
            "name": config.zone_name,
            "lat": config.zone_lat,
            "lon": config.zone_lon,
            "radius_nm": config.zone_radius_nm,
            "floor_m": config.zone_floor_m,
            "ceiling_m": config.zone_ceiling_m,
        },
        "poll_interval_s": config.poll_interval_s,
        "query_radius_nm": config.query_radius_nm,
        "telegram_enabled": config.telegram_enabled,
        "predict_minutes": getattr(config, "predict_minutes", 0),
        "behavior_alerts": getattr(config, "behavior_alerts", False),
        # Zona horaria del sistema (TZ del .env), para mostrar la hora local de la zona.
        "time_zone": settings.TIME_ZONE,
        "maintenance": config.maintenance_message,
        "updated_at": config.updated_at.isoformat() if config.updated_at else None,
    }


async def send(event):
    await get_channel_layer().group_send(GROUP, {"type": "monitor.event", "event": event})


async def send_aircraft(aircraft, status):
    await send({"type": "aircraft", "aircraft": aircraft, "status": status})


async def send_alert(alert):
    await send({"type": "alert", "alert": alert})


async def send_incident(incident):
    """Comportamiento raro, avistamiento o simulacro: va al registro de cada navegador."""
    await send({"type": "incident", "incident": incident})


def send_incident_sync(incident):
    async_to_sync(send_incident)(incident)


async def send_config(config):
    await send({"type": "config", "config": public_config(config)})


def send_config_sync(config):
    """Para las vistas (síncronas) que guardan la configuración."""
    async_to_sync(send_config)(config)


def disconnect_user_sync(user_id):
    """Cierra los WebSocket abiertos de un usuario (p. ej. al desactivarlo)."""
    async_to_sync(get_channel_layer().group_send)(user_group(user_id), {"type": "session.close"})


def disconnect_guests_sync():
    """Cierra los WebSocket de los visitantes (p. ej. al apagar el acceso de visitantes)."""
    async_to_sync(get_channel_layer().group_send)(GUEST_GROUP, {"type": "session.close"})
