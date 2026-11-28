"""Monitor en segundo plano: una tarea asyncio dentro del proceso de daphne.

Cada ciclo lee la configuración guardada (así los cambios se aplican sin
reiniciar), hace UNA consulta a adsb.fi centrada en la zona, actualiza el
estado en memoria, encola las alertas de Telegram y difunde a los navegadores.
"""

import asyncio
import logging
import time

from asgiref.sync import sync_to_async
from django.conf import settings

from configuration.models import SystemConfig

from . import adsbfi, behavior, broadcast, exclusions
from .alerts import build_alert
from .state import AircraftState
from .telegram import TelegramNotifier
from .zone import Zone

logger = logging.getLogger(__name__)

MAX_BACKOFF_S = 300.0
# Separación mínima entre dos consultas aunque se pida un refresco inmediato
# (adsb.fi admite como máximo una por segundo).
MIN_REQUEST_GAP_S = 2.0
UNEXPECTED_ERROR_DELAY_S = 30.0
# Una aeronave avisada como "posible ingreso" no se vuelve a avisar hasta pasado
# este tiempo (evita mensajes repetidos con aviones que dan vueltas cerca).
PREDICTION_COOLDOWN_S = 15 * 60
# Horizonte de la estimación de ingreso que va a los navegadores (Modo cielo)
# cuando el aviso anticipado por Telegram está apagado (predict_minutes = 0).
ETA_DEFAULT_HORIZON_MIN = 10
# Un mismo comportamiento raro de una aeronave no se vuelve a avisar hasta que
# pase este tiempo sin que se repita (mientras sigue, no se insiste).
BEHAVIOR_COOLDOWN_S = 30 * 60
# Cuánto tiempo se marca la aeronave en el mapa después de la última señal.
BEHAVIOR_BADGE_S = 5 * 60
# Tras un incidente se sigue guardando el recorrido este tiempo (repetición en 3D)
# y se escribe en la base cada tanto, no en cada ciclo.
INCIDENT_FOLLOW_S = 10 * 60
INCIDENT_SAVE_EVERY_S = 30
# Si desaparecen de golpe más aeronaves que estas, es la fuente y no un transpondedor.
MAX_SIMULTANEOUS_LOST = 2


def backoff_delay(poll_interval_s, failures, retry_after=None):
    """Espera creciente tras ``failures`` fallos seguidos: 2x, 4x, 8x… el intervalo."""
    delay = min(poll_interval_s * 2 ** failures, MAX_BACKOFF_S)
    if retry_after:
        delay = max(delay, retry_after)
    return delay


async def load_config():
    return await sync_to_async(SystemConfig.load)()


