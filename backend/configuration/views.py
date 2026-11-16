from asgiref.sync import async_to_sync
from rest_framework import status
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsAdmin
from monitor.broadcast import send_config_sync
from monitor.openair import OpenAirError, decode_openair_bytes, parse_openair
from monitor.service import monitor
from monitor.telegram import TelegramError, find_group_chats, send_maintenance, send_test_message

from .models import SystemConfig
from .serializers import (
    MaintenanceSerializer,
    OpenAirUploadSerializer,
    SystemConfigSerializer,
    TelegramChatsSerializer,
)


class ConfigView(APIView):
    """GET / PUT / PATCH de la configuración. Solo administradores."""

    permission_classes = [IsAdmin]

    def get(self, request):
        return Response(SystemConfigSerializer(SystemConfig.load()).data)

    def put(self, request):
        return self._update(request, partial=False)

    def patch(self, request):
        return self._update(request, partial=True)

    def _update(self, request, partial):
        serializer = SystemConfigSerializer(SystemConfig.load(), data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        config = serializer.save()
        # Los navegadores se actualizan sin recargar y el monitor consulta ya
        # con la zona nueva, sin esperar al próximo ciclo.
        send_config_sync(config)
        monitor.request_refresh()
        return Response(SystemConfigSerializer(config).data)


class OpenAirView(APIView):
    """Lee un archivo OpenAir y devuelve los campos de la zona, SIN guardarlos.

    El frontend los carga en el formulario y el administrador los guarda.
    """

    permission_classes = [IsAdmin]
    parser_classes = [MultiPartParser]

    def post(self, request):
        upload = OpenAirUploadSerializer(data=request.data)
        upload.is_valid(raise_exception=True)
        text = decode_openair_bytes(upload.validated_data["file"].read())
        try:
            zone = parse_openair(text)
        except OpenAirError as exc:
            return Response({"file": [str(exc)]}, status=status.HTTP_400_BAD_REQUEST)
        return Response(zone.as_config_fields())


class TelegramTestView(APIView):
    """Envía un mensaje de prueba al grupo con la configuración guardada."""

    permission_classes = [IsAdmin]

    def post(self, request):
        try:
            title, with_map = async_to_sync(send_test_message)(SystemConfig.load())
        except TelegramError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        detail = f"Mensaje de prueba enviado al grupo «{title}»"
        detail += " con el mapa." if with_map else " (sin mapa: no se pudo armar la imagen, ver el log)."
        return Response({"detail": detail, "with_map": with_map})


class MaintenanceView(APIView):
    """Publica o quita el aviso de mantenimiento y, si se pide, lo manda al grupo."""

    permission_classes = [IsAdmin]

    def post(self, request):
        data = MaintenanceSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        config = SystemConfig.load()
        config.maintenance_message = data.validated_data["message"]
        config.save(update_fields=["maintenance_message", "updated_at"])
        send_config_sync(config)

        telegram = None  # None: no se pidió; True: enviado; texto: el error
        if data.validated_data["notify_telegram"]:
            try:
                async_to_sync(send_maintenance)(config)
                telegram = True
            except TelegramError as exc:
                telegram = str(exc)
        body = SystemConfigSerializer(config).data
        body["telegram_result"] = telegram
        return Response(body)


class TelegramChatsView(APIView):
    """Lista los grupos donde está el bot, para elegir el chat id sin buscarlo a mano."""

    permission_classes = [IsAdmin]

    def post(self, request):
        data = TelegramChatsSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        token = data.validated_data.get("telegram_bot_token") or SystemConfig.load().telegram_bot_token
        if not token:
            return Response({"detail": "Primero escribí el token del bot."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            chats = async_to_sync(find_group_chats)(token)
        except TelegramError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"chats": chats})
