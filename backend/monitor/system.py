"""Estado del equipo donde corre el backend (la Raspberry Pi) y aviso de temperatura.

Dentro del contenedor, /proc y /sys muestran los datos del equipo (no del
contenedor): temperatura del procesador, memoria, tiempo encendido. El disco se
mide sobre el volumen de datos, que vive en el disco del equipo.

El aviso de temperatura no hace spam: manda UN mensaje cuando el equipo pasa
la temperatura límite y otro cuando vuelve a estar por debajo (con un margen),
y entre dos avisos de calor deja pasar al menos ``ALERT_COOLDOWN_S``.
"""

import asyncio
import logging
import os
import shutil
import time
from pathlib import Path

import httpx
from asgiref.sync import sync_to_async

from configuration.models import SystemConfig

from .telegram import TelegramError, explain, send_message

logger = logging.getLogger(__name__)

THERMAL_DIR = Path("/sys/class/thermal")
MEMINFO = Path("/proc/meminfo")
UPTIME = Path("/proc/uptime")
DATA_DIR = os.path.dirname(os.environ.get("SQLITE_PATH", "")) or "/"

CHECK_INTERVAL_S = 60
# Lecturas seguidas por encima del límite antes de avisar (evita picos sueltos).
HOT_READINGS = 2
# Para dar por terminado el calor, la temperatura tiene que bajar este margen.
HYSTERESIS_C = 5.0
ALERT_COOLDOWN_S = 60 * 60


def read_temperature(thermal_dir=THERMAL_DIR):
    """Temperatura del procesador en °C, o None si el equipo no la informa."""
    zones = sorted(thermal_dir.glob("thermal_zone*"))
    # En la Raspberry Pi la zona del procesador se llama "cpu-thermal".
    zones.sort(key=lambda z: 0 if "cpu" in _read(z / "type").lower() else 1)
    for zone in zones:
        raw = _read(zone / "temp")
        try:
            return round(int(raw) / 1000, 1)
        except ValueError:
            continue
    return None


def read_memory(meminfo=MEMINFO):
    values = {}
    for line in _read(meminfo).splitlines():
        key, _, rest = line.partition(":")
        if key in ("MemTotal", "MemAvailable"):
            values[key] = int(rest.split()[0]) * 1024
    if "MemTotal" not in values or "MemAvailable" not in values:
        return None
    return _usage(values["MemTotal"], values["MemTotal"] - values["MemAvailable"])


def read_disk(path=DATA_DIR):
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return None
    return _usage(usage.total, usage.used)


def read_uptime(uptime=UPTIME):
    try:
        return int(float(_read(uptime).split()[0]))
    except (ValueError, IndexError):
        return None


def _usage(total, used):
    return {"total": total, "used": used, "percent": round(used / total * 100, 1) if total else 0}


def _read(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return ""


def snapshot():
    return {
        "temperature_c": read_temperature(),
        "memory": read_memory(),
        "disk": read_disk(),
        "uptime_s": read_uptime(),
    }


# --- aviso de temperatura ----------------------------------------------------


def hot_text(temp, threshold):
    return (f"<b>EQUIPO CALIENTE</b>\n\nEl equipo del monitor está a <b>{temp:.0f} °C</b> "
            f"(límite: {threshold:.0f} °C).\n\n<i>Revisá la ventilación. Si sigue subiendo, "
            "el equipo se vuelve lento y puede apagarse.</i>")


def normal_text(temp):
    return f"<b>TEMPERATURA NORMAL</b>\n\nEl equipo del monitor bajó a {temp:.0f} °C."


class TemperatureWatcher:
    """Revisa la temperatura cada minuto y avisa por Telegram (sin repetir)."""

    def __init__(self, *, read=read_temperature, load_config=None, send=None, clock=time.monotonic):
        self.read = read
        self.load_config = load_config or sync_to_async(SystemConfig.load)
        self.send = send or self._send_telegram
        self.clock = clock
        self.hot = False  # está por encima del límite (y todavía no bajó el margen)
        self._alerted = False  # se mandó el aviso de calor de este episodio
        self._over = 0
        self._last_alert_at = None
        self._task = None

    def start(self):
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self.run(), name="temperature-watcher")

    async def run(self):
        if self.read() is None:
            logger.info("El equipo no informa la temperatura: no se vigila.")
            return
        while True:
            try:
                await self.check()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Error inesperado al revisar la temperatura.")
            await asyncio.sleep(CHECK_INTERVAL_S)

    async def check(self):
        temp = self.read()
        if temp is None:
            return
        config = await self.load_config()
        threshold = config.temp_alert_threshold_c

        if temp >= threshold:
            self._over += 1
        else:
            self._over = 0

        if not config.temp_alert_enabled:
            self.hot = self._alerted = False
            return

        if not self.hot and self._over >= HOT_READINGS:
            cooled_down = self._last_alert_at is None or self.clock() - self._last_alert_at >= ALERT_COOLDOWN_S
            logger.warning("Temperatura del equipo: %.1f °C (límite %.0f °C).", temp, threshold)
            self.hot = True
            if cooled_down:
                self._alerted = True
                self._last_alert_at = self.clock()
                await self.send(config, hot_text(temp, threshold))
        elif self.hot and temp <= threshold - HYSTERESIS_C:
            logger.info("Temperatura del equipo normal: %.1f °C.", temp)
            self.hot = False
            if self._alerted:
                self._alerted = False
                await self.send(config, normal_text(temp))

    async def _send_telegram(self, config, text):
        if not config.telegram_enabled:
            logger.info("Telegram no está configurado: el aviso de temperatura no se envía.")
            return
        async with httpx.AsyncClient() as client:
            try:
                await send_message(client, config.telegram_bot_token, config.telegram_chat_id, text)
            except TelegramError as exc:
                logger.error("No se pudo enviar el aviso de temperatura: %s", explain(exc))


temperature_watcher = TemperatureWatcher()
