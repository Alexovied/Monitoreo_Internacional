"""Comportamiento raro dentro del ciclo del monitor y API del registro."""

import base64
import io
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth.models import Group, User
from django.test import SimpleTestCase, TestCase, override_settings
from PIL import Image
from rest_framework.test import APIClient

from accounts.roles import ADMIN_GROUP, USER_GROUP
from configuration.tests import VALID
from monitor import behavior, records, simulate
from monitor.alerts import format_alert_message
from monitor.models import Incident
from monitor.service import INCIDENT_FOLLOW_S, Monitor

from .helpers import CNA, NOW, make_aircraft, make_snapshot
from .test_service import CONFIG, FakeClock, FakeNotifier, FakePublisher


class Publisher(FakePublisher):
    def __init__(self):
        super().__init__()
        self.incidents = []

    async def send_incident(self, incident):
        self.incidents.append(incident)


class FakeRecorder:
    def __init__(self):
        self.opened, self.updates, self.started = [], [], 0

    async def startup(self):
        self.started += 1

    async def open(self, kind, zone, *, occurred_at, alert, detail, track):
        self.opened.append({"kind": kind, "label": alert["label"], "track": track, "time": occurred_at})
        return {"id": len(self.opened)}

    async def update(self, incident_id, track, closed):
        self.updates.append((incident_id, len(track), closed))


