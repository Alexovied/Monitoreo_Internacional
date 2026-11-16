from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from accounts.guest import is_guest_session
from configuration.models import SystemConfig

from . import broadcast
from .service import monitor

# Código de cierre propio (rango 4000-4999) para "sin sesión".
CLOSE_UNAUTHENTICATED = 4401


class AircraftConsumer(AsyncJsonWebsocketConsumer):
    """Envía al navegador las aeronaves, las alertas y los cambios de configuración."""

    groups = []

    async def connect(self):
        user = self.scope.get("user")
        if user is not None and user.is_authenticated and user.is_active:
            self.user_group = broadcast.user_group(user.pk)
        elif await self.is_guest():
            # Visitante: solo mira. Se lo desconecta si se apaga el acceso.
            self.user_group = broadcast.GUEST_GROUP
        else:
            await self.close(code=CLOSE_UNAUTHENTICATED)
            return
        await self.channel_layer.group_add(broadcast.GROUP, self.channel_name)
        await self.channel_layer.group_add(self.user_group, self.channel_name)
        await self.accept()

        config = await database_sync_to_async(SystemConfig.load)()
        await self.send_json(
            {
                "type": "snapshot",
                "aircraft": monitor.state.payload(),
                "config": broadcast.public_config(config),
                "status": monitor.status,
            }
        )

    async def is_guest(self):
        session = self.scope.get("session")
        if session is None:
            return False
        return await database_sync_to_async(is_guest_session)(session)

    async def disconnect(self, code):
        await self.channel_layer.group_discard(broadcast.GROUP, self.channel_name)
        if hasattr(self, "user_group"):
            await self.channel_layer.group_discard(self.user_group, self.channel_name)

    async def receive_json(self, content, **kwargs):
        # El canal es solo de servidor a navegador.
        pass

    async def monitor_event(self, message):
        await self.send_json(message["event"])

    async def session_close(self, message):
        await self.close(code=CLOSE_UNAUTHENTICATED)
