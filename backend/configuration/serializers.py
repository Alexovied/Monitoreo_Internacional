import copy

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from .models import SystemConfig, telegram_token_validator


def mask_token(token):
    return f"••••••••{token[-4:]}" if token else ""


class SystemConfigSerializer(serializers.ModelSerializer):
    """Configuración para el administrador.

    El token del bot es de solo escritura: se devuelve enmascarado. Si no se
    envía, se conserva el guardado; si se envía vacío, se borra.
    """

    telegram_bot_token = serializers.CharField(
        write_only=True, required=False, allow_blank=True, max_length=100
    )
    telegram_bot_token_masked = serializers.SerializerMethodField()
    telegram_enabled = serializers.BooleanField(read_only=True)
    # Lo que permite el .env (GUEST_ACCESS_ENABLED) y si hoy se puede entrar.
    guest_access_allowed = serializers.SerializerMethodField()
    guest_access_enabled = serializers.BooleanField(read_only=True)

    class Meta:
        model = SystemConfig
        fields = [
            "zone_name", "zone_lat", "zone_lon", "zone_radius_nm", "zone_floor_m", "zone_ceiling_m",
            "poll_interval_s", "query_radius_nm",
            "telegram_bot_token", "telegram_bot_token_masked", "telegram_chat_id", "telegram_enabled",
            "guest_access", "guest_access_allowed", "guest_access_enabled",
            "temp_alert_enabled", "temp_alert_threshold_c",
            "maintenance_message", "updated_at",
        ]
        # El aviso se cambia solo con /config/maintenance/ (puede avisar por Telegram).
        read_only_fields = ["maintenance_message", "updated_at"]

    def get_telegram_bot_token_masked(self, config):
        return mask_token(config.telegram_bot_token)

    def get_guest_access_allowed(self, config):
        return settings.GUEST_ACCESS_ALLOWED

    def validate_guest_access(self, value):
        # Apagarlo siempre se puede; encenderlo solo si el .env lo permite.
        if value and not settings.GUEST_ACCESS_ALLOWED and not (self.instance and self.instance.guest_access):
            raise serializers.ValidationError(
                "El acceso de visitantes está desactivado en el servidor: "
                "primero hay que poner GUEST_ACCESS_ENABLED=true en el .env."
            )
        return value

    def validate(self, attrs):
        for key in ("telegram_bot_token", "telegram_chat_id", "zone_name"):
            if isinstance(attrs.get(key), str):
                attrs[key] = attrs[key].strip()
        # Validación completa del modelo (incluye las reglas entre campos)
        # sobre una copia con los cambios aplicados.
        candidate = copy.copy(self.instance)
        for field, value in attrs.items():
            setattr(candidate, field, value)
        try:
            candidate.full_clean()
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.message_dict)
        return attrs


class OpenAirUploadSerializer(serializers.Serializer):
    file = serializers.FileField()

    MAX_SIZE = 64 * 1024

    def validate_file(self, uploaded):
        if uploaded.size > self.MAX_SIZE:
            raise serializers.ValidationError("El archivo es demasiado grande (máximo 64 KB).")
        return uploaded


class MaintenanceSerializer(serializers.Serializer):
    message = serializers.CharField(max_length=300, allow_blank=True, trim_whitespace=True)
    notify_telegram = serializers.BooleanField(default=False)


class TelegramChatsSerializer(serializers.Serializer):
    # Opcional: el token recién escrito, antes de guardarlo. Vacío = el guardado.
    telegram_bot_token = serializers.CharField(required=False, allow_blank=True, max_length=100)

    def validate_telegram_bot_token(self, value):
        value = value.strip()
        if value:
            try:
                telegram_token_validator(value)
            except DjangoValidationError as exc:
                raise serializers.ValidationError(exc.messages)
        return value
