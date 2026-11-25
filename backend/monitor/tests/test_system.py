from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from asgiref.sync import async_to_sync
from django.test import SimpleTestCase

from monitor import system
from monitor.system import ALERT_COOLDOWN_S, TemperatureWatcher


class ReadTests(SimpleTestCase):
    def test_temperature_prefers_cpu_zone(self):
        with TemporaryDirectory() as tmp:
            for name, kind, temp in (("thermal_zone0", "gpu-thermal", "40000"), ("thermal_zone1", "cpu-thermal", "52123")):
                zone = Path(tmp, name)
                zone.mkdir()
                (zone / "type").write_text(kind)
                (zone / "temp").write_text(temp)
            self.assertEqual(system.read_temperature(Path(tmp)), 52.1)

    def test_temperature_missing(self):
        with TemporaryDirectory() as tmp:
            self.assertIsNone(system.read_temperature(Path(tmp)))

    def test_memory(self):
        with TemporaryDirectory() as tmp:
            meminfo = Path(tmp, "meminfo")
            meminfo.write_text("MemTotal:  4000000 kB\nMemFree:  100 kB\nMemAvailable:  3000000 kB\n")
            memory = system.read_memory(meminfo)
        self.assertEqual(memory["total"], 4000000 * 1024)
        self.assertEqual(memory["used"], 1000000 * 1024)
        self.assertEqual(memory["percent"], 25.0)

    def test_snapshot_shape(self):
        data = system.snapshot()
        self.assertEqual(set(data), {"temperature_c", "memory", "disk", "uptime_s"})
        self.assertIsNotNone(data["disk"])


class WatcherTests(SimpleTestCase):
    def make(self, enabled=True, threshold=75):
        self.temp = 50
        self.now = 0
        self.sent = []
        self.config = SimpleNamespace(temp_alert_enabled=enabled, temp_alert_threshold_c=threshold)

        async def load():
            return self.config

        async def send(config, text):
            self.sent.append(text)

        return TemperatureWatcher(read=lambda: self.temp, load_config=load, send=send, clock=lambda: self.now)

    def run_checks(self, watcher, *temps):
        for temp in temps:
            self.temp = temp
            self.now += 60
            async_to_sync(watcher.check)()

    def test_one_alert_per_episode_and_recovery(self):
        watcher = self.make()
        self.run_checks(watcher, 80)  # un pico suelto no avisa
        self.assertEqual(self.sent, [])
        self.run_checks(watcher, 50, 80, 81, 82, 83, 79)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("EQUIPO CALIENTE", self.sent[0])
        self.assertIn("81 °C", self.sent[0])
        self.run_checks(watcher, 72)  # dentro del margen: sigue "caliente"
        self.assertEqual(len(self.sent), 1)
        self.run_checks(watcher, 69)
        self.assertEqual(len(self.sent), 2)
        self.assertIn("TEMPERATURA NORMAL", self.sent[1])

    def test_cooldown_between_hot_alerts(self):
        watcher = self.make()
        self.run_checks(watcher, 80, 80, 60, 80, 80, 60)
        # Volvió a calentarse enseguida: ni el segundo aviso de calor ni su "normal".
        self.assertEqual([t.split("</b>")[0] for t in self.sent], ["<b>EQUIPO CALIENTE", "<b>TEMPERATURA NORMAL"])
        self.now += ALERT_COOLDOWN_S
        self.run_checks(watcher, 80, 80)
        self.assertIn("EQUIPO CALIENTE", self.sent[-1])

    def test_disabled_sends_nothing(self):
        watcher = self.make(enabled=False)
        self.run_checks(watcher, 90, 90, 90, 40)
        self.assertEqual(self.sent, [])
