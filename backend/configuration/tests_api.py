from unittest import mock

from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.roles import ADMIN_GROUP, USER_GROUP
from monitor.telegram import TelegramError

from .models import SystemConfig
from .tests import VALID

TOKEN = "123456789:AAH-secreto_del_bot_abcd"

CNA_FILE = b"""* Alerta Ca\xf1uelas CNA
AC R
AY ALERT
AN ALERTA CNA 10KM
AL PISO
AH 4400m
V X=35:06:43 S 058:43:34 W
DC 5.4
*
"""


@override_settings(INITIAL_CONFIG={**VALID, "telegram_bot_token": TOKEN, "telegram_chat_id": "-1001234567890"})
class ConfigApiTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user("jefe", password="x")
        self.admin.groups.add(Group.objects.get(name=ADMIN_GROUP))
        self.user = User.objects.create_user("piloto", password="x")
        self.user.groups.add(Group.objects.get(name=USER_GROUP))
        self.client = APIClient()
        self.client.force_authenticate(self.admin)
        patcher = mock.patch("configuration.views.send_config_sync")
        self.send_config = patcher.start()
        self.addCleanup(patcher.stop)

    def test_registered_user_is_rejected(self):
        client = APIClient()
        client.force_authenticate(self.user)
        for method, url in (("get", "/api/config/"), ("patch", "/api/config/"), ("put", "/api/config/"),
                            ("post", "/api/config/openair/"), ("post", "/api/config/telegram-test/"),
                            ("post", "/api/config/maintenance/"), ("post", "/api/config/telegram-chats/")):
            with self.subTest(method=method, url=url):
                self.assertEqual(getattr(client, method)(url, {}).status_code, 403)

    def test_get_masks_token(self):
        data = self.client.get("/api/config/").json()
        self.assertEqual(data["zone_name"], "ALERTA CNA 10KM")
        self.assertEqual(data["telegram_bot_token_masked"], "••••••••abcd")
        self.assertNotIn("secreto", str(data))
        self.assertNotIn("telegram_bot_token", data)
        self.assertTrue(data["telegram_enabled"])

    def test_patch_excluded_aircraft_is_normalized(self):
        with mock.patch("configuration.views.monitor"):
            response = self.client.patch(
                "/api/config/", {"excluded_aircraft": [" e0b390 ", "pr-abc", "E0B390", ""]}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["excluded_aircraft"], ["E0B390", "PR-ABC"])
        self.assertEqual(SystemConfig.load().excluded_aircraft, ["E0B390", "PR-ABC"])

    def test_patch_excluded_aircraft_rejects_invalid(self):
        response = self.client.patch("/api/config/", {"excluded_aircraft": ["E0B390", "#$%"]}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("#$%", str(response.json()["excluded_aircraft"]))
        self.assertEqual(SystemConfig.load().excluded_aircraft, [])

    def test_patch_updates_broadcasts_and_refreshes(self):
        with mock.patch("configuration.views.monitor") as monitor:
            response = self.client.patch("/api/config/", {"poll_interval_s": 10, "zone_name": " NUEVA "}, format="json")
        self.assertEqual(response.status_code, 200, response.json())
        config = SystemConfig.load()
        self.assertEqual(config.poll_interval_s, 10)
        self.assertEqual(config.zone_name, "NUEVA")
        self.assertEqual(config.telegram_bot_token, TOKEN)  # sin enviar el token, se conserva
        self.send_config.assert_called_once()
        monitor.request_refresh.assert_called_once()

    def test_validation_errors(self):
        cases = {
            "zone_lat": {"zone_lat": -95},
            "zone_lon": {"zone_lon": 200},
            "zone_radius_nm": {"zone_radius_nm": 0},
            "zone_ceiling_m": {"zone_floor_m": 5000},
            "poll_interval_s": {"poll_interval_s": 1},
            "query_radius_nm": {"query_radius_nm": 300},
            "telegram_chat_id": {"telegram_chat_id": "grupo"},
            "telegram_bot_token": {"telegram_bot_token": "no-es-token"},
        }
        for field, payload in cases.items():
            with self.subTest(field=field):
                response = self.client.patch("/api/config/", payload, format="json")
                self.assertEqual(response.status_code, 400)
                self.assertIn(field, response.json())
        query = self.client.patch("/api/config/", {"zone_radius_nm": 60}, format="json")
        self.assertIn("query_radius_nm", query.json())
        self.send_config.assert_not_called()
        self.assertEqual(SystemConfig.load().poll_interval_s, 5)

    def test_replace_and_clear_telegram(self):
        self.client.patch("/api/config/", {"telegram_bot_token": "999:NUEVO_token-wxyz"}, format="json")
        self.assertEqual(SystemConfig.load().telegram_bot_token, "999:NUEVO_token-wxyz")
        response = self.client.patch("/api/config/", {"telegram_bot_token": "", "telegram_chat_id": ""}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["telegram_enabled"])
        response = self.client.patch("/api/config/", {"telegram_chat_id": "-100123"}, format="json")
        self.assertIn("telegram_bot_token", response.json())

    def test_openair_parses_without_saving(self):
        self.client.patch("/api/config/", {"zone_name": "OTRA", "zone_radius_nm": 2}, format="json")
        upload = SimpleUploadedFile("cna.txt", CNA_FILE, content_type="text/plain")
        response = self.client.post("/api/config/openair/", {"file": upload}, format="multipart")
        self.assertEqual(response.status_code, 200, response.json())
        data = response.json()
        self.assertEqual(data["zone_name"], "ALERTA CNA 10KM")
        self.assertEqual(data["zone_lat"], -35.111944)  # redondeado a 6 decimales
        self.assertEqual(data["zone_lon"], -58.726111)
        self.assertEqual(data["zone_ceiling_m"], 4400.0)
        self.assertEqual(SystemConfig.load().zone_name, "OTRA")

    def test_openair_errors(self):
        upload = SimpleUploadedFile("mal.txt", b"AC R\nDP 35:00:00 S 058:00:00 W\n")
        response = self.client.post("/api/config/openair/", {"file": upload}, format="multipart")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Línea 2", response.json()["file"][0])
        big = SimpleUploadedFile("grande.txt", b"*" * (65 * 1024))
        self.assertEqual(self.client.post("/api/config/openair/", {"file": big}, format="multipart").status_code, 400)

    def test_telegram_test_endpoint(self):
        with mock.patch("configuration.views.send_test_message", mock.AsyncMock(return_value=("avion", True))):
            response = self.client.post("/api/config/telegram-test/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("avion", response.json()["detail"])
        self.assertIn("con el mapa", response.json()["detail"])
        with mock.patch("configuration.views.send_test_message", mock.AsyncMock(return_value=("avion", False))):
            response = self.client.post("/api/config/telegram-test/")
        self.assertIn("sin mapa", response.json()["detail"])
        error = TelegramError("Telegram rechazó el mensaje: Unauthorized — el token del bot no es válido.")
        with mock.patch("configuration.views.send_test_message", mock.AsyncMock(side_effect=error)):
            response = self.client.post("/api/config/telegram-test/")
        self.assertEqual(response.status_code, 400)
        self.assertIn("token", response.json()["detail"])

    def test_maintenance_publish_and_clear(self):
        sent = mock.AsyncMock()
        with mock.patch("configuration.views.send_maintenance", sent):
            response = self.client.post("/api/config/maintenance/", {"message": "  Corte el lunes 10 h  "}, format="json")
            self.assertEqual(response.status_code, 200, response.json())
            self.assertEqual(response.json()["maintenance_message"], "Corte el lunes 10 h")
            self.assertIsNone(response.json()["telegram_result"])
            sent.assert_not_called()
            self.send_config.assert_called_once()

            response = self.client.post("/api/config/maintenance/", {"message": "", "notify_telegram": True}, format="json")
        self.assertEqual(response.json()["maintenance_message"], "")
        self.assertIs(response.json()["telegram_result"], True)
        sent.assert_awaited_once()
        # PATCH general no puede tocar el aviso.
        self.client.patch("/api/config/", {"maintenance_message": "x"}, format="json")
        self.assertEqual(SystemConfig.load().maintenance_message, "")

    def test_maintenance_telegram_error_still_saves(self):
        error = TelegramError("Telegram rechazó el mensaje: Forbidden")
        with mock.patch("configuration.views.send_maintenance", mock.AsyncMock(side_effect=error)):
            response = self.client.post("/api/config/maintenance/", {"message": "Hoy", "notify_telegram": True},
                                        format="json")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Forbidden", response.json()["telegram_result"])
        self.assertEqual(SystemConfig.load().maintenance_message, "Hoy")
        long = self.client.post("/api/config/maintenance/", {"message": "x" * 301}, format="json")
        self.assertEqual(long.status_code, 400)

    def test_telegram_chats_uses_new_or_saved_token(self):
        find = mock.AsyncMock(return_value=[{"id": "-100555", "title": "Pilotos"}])
        with mock.patch("configuration.views.find_group_chats", find):
            response = self.client.post("/api/config/telegram-chats/", {}, format="json")
            self.assertEqual(response.json(), {"chats": [{"id": "-100555", "title": "Pilotos"}]})
            find.assert_awaited_with(TOKEN)
            self.client.post("/api/config/telegram-chats/", {"telegram_bot_token": " 42:otro "}, format="json")
            find.assert_awaited_with("42:otro")
            bad = self.client.post("/api/config/telegram-chats/", {"telegram_bot_token": "malo"}, format="json")
        self.assertEqual(bad.status_code, 400)
        error = TelegramError("El token del bot no es válido.")
        with mock.patch("configuration.views.find_group_chats", mock.AsyncMock(side_effect=error)):
            response = self.client.post("/api/config/telegram-chats/", {}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("token", response.json()["detail"])


@override_settings(INITIAL_CONFIG=VALID)
class GuestAccessConfigTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user("jefe", password="x")
        self.admin.groups.add(Group.objects.get(name=ADMIN_GROUP))
        self.client = APIClient()
        self.client.force_authenticate(self.admin)
        for target in ("configuration.views.send_config_sync", "configuration.views.disconnect_guests_sync"):
            patcher = mock.patch(target)
            setattr(self, target.rsplit(".", 1)[1], patcher.start())
            self.addCleanup(patcher.stop)

    def test_cannot_enable_when_env_forbids_it(self):
        with self.settings(GUEST_ACCESS_ALLOWED=False):
            data = self.client.get("/api/config/").json()
            self.assertFalse(data["guest_access_allowed"])
            response = self.client.patch("/api/config/", {"guest_access": True}, format="json")
            self.assertEqual(response.status_code, 400)
            self.assertIn("guest_access", response.json())
            self.assertFalse(SystemConfig.load().guest_access)

    def test_enable_and_disable(self):
        with self.settings(GUEST_ACCESS_ALLOWED=True):
            response = self.client.patch("/api/config/", {"guest_access": True}, format="json")
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()["guest_access_enabled"])
            self.disconnect_guests_sync.assert_not_called()

            response = self.client.patch("/api/config/", {"guest_access": False}, format="json")
            self.assertFalse(response.json()["guest_access_enabled"])
            self.disconnect_guests_sync.assert_called_once()

    def test_stale_value_does_not_block_other_changes(self):
        # Estaba activado y después se apagó el .env: se puede seguir guardando lo demás.
        config = SystemConfig.load()
        config.guest_access = True
        config.save()
        with self.settings(GUEST_ACCESS_ALLOWED=False):
            response = self.client.patch("/api/config/", {"zone_name": "OTRA", "guest_access": True}, format="json")
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.json()["guest_access_enabled"])

    def test_regular_user_cannot_change_it(self):
        user = User.objects.create_user("piloto", password="x")
        client = APIClient()
        client.force_authenticate(user)
        self.assertEqual(client.patch("/api/config/", {"guest_access": True}, format="json").status_code, 403)


@override_settings(INITIAL_CONFIG=VALID)
class SystemInfoApiTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user("jefe", password="x")
        self.admin.groups.add(Group.objects.get(name=ADMIN_GROUP))
        self.user = User.objects.create_user("piloto", password="x")
        self.user.groups.add(Group.objects.get(name=USER_GROUP))

    def test_admin_only(self):
        client = APIClient()
        client.force_authenticate(self.user)
        self.assertEqual(client.get("/api/system/").status_code, 403)
        self.assertEqual(client.get("/api/system/version/").status_code, 403)

    def test_returns_info_and_alert_settings(self):
        client = APIClient()
        client.force_authenticate(self.admin)
        data = client.get("/api/system/").json()
        self.assertIn("temperature_c", data)
        self.assertIn("percent", data["disk"])
        self.assertEqual(data["temp_alert"], {"enabled": False, "threshold_c": 75, "telegram_enabled": False})

    def test_threshold_limits(self):
        client = APIClient()
        client.force_authenticate(self.admin)
        with mock.patch("configuration.views.send_config_sync"), mock.patch("configuration.views.monitor"):
            self.assertEqual(client.patch("/api/config/", {"temp_alert_threshold_c": 30}, format="json").status_code, 400)
            response = client.patch("/api/config/", {"temp_alert_enabled": True, "temp_alert_threshold_c": 70},
                                    format="json")
        self.assertEqual(response.status_code, 200, response.json())
        config = SystemConfig.load()
        self.assertTrue(config.temp_alert_enabled)
        self.assertEqual(config.temp_alert_threshold_c, 70)

    def test_version_endpoint(self):
        client = APIClient()
        client.force_authenticate(self.admin)
        info = {"sha": "a" * 40, "branch": "main", "repo_url": "https://github.com/x/y", "updates": None}
        with mock.patch("configuration.views.version.version_info", return_value=info):
            self.assertEqual(client.get("/api/system/version/").json(), info)
