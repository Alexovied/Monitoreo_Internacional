"""Registro de incidentes en la base de datos.

El monitor corre en el event loop del servidor: escribe a través de
``DbRecorder`` (asíncrono). Las vistas usan las funciones síncronas.
"""

import logging

from asgiref.sync import sync_to_async

from . import behavior
from .models import Incident

logger = logging.getLogger(__name__)

# Se conservan los últimos; lo más viejo se borra solo (la base vive en una Raspberry Pi).
MAX_INCIDENTS = 500


def zone_dict(zone):
    return {"name": zone.name, "lat": zone.lat, "lon": zone.lon, "radius_nm": zone.radius_nm,
            "floor_m": zone.floor_m, "ceiling_m": zone.ceiling_m}


def create_incident(kind, zone, *, occurred_at, alert=None, detail=None, track=(), summary=None,
                    simulated=False, photo=None, closed=False):
    """Guarda un incidente y borra los más viejos si se pasó del máximo."""
    alert = alert or {}
    incident = Incident.objects.create(
        kind=kind,
        occurred_at=occurred_at,
        hex=str(alert.get("hex") or "")[:10],
        label=str(alert.get("label") or "")[:40],
        registration=str(alert.get("registration") or "")[:20],
        aircraft_type=str(alert.get("type") or "")[:10],
        category=str(alert.get("category") or "")[:4],
        summary=(summary or behavior.summary(kind, detail, zone.name))[:250],
        zone=zone_dict(zone),
        data={**alert, "detail": detail or {}},
        track=list(track),
        closed=closed,
        simulated=simulated,
        photo=photo,
    )
    prune()
    return incident


def prune(maximum=MAX_INCIDENTS):
    stale = list(Incident.objects.values_list("pk", flat=True)[maximum:])
    if stale:
        Incident.objects.filter(pk__in=stale).delete()


def save_track(incident_id, track, closed):
    changes = {"closed": closed}
    if track:  # sin puntos nuevos se conserva el recorrido guardado
        changes["track"] = list(track)
        changes["points"] = len(changes["track"])
    Incident.objects.filter(pk=incident_id).update(**changes)


def close_open_incidents():
    """Al arrancar: lo que quedó abierto de la ejecución anterior ya no va a crecer."""
    return Incident.objects.filter(closed=False).update(closed=True)


class DbRecorder:
    """Lo que usa el monitor. Nunca lanza: un fallo de la base no frena las alertas."""

    async def open(self, kind, zone, *, occurred_at, alert, detail, track):
        try:
            incident = await sync_to_async(create_incident)(
                kind, zone, occurred_at=occurred_at, alert=alert, detail=detail, track=track)
            return incident.to_summary()
        except Exception:
            logger.exception("No se pudo guardar el incidente de %s en el registro.", alert.get("label"))
            return None

    async def update(self, incident_id, track, closed):
        try:
            await sync_to_async(save_track)(incident_id, track, closed)
        except Exception:
            logger.exception("No se pudo actualizar el recorrido del incidente %s.", incident_id)

    async def startup(self):
        try:
            await sync_to_async(close_open_incidents)()
        except Exception:
            logger.exception("No se pudieron cerrar los incidentes abiertos.")