class BehaviorCycleTests(SimpleTestCase):
    def setUp(self):
        self.responses = []
        self.config = SimpleNamespace(**vars(CONFIG), behavior_alerts=True, predict_minutes=0)
        self.clock = FakeClock()
        self.publisher = Publisher()
        self.notifier = FakeNotifier()
        self.recorder = FakeRecorder()

        async def fetch(client, lat, lon, radius):
            return self.responses.pop(0)

        async def load_config():
            return self.config

        self.monitor = Monitor(fetch=fetch, load_config=load_config, notifier=self.notifier,
                               publisher=self.publisher, clock=self.clock, recorder=self.recorder)

    async def cycle(self, *aircraft, at):
        self.responses.append(make_snapshot(*aircraft, now=at))
        self.clock.t += 5
        return await self.monitor.run_cycle(client=None)

    async def circle(self, steps, **extra):
        for i in range(steps):
            await self.cycle(make_aircraft(dst=8.0, alt=2500, gs=90.0, track=(i * 30) % 360,
                                           now=NOW + i * 10, **extra), at=NOW + i * 10)

    async def test_loitering_is_alerted_once_and_recorded(self):
        await self.circle(30)  # 870° de giro: cruza el umbral (540°) en el camino
        self.assertEqual([i["kind"] for i in self.publisher.incidents], [behavior.LOITER])
        incident = self.publisher.incidents[0]
        self.assertEqual(incident["label"], "ARG1309")
        self.assertEqual(incident["id"], 1)
        self.assertIn("vueltas", incident["summary"])
        self.assertEqual([a["kind"] for a in self.notifier.queued], [behavior.LOITER])
        self.assertEqual(self.publisher.alerts, [])  # no entró a la zona: no es una alerta de ingreso
        opened = self.recorder.opened[0]
        self.assertEqual(opened["kind"], behavior.LOITER)
        self.assertEqual(len(opened["track"]), 19)  # el recorrido hasta el momento del aviso
        # La aeronave queda marcada en el mapa.
        self.assertEqual(self.publisher.updates[-1][0][0]["behavior"], [behavior.LOITER])

    async def test_nothing_is_reported_when_the_setting_is_off(self):
        self.config.behavior_alerts = False
        await self.circle(30, squawk="7700")
        self.assertEqual(self.publisher.incidents, [])
        self.assertEqual(self.notifier.queued, [])
        self.assertEqual(self.publisher.updates[-1][0][0]["behavior"], [])

    async def test_excluded_aircraft_are_not_reported(self):
        self.config.excluded_aircraft = ["E0B390"]
        await self.circle(30, squawk="7700")
        self.assertEqual(self.publisher.incidents, [])

    async def test_emergency_squawk_is_reported_once_while_it_lasts(self):
        for i in range(5):
            await self.cycle(make_aircraft(dst=40.0, squawk="7700", now=NOW + i * 5), at=NOW + i * 5)
        self.assertEqual([i["kind"] for i in self.publisher.incidents], [behavior.EMERGENCY])
        self.assertIn("7700", self.publisher.incidents[0]["summary"])
        message = format_alert_message(self.notifier.queued[0])
        self.assertTrue(message.startswith("<b>EMERGENCIA DECLARADA</b>"))
        self.assertNotIn("INGRESO A ZONA", message)

    async def test_transponder_switched_off_inside_the_zone(self):
        for i in range(7):  # se la venía recibiendo bien
            await self.cycle(make_aircraft(dst=2.0, alt=3000, gs=140.0, now=NOW + i * 5), at=NOW + i * 5)
        self.assertEqual(len(self.publisher.alerts), 1)  # el ingreso
        self.assertEqual(self.publisher.alerts[0]["incident_id"], 1)
        other = make_aircraft("e48a1b", dst=30.0, flight="AZU4521", now=NOW + 100)
        await self.cycle(other, at=NOW + 100)  # 70 s sin posición: se la deja de ver
        self.assertEqual([i["kind"] for i in self.publisher.incidents], [behavior.SIGNAL_LOST])
        self.assertEqual(self.publisher.incidents[0]["label"], "ARG1309")
        self.assertEqual([o["kind"] for o in self.recorder.opened], [behavior.ENTRY, behavior.SIGNAL_LOST])

    async def test_landing_inside_the_zone_is_not_a_lost_signal(self):
        for i in range(7):
            await self.cycle(make_aircraft(dst=2.0, alt=600, gs=65.0, baro_rate=-600.0, now=NOW + i * 5), at=NOW + i * 5)
        await self.cycle(at=NOW + 100)
        self.assertEqual(self.publisher.incidents, [])

    async def test_aircraft_seen_only_once_is_not_a_lost_signal(self):
        await self.cycle(make_aircraft(dst=2.0, alt=3000, gs=140.0, now=NOW), at=NOW)
        await self.cycle(at=NOW + 70)  # poca cobertura: apareció una vez y no se la vio más
        self.assertEqual(self.publisher.incidents, [])

    async def test_many_lost_at_once_is_a_feed_problem(self):
        for step in range(7):
            at = NOW + step * 5
            await self.cycle(*[make_aircraft(f"e0000{i}", dst=2.0, alt=3000, gs=140.0, now=at) for i in range(3)], at=at)
        with self.assertLogs("monitor.service", "WARNING") as logs:
            await self.cycle(at=NOW + 100)
        self.assertEqual(self.publisher.incidents, [])
        self.assertTrue(any("corte" in line for line in logs.output))

    async def test_rapid_descent(self):
        for i, rate in enumerate([-1500.0, -4800.0, -5200.0, -5200.0]):
            await self.cycle(make_aircraft(dst=9.0, alt=12000 - i * 400, baro_rate=rate, now=NOW + i * 5),
                             at=NOW + i * 5)
        self.assertEqual([i["kind"] for i in self.publisher.incidents], [behavior.RAPID_DESCENT])
        self.assertEqual(self.publisher.incidents[0]["detail"], {"rate_fpm": -5200})

    async def test_track_keeps_growing_after_the_incident_and_then_closes(self):
        await self.cycle(make_aircraft(dst=2.0, now=NOW), at=NOW)  # ingreso: incidente 1
        self.assertEqual(self.recorder.updates, [])
        for i in range(1, 8):
            await self.cycle(make_aircraft(dst=2.0, now=NOW + i * 5), at=NOW + i * 5)
        self.assertEqual(self.recorder.updates, [(1, 7, False)])  # se guarda cada 30 s, no en cada ciclo
        late = NOW + INCIDENT_FOLLOW_S + 5
        await self.cycle(make_aircraft(dst=2.0, now=late), at=late)
        self.assertEqual(self.recorder.updates[-1][0::2], (1, True))
        await self.cycle(make_aircraft(dst=2.0, now=late + 60), at=late + 60)
        self.assertEqual(len(self.recorder.updates), 2)  # cerrado: no se toca más


def jpeg(size=(40, 30)):
    out = io.BytesIO()
    Image.new("RGB", size, (30, 90, 200)).save(out, "JPEG")
    return "data:image/jpeg;base64," + base64.b64encode(out.getvalue()).decode()


