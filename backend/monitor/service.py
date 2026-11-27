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

from . import adsbfi, broadcast, exclusions
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
                 publisher=broadcast, clock=time.monotonic):
        self.fetch = fetch
        self.load_config = load_config
        self.notifier = notifier or TelegramNotifier(load_config)
        self.publisher = publisher
        self.clock = clock
        self.state = AircraftState()
        self.status = {"ok": None, "error": None, "last_update": None, "failures": 0}
        self._failures = 0
        self._excluded = frozenset()  # aeronaves que no generan alerta (claves de exclusions)
        self._predicted = {}  # hex -> hora (servidor) del último aviso de posible ingreso
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
        self._excluded = exclusions.excluded_keys(getattr(config, "excluded_aircraft", None))
        query = (config.zone_lat, config.zone_lon, config.query_radius_nm)

        await self._respect_request_gap()
        try:
            snapshot = await self.fetch(client, *query)
        except adsbfi.AdsbFiError as exc:
            return await self._handle_failure(config, exc)

        if self._failures:
            logger.info("adsb.fi responde nuevamente después de %d fallo(s).", self._failures)
        self._failures = 0
        self._server_clock = (snapshot.now, self.clock())
        if snapshot.msg and snapshot.msg != "No error":
            logger.warning("adsb.fi informó: %s", snapshot.msg)

        result = self.state.update(snapshot, zone, query)
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
            self.notifier.enqueue(alert, zone)
            await self.publisher.send_alert(alert)
        for hex_code in result.exited:
            logger.info("%s salió de la zona.", hex_code)
        self._predict(getattr(config, "predict_minutes", 0), zone, snapshot.now)

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
            logger.info("POSIBLE INGRESO: %s entraría a %s en %.1f min.", alert["label"], zone.name, minutes)
            self.notifier.enqueue(alert, zone)

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
        """Aeronaves para los navegadores, con la marca de las que no generan alerta."""
        return self.state.payload(self._excluded)

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
monitor = Monitor()


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
