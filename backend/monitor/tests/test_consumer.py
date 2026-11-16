from channels.auth import AuthMiddlewareStack
from channels.routing import URLRouter
from channels.security.websocket import AllowedHostsOriginValidator
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.contrib.auth.models import User
from django.test import Client, TransactionTestCase

from monitor import broadcast
from monitor.consumers import CLOSE_UNAUTHENTICATED
from monitor.routing import websocket_urlpatterns
from monitor.service import monitor

from .helpers import CNA, QUERY, make_aircraft, make_snapshot

application = AllowedHostsOriginValidator(AuthMiddlewareStack(URLRouter(websocket_urlpatterns)))


class ConsumerTests(TransactionTestCase):
    def setUp(self):
        monitor.state.aircraft.clear()
        monitor.state.inside.clear()
        monitor.state.update(make_snapshot(make_aircraft(dst=1)), CNA, QUERY)
        self.user = User.objects.create_user("operador", password="clave-de-prueba-123")

    def tearDown(self):
        monitor.state.aircraft.clear()
        monitor.state.inside.clear()

    def session_cookie(self, user):
        client = Client()
        client.force_login(user)
        return client.cookies[settings.SESSION_COOKIE_NAME].value

    def communicator(self, cookie=None, origin="http://testserver"):
        headers = [(b"origin", origin.encode())]
        if cookie:
            headers.append((b"cookie", f"{settings.SESSION_COOKIE_NAME}={cookie}".encode()))
        return WebsocketCommunicator(application, "/ws/aircraft/", headers=headers)

    async def test_anonymous_is_rejected(self):
        comm = self.communicator()
        connected, code = await comm.connect()
        self.assertFalse(connected)
        self.assertEqual(code, CLOSE_UNAUTHENTICATED)

    async def test_foreign_origin_is_rejected(self):
        cookie = await self.async_session_cookie()
        connected, _ = await self.communicator(cookie, origin="http://evil.example").connect()
        self.assertFalse(connected)

    async def test_inactive_user_is_rejected(self):
        cookie = await self.async_session_cookie()
        self.user.is_active = False
        await self.user.asave()
        connected, _ = await self.communicator(cookie).connect()
        self.assertFalse(connected)

    async def test_snapshot_then_broadcasts(self):
        comm = self.communicator(await self.async_session_cookie())
        connected, _ = await comm.connect()
        self.assertTrue(connected)

        snapshot = await comm.receive_json_from()
        self.assertEqual(snapshot["type"], "snapshot")
        self.assertEqual([a["hex"] for a in snapshot["aircraft"]], ["e0b390"])
        self.assertTrue(snapshot["aircraft"][0]["inside"])
        self.assertEqual(snapshot["config"]["zone"]["name"], "ALERTA CNA 10KM")
        self.assertNotIn("telegram_bot_token", str(snapshot))

        await broadcast.send_alert({"label": "ARG1309"})
        self.assertEqual(await comm.receive_json_from(), {"type": "alert", "alert": {"label": "ARG1309"}})

        await broadcast.send_aircraft([], {"ok": True})
        message = await comm.receive_json_from()
        self.assertEqual(message["type"], "aircraft")
        await comm.disconnect()

    async def test_user_can_be_disconnected(self):
        comm = self.communicator(await self.async_session_cookie())
        await comm.connect()
        await comm.receive_json_from()
        await broadcast.get_channel_layer().group_send(broadcast.user_group(self.user.pk), {"type": "session.close"})
        output = await comm.receive_output()
        self.assertEqual(output, {"type": "websocket.close", "code": CLOSE_UNAUTHENTICATED})

    def guest_cookie(self, enabled=True):
        from configuration.models import SystemConfig

        config = SystemConfig.load()
        config.guest_access = enabled
        config.save()
        client = Client(enforce_csrf_checks=False)
        with self.settings(GUEST_ACCESS_ALLOWED=True):
            self.assertEqual(client.post("/api/auth/guest/").status_code, 200 if enabled else 403)
        return client.cookies[settings.SESSION_COOKIE_NAME].value if enabled else None

    async def test_guest_can_watch_and_is_cut_off(self):
        from asgiref.sync import sync_to_async

        cookie = await sync_to_async(self.guest_cookie)()
        with self.settings(GUEST_ACCESS_ALLOWED=True):
            comm = self.communicator(cookie)
            connected, _ = await comm.connect()
            self.assertTrue(connected)
            self.assertEqual((await comm.receive_json_from())["type"], "snapshot")
            await sync_to_async(broadcast.disconnect_guests_sync)()
            output = await comm.receive_output()
            self.assertEqual(output, {"type": "websocket.close", "code": CLOSE_UNAUTHENTICATED})

    async def test_guest_rejected_when_env_forbids_it(self):
        from asgiref.sync import sync_to_async

        cookie = await sync_to_async(self.guest_cookie)()
        with self.settings(GUEST_ACCESS_ALLOWED=False):
            connected, code = await self.communicator(cookie).connect()
            self.assertFalse(connected)
            self.assertEqual(code, CLOSE_UNAUTHENTICATED)

    async def async_session_cookie(self):
        from asgiref.sync import sync_to_async

        return await sync_to_async(self.session_cookie)(self.user)