@override_settings(INITIAL_CONFIG=VALID)
class RegistryApiTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user("jefe", password="x")
        self.admin.groups.add(Group.objects.get(name=ADMIN_GROUP))
        self.user = User.objects.create_user("guardia", password="x", first_name="Ana", last_name="Paz")
        self.user.groups.add(Group.objects.get(name=USER_GROUP))
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        patcher = mock.patch("monitor.views.broadcast.send_incident_sync")
        self.sent = patcher.start()
        self.addCleanup(patcher.stop)
        state = mock.patch("monitor.views.monitor")
        self.monitor = state.start()
        self.addCleanup(state.stop)
        self.monitor.aircraft_now.return_value = []
        self.monitor.status = {"ok": True}

    def body(self, **changes):
        return {"observer": {"source": "zone"}, "azimuth": 0, "elevation": 10, **changes}

    def north(self, nm=10, **extra):
        ac = make_aircraft(lat=VALID["zone_lat"] + nm / 60, lon=VALID["zone_lon"], dst=nm, alt=10_000,
                           flight="GLO1234", **extra)
        self.monitor.aircraft_now.return_value = [ac]
        return ac

    def test_requires_a_session(self):
        anonymous = APIClient()
        self.assertEqual(anonymous.get("/api/incidents/").status_code, 401)
        self.assertEqual(anonymous.post("/api/sightings/", self.body(), format="json").status_code, 401)

    def test_check_says_which_aircraft_is_there(self):
        self.north()
        response = self.client.post("/api/sightings/check/", self.body(azimuth=1, elevation=9), format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["match"]["label"], "GLO1234")
        self.assertEqual(response.data["aircraft_seen"], 1)
        self.assertEqual(Incident.objects.count(), 0)  # consultar no guarda nada

    def test_sighting_of_something_without_transponder_is_saved(self):
        self.north()
        response = self.client.post("/api/sightings/", self.body(
            azimuth=180, elevation=35, note="  Avioneta blanca, sin luces  ", photo=jpeg()), format="json")
        self.assertEqual(response.status_code, 201, response.data)
        incident = Incident.objects.get()
        self.assertEqual(incident.kind, behavior.SIGHTING)
        self.assertEqual(incident.summary, "Objeto sin transponder hacia el S (180°), 35° sobre el horizonte")
        detail = incident.data["detail"]
        self.assertEqual(detail["note"], "Avioneta blanca, sin luces")
        self.assertEqual(detail["reported_by"], "Ana Paz")
        self.assertEqual(detail["observer"]["source"], "zone")
        self.assertEqual(detail["traffic"][0][1], "GLO1234")  # lo que sí se veía con transponder
        self.assertFalse(detail["forced"])
        self.assertTrue(response.data["has_photo"])
        self.sent.assert_called_once()
        self.assertNotIn("traffic", self.sent.call_args[0][0]["detail"])
        self.monitor.notify_from_thread.assert_called_once()
        text = self.monitor.notify_from_thread.call_args[0][0]["text"]
        self.assertIn("AVISTAMIENTO SIN TRANSPONDER", text)
        self.assertIn("Avioneta blanca", text)

        photo = self.client.get(f"/api/incidents/{incident.pk}/photo/")
        self.assertEqual(photo.status_code, 200)
        self.assertEqual(photo["Content-Type"], "image/jpeg")
        self.assertEqual(Image.open(io.BytesIO(photo.content)).size, (40, 30))

    def test_identified_aircraft_is_not_saved_unless_forced(self):
        self.north()
        response = self.client.post("/api/sightings/", self.body(azimuth=1, elevation=9), format="json")
        self.assertEqual(response.status_code, 409)
        self.assertIn("GLO1234", response.data["detail"])
        self.assertEqual(Incident.objects.count(), 0)
        response = self.client.post("/api/sightings/", self.body(azimuth=1, elevation=9, force=True), format="json")
        self.assertEqual(response.status_code, 201)
        incident = Incident.objects.get()
        self.assertTrue(incident.data["detail"]["forced"])
        self.assertIn("GLO1234", incident.summary)

    def test_real_position_uses_real_angles(self):
        self.north(27, )  # 50 km al norte, 3.000 m: unos 3° reales
        body = self.body(observer={"source": "gps", "lat": VALID["zone_lat"], "lon": VALID["zone_lon"], "alt": 20},
                         azimuth=0, elevation=3, lift=4)
        response = self.client.post("/api/sightings/check/", body, format="json")
        self.assertIsNotNone(response.data["match"])  # el "cielo levantado" no se aplica con la ubicación real

    def test_invalid_input(self):
        for changes in ({"azimuth": 400}, {"elevation": 120}, {"observer": {"source": "gps"}},
                        {"photo": "data:image/jpeg;base64,bm8gc295IHVuYSBmb3Rv"}, {"note": "x" * 301}):
            response = self.client.post("/api/sightings/", self.body(**changes), format="json")
            self.assertEqual(response.status_code, 400, changes)
        self.assertEqual(Incident.objects.count(), 0)

    def test_big_photo_is_shrunk(self):
        response = self.client.post("/api/sightings/", self.body(photo=jpeg((2600, 1300))), format="json")
        self.assertEqual(response.status_code, 201)
        photo = self.client.get(f"/api/incidents/{response.data['id']}/photo/")
        self.assertEqual(Image.open(io.BytesIO(photo.content)).size, (1280, 640))

    def test_no_telegram_when_behavior_alerts_are_off(self):
        from configuration.models import SystemConfig
        config = SystemConfig.load()
        config.behavior_alerts = False
        config.save()
        self.assertEqual(self.client.post("/api/sightings/", self.body(), format="json").status_code, 201)
        self.monitor.notify_from_thread.assert_not_called()

    def test_list_detail_and_delete(self):
        zone = CNA
        track = [[NOW + i, -35.0, -58.7, 3000, 140.0, 90.0] for i in range(4)]
        first = records.create_incident(behavior.ENTRY, zone, occurred_at=NOW, track=track,
                                        alert={"hex": "e0b390", "label": "ARG1309", "type": "B38M"})
        records.create_incident(behavior.LOITER, zone, occurred_at=NOW + 60,
                                alert={"hex": "e48a1b", "label": "LV-GHT"},
                                detail={"turns": 2.0, "direction": "horario"})
        listed = self.client.get("/api/incidents/").data
        self.assertEqual([i["label"] for i in listed], ["LV-GHT", "ARG1309"])  # el más nuevo primero
        self.assertEqual(listed[1]["points"], 4)
        self.assertNotIn("track", listed[1])
        self.assertEqual(listed[0]["summary"], "Dio 2,0 vueltas (horario) cerca de ALERTA CNA 10KM")
        self.assertEqual(len(self.client.get("/api/incidents/?kind=loiter").data), 1)

        detail = self.client.get(f"/api/incidents/{first.pk}/").data
        self.assertEqual(detail["track"], track)
        self.assertEqual(detail["zone"]["radius_nm"], 5.4)
        self.assertEqual(self.client.get(f"/api/incidents/{first.pk}/photo/").status_code, 404)
        self.assertEqual(self.client.get("/api/incidents/9999/").status_code, 404)

        self.assertEqual(self.client.delete(f"/api/incidents/{first.pk}/").status_code, 403)
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.delete(f"/api/incidents/{first.pk}/").status_code, 204)
        self.assertEqual(Incident.objects.count(), 1)

    def test_simulation_is_for_admins_and_is_marked(self):
        self.assertEqual(self.client.post("/api/incidents/simulate/", {"kind": "loiter"}, format="json").status_code, 403)
        self.client.force_authenticate(self.admin)
        for kind in sorted(simulate.PROFILES):
            response = self.client.post("/api/incidents/simulate/", {"kind": kind}, format="json")
            self.assertEqual(response.status_code, 201, kind)
            self.assertTrue(response.data["simulated"])
            self.assertTrue(response.data["summary"].startswith("SIMULACRO"))
            self.assertGreater(response.data["points"], 20)
        self.assertEqual(self.sent.call_count, len(simulate.PROFILES))
        self.monitor.notify_from_thread.assert_not_called()  # un simulacro nunca va a Telegram
        self.assertEqual(self.client.post("/api/incidents/simulate/", {"kind": "sighting"}, format="json").status_code, 400)

    def test_old_incidents_are_pruned_and_tracks_updated(self):
        for i in range(6):
            records.create_incident(behavior.ENTRY, CNA, occurred_at=NOW + i, alert={"label": f"A{i}"})
        records.prune(maximum=4)
        self.assertEqual(list(Incident.objects.values_list("label", flat=True)), ["A5", "A4", "A3", "A2"])
        newest = Incident.objects.first()
        records.save_track(newest.pk, [[NOW, 1, 2, 3, 4, 5]] * 3, closed=False)
        records.save_track(newest.pk, [], closed=True)  # sin puntos nuevos conserva el recorrido
        newest.refresh_from_db()
        self.assertEqual((newest.points, len(newest.track), newest.closed), (3, 3, True))
        self.assertEqual(records.close_open_incidents(), 3)


class SimulationTests(SimpleTestCase):
    def test_simulated_loiter_would_be_detected_by_the_real_rule(self):
        _alert, _detail, track, _at = simulate.build(behavior.LOITER, CNA, NOW)
        self.assertGreaterEqual(abs(behavior.turned_degrees(track, track[-1][0], window_s=3600)), behavior.LOITER_TURN_DEG)

    def test_simulated_entry_crosses_the_zone(self):
        alert, _detail, track, occurred_at = simulate.build(behavior.ENTRY, CNA, NOW)
        self.assertLess(alert["dst"], CNA.radius_nm)
        self.assertEqual(track[-1][0], NOW)
        self.assertTrue(track[0][0] < occurred_at < NOW)