class Monitor:
    def __init__(self, *, fetch=adsbfi.fetch_snapshot, load_config=load_config, notifier=None,
                 publisher=broadcast, clock=time.monotonic, recorder=None):
        self.fetch = fetch
        self.load_config = load_config
        self.notifier = notifier or TelegramNotifier(load_config)
        self.publisher = publisher
        self.clock = clock
        self.recorder = recorder  # registro de incidentes (None: no se guarda nada)
        self.state = AircraftState()
        self.tracks = behavior.Tracks()
        self._flagged = {}  # (hex, tipo de comportamiento) -> hora de la última señal
        self._open = {}  # id de incidente -> {"hex", "until", "saved_at"}: recorridos que se siguen guardando
        self._now = None  # hora del servidor de adsb.fi en el último ciclo
        self.status = {"ok": None, "error": None, "last_update": None, "failures": 0}
        self._failures = 0
        self._excluded = frozenset()  # aeronaves que no generan alerta (claves de exclusions)
        self._predicted = {}  # hex -> hora (servidor) del último aviso de posible ingreso
        self._zone = None  # zona del último ciclo, para estimar ingresos en el payload
        self._eta_horizon_min = ETA_DEFAULT_HORIZON_MIN
        self._last_request_at = None
        self._server_clock = None  # (now del servidor, clock local) de la última respuesta
        self._loop = None
        self._wake = None
        self._task = None

    # --- ciclo de vida -------------------------------------------------------

    def start(self):
        """Arranca el monitor. Debe llamarse con el event loop del servidor corriendo."""
        if self._task is not None:
            return
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        self.notifier.start()
        self._task = self._loop.create_task(self.run(), name="aircraft-monitor")
        logger.info("Monitor de aeronaves iniciado.")

    def request_refresh(self):
        """Pide un ciclo inmediato (p. ej. tras cambiar la zona). Seguro desde otro hilo."""
        if self._loop is not None and self._wake is not None:
            self._loop.call_soon_threadsafe(self._wake.set)

    async def run(self):
        if self.recorder is not None:
            await self.recorder.startup()
        async with adsbfi.make_client(settings.ADSBFI_USER_AGENT) as client:
            while True:
                started = self.clock()
                try:
                    delay = await self.run_cycle(client)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Error inesperado en el ciclo del monitor.")
                    delay = UNEXPECTED_ERROR_DELAY_S
                await self._sleep(max(0.0, delay - (self.clock() - started)))

    async def _sleep(self, seconds):
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=seconds)
            logger.debug("Refresco inmediato solicitado.")
        except asyncio.TimeoutError:
            pass
        self._wake.clear()

    async def _respect_request_gap(self):
        if self._last_request_at is not None:
            wait = MIN_REQUEST_GAP_S - (self.clock() - self._last_request_at)
            if wait > 0:
                await asyncio.sleep(wait)
        self._last_request_at = self.clock()

    # --- un ciclo ------------------------------------------------------------

    async def run_cycle(self, client):
        """Ejecuta un ciclo y devuelve cuántos segundos esperar hasta el próximo."""
        config = await self.load_config()
        zone = Zone.from_config(config)
        self._zone = zone
        self._eta_horizon_min = getattr(config, "predict_minutes", 0) or ETA_DEFAULT_HORIZON_MIN
        self._excluded = exclusions.excluded_keys(getattr(config, "excluded_aircraft", None))
        query = (config.zone_lat, config.zone_lon, config.query_radius_nm)

        await self._respect_request_gap()
        try:
            snapshot = await self.fetch(client, *query)
        except adsbfi.AdsbFiError as exc:
            return await self._handle_failure(config, exc)

        recovering = bool(self._failures)
        if self._failures:
            logger.info("adsb.fi responde nuevamente después de %d fallo(s).", self._failures)
        self._failures = 0
        self._server_clock = (snapshot.now, self.clock())
        if snapshot.msg and snapshot.msg != "No error":
            logger.warning("adsb.fi informó: %s", snapshot.msg)

        # Para "dejó de transmitir": quién estaba adentro antes de esta respuesta.
        same_query = self.state.query_key == query
        before = {h: self.state.aircraft[h] for h in self.state.inside if h in self.state.aircraft}

        result = self.state.update(snapshot, zone, query)
        self._now = snapshot.now
        for aircraft in self.state.aircraft.values():
            self.tracks.record(aircraft)
        self._log_incomplete(result)
        self._set_status(ok=True, error=None, last_update=snapshot.now)

        for aircraft in result.entered:
            if exclusions.is_excluded(aircraft, self._excluded):
                logger.info("%s ingresó a %s; no se alerta porque está en la lista de aeronaves sin alerta.",
                            aircraft.label, zone.name)
                continue
            alert = build_alert(aircraft, zone)
            logger.info("ALERTA: %s ingresó a %s (alt %s ft, %s kt, rumbo %s°, %.1f NM).",
                        alert["label"], zone.name, alert["alt_ft"], alert["gs"], alert["track"], alert["dst"])
            alert["incident_id"] = await self._open_incident(behavior.ENTRY, zone, alert, None, snapshot.now)
            self.notifier.enqueue(alert, zone)
            await self.publisher.send_alert(alert)
        for hex_code in result.exited:
            logger.info("%s salió de la zona.", hex_code)
        self._predict(getattr(config, "predict_minutes", 0), zone, snapshot.now)
        lost = [before[h] for h in result.purged if h in before] if same_query and not recovering else []
        await self._watch_behavior(getattr(config, "behavior_alerts", False), zone, lost, snapshot.now)
        await self._follow_incidents(snapshot.now)
        self.tracks.prune(snapshot.now, keep={info["hex"] for info in self._open.values()})

        await self.publisher.send_aircraft(self.aircraft_payload(), self.status)
        return config.poll_interval_s

    def _predict(self, horizon_min, zone, now):
        """Avisa por Telegram de las aeronaves que entrarían a la zona en menos de ``horizon_min``."""
        self._predicted = {h: t for h, t in self._predicted.items() if now - t < PREDICTION_COOLDOWN_S}
        if not horizon_min:
            return
        for hex_code, aircraft in self.state.aircraft.items():
            if (hex_code in self.state.inside or hex_code in self._predicted
                    or exclusions.is_excluded(aircraft, self._excluded)):
                continue
            minutes = zone.minutes_to_entry(aircraft, horizon_min)
            if minutes is None:
                continue
            self._predicted[hex_code] = now
            alert = {**build_alert(aircraft, zone), "eta_min": minutes}
            logger.info("PREAVISO: %s entraría a %s en %.1f min.", alert["label"], zone.name, minutes)
            self.notifier.enqueue(alert, zone)

    # --- comportamiento raro e incidentes ------------------------------------

    async def _watch_behavior(self, enabled, zone, lost, now):
        """Avisa (web y Telegram) de los comportamientos raros nuevos. Ver ``behavior``."""
        self._flagged = {k: t for k, t in self._flagged.items() if now - t < BEHAVIOR_COOLDOWN_S}
        if not enabled:
            return
        events = []
        for hex_code, aircraft in self.state.aircraft.items():
            if exclusions.is_excluded(aircraft, self._excluded):
                continue
            found = behavior.detect(aircraft, self.tracks.points.get(hex_code, ()),
                                    self.tracks.rates.get(hex_code, ()), zone, now)
            events.extend((kind, aircraft, detail) for kind, detail in found.items())
        if len(lost) > MAX_SIMULTANEOUS_LOST:
            logger.warning("%d aeronaves dejaron de verse a la vez dentro de la zona: parece un corte "
                           "de la fuente de datos, no se avisa.", len(lost))
            lost = []
        for aircraft in lost:
            if not exclusions.is_excluded(aircraft, self._excluded) and behavior.lost_while_flying(aircraft, self.tracks.get(aircraft.hex)):
                events.append((behavior.SIGNAL_LOST, aircraft, {"last_seen": aircraft.timestamp}))

        for kind, aircraft, detail in events:
            key = (aircraft.hex, kind)
            repeated = key in self._flagged
            self._flagged[key] = now  # mientras siga pasando, no se vuelve a avisar
            if repeated:
                continue
            alert = {
                **build_alert(aircraft, zone),
                "kind": kind,
                "title": behavior.KIND_TITLES[kind],
                "summary": behavior.summary(kind, detail, zone.name),
                "detail": detail,
                "time": now,
            }
            logger.info("COMPORTAMIENTO: %s — %s.", alert["label"], alert["summary"])
            alert["incident_id"] = await self._open_incident(kind, zone, alert, detail, now)
            self.notifier.enqueue(alert, zone)
            await self.publisher.send_incident({**alert, "id": alert["incident_id"], "simulated": False})

    async def _open_incident(self, kind, zone, alert, detail, now):
        """Guarda el incidente con el recorrido hasta ahora. Devuelve su id (o None)."""
        if self.recorder is None:
            return None
        saved = await self.recorder.open(kind, zone, occurred_at=now, alert=alert, detail=detail,
                                         track=self.tracks.get(alert["hex"]))
        if not saved:
            return None
        self._open[saved["id"]] = {"hex": alert["hex"], "until": now + INCIDENT_FOLLOW_S, "saved_at": now}
        return saved["id"]

    async def _follow_incidents(self, now):
        """Completa el recorrido de los incidentes recientes (lo que pasó después)."""
        for incident_id, info in list(self._open.items()):
            done = now >= info["until"] or info["hex"] not in self.state.aircraft
            if not done and now - info["saved_at"] < INCIDENT_SAVE_EVERY_S:
                continue
            info["saved_at"] = now
            await self.recorder.update(incident_id, self.tracks.get(info["hex"]), done)
            if done:
                del self._open[incident_id]

    def aircraft_now(self):
        """Copia de las aeronaves actuales, segura desde una vista (otro hilo)."""
        for _attempt in range(3):
            try:
                return list(self.state.aircraft.values())
            except RuntimeError:  # el monitor justo estaba modificando el diccionario
                continue
        return []

    def notify_from_thread(self, alert):
        """Encola un aviso de Telegram desde una vista (otro hilo que el del monitor)."""
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self.notifier.enqueue, alert, None)

    def behavior_of(self, hex_code):
        """Comportamientos raros recientes de una aeronave (para marcarla en el mapa)."""
        if self._now is None:
            return []
        return [kind for (h, kind), t in self._flagged.items() if h == hex_code and self._now - t < BEHAVIOR_BADGE_S]

    async def _handle_failure(self, config, exc):
        self._failures += 1
        delay = backoff_delay(config.poll_interval_s, self._failures, getattr(exc, "retry_after", None))
        logger.warning("Falló la consulta a adsb.fi (%d seguida/s): %s Reintento en %.0f s.",
                       self._failures, exc, delay)
        # Sin respuesta igual se purga lo viejo, estimando la hora del servidor.
        if self._server_clock is not None:
            server_now, at = self._server_clock
            self.state.purge(server_now + (self.clock() - at))
        self._set_status(ok=False, error=str(exc))
        await self.publisher.send_aircraft(self.aircraft_payload(), self.status)
        return delay

    def aircraft_payload(self):
        """Aeronaves para los navegadores, con la marca de las que no generan alerta.

        ``eta_min``: minutos estimados hasta entrar a la zona (la misma cuenta del
        aviso anticipado), o None si ya está adentro, se aleja o tarda más que el
        horizonte. Lo usa el Modo cielo. ``behavior``: comportamientos raros
        recientes (ver ``behavior``).
        """
        payload = self.state.payload(self._excluded)
        for item in payload:
            aircraft = self.state.aircraft.get(item["hex"])
            eta = None
            if self._zone is not None and aircraft is not None and not item["inside"]:
                eta = self._zone.minutes_to_entry(aircraft, self._eta_horizon_min)
            item["eta_min"] = round(eta, 1) if eta is not None else None
            item["behavior"] = self.behavior_of(item["hex"])
        return payload

    def _log_incomplete(self, result):
        for hex_code, missing in result.newly_incomplete.items():
            logger.warning("Aeronave %s sin %s: no se usa para alertar.", hex_code, ", ".join(missing))
        if result.all_incomplete_started:
            logger.error("Ninguna aeronave de la respuesta de adsb.fi trae lat/lon/dst: "
                         "no se puede evaluar la zona. ¿Cambió el formato de la API?")
        if result.all_incomplete_ended:
            logger.info("La respuesta de adsb.fi vuelve a traer aeronaves con posición.")

    def _set_status(self, *, ok, error, last_update=None):
        self.status = {
            "ok": ok,
            "error": error,
            "last_update": last_update if last_update is not None else self.status.get("last_update"),
            "failures": self._failures,
        }


# Instancia única del proceso: la usan el consumer de WebSocket y la API.
def _db_recorder():
    from .records import DbRecorder  # acá y no arriba: necesita los modelos ya cargados

    return DbRecorder()


monitor = Monitor(recorder=_db_recorder())


def start_with_daphne():
    """Programa el arranque del monitor cuando el reactor de daphne empiece a correr.

    daphne no implementa el protocolo "lifespan" de ASGI, pero ejecuta todo
    sobre el reactor asyncio de Twisted: ``callWhenRunning`` + ``callLater(0)``
    garantiza que ``start()`` corre con el event loop del servidor ya activo.
    """
    from twisted.internet import reactor

    from .system import temperature_watcher

    def start():
        monitor.start()
        temperature_watcher.start()

    reactor.callWhenRunning(lambda: reactor.callLater(0, start))
