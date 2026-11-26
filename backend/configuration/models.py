from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models

from monitor import exclusions
from monitor.adsbfi import MAX_RADIUS_NM as ADSBFI_MAX_RADIUS_NM

# adsb.fi admite 1 consulta por segundo; 2 s deja margen para demoras y
# reintentos (los 429 cuentan para el bloqueo temporal de la IP) y coincide
# con la separación mínima entre consultas del monitor (MIN_REQUEST_GAP_S).
MIN_POLL_INTERVAL_S = 2

# Límites para la temperatura del aviso de calor del equipo (Raspberry Pi:
# baja la velocidad a 80 °C y se apaga a 85 °C).
MIN_TEMP_ALERT_C = 40
MAX_TEMP_ALERT_C = 95


def validate_positive(value):
    if value is None or value <= 0:
        raise ValidationError("Debe ser mayor que 0.")


telegram_token_validator = RegexValidator(
    r"^\d+:[A-Za-z0-9_-]+$",
    "El token debe tener el formato <número>:<clave>, tal como lo entrega @BotFather.",
)
telegram_chat_id_validator = RegexValidator(
    r"^-?\d+$",
    "El chat id debe ser numérico (los grupos tienen id negativo, p. ej. -1001234567890).",
)


def validate_excluded_aircraft(value):
    """Devuelve el mensaje de error, o None si la lista es válida."""
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return "Tiene que ser una lista de códigos o matrículas."
    if len(value) > exclusions.MAX_EXCLUDED:
        return f"Como máximo {exclusions.MAX_EXCLUDED} aeronaves."
    invalid = [item for item in value if item != exclusions.normalize(item) or not exclusions.is_valid(item)]
    if invalid:
        return (
            f"No parece un código ICAO (6 caracteres, p. ej. E0B390) ni una matrícula (p. ej. PR-ABC): "
            f"{', '.join(invalid)}"
        )
    return None


class SystemConfig(models.Model):
    """Configuración editable desde la web. Hay una sola fila (pk=1)."""

    SINGLETON_PK = 1

    zone_name = models.CharField("nombre de la zona", max_length=100)
    zone_lat = models.FloatField(
        "latitud del centro",
        validators=[MinValueValidator(-90), MaxValueValidator(90)],
    )
    zone_lon = models.FloatField(
        "longitud del centro",
        validators=[MinValueValidator(-180), MaxValueValidator(180)],
    )
    zone_radius_nm = models.FloatField(
        "radio de la zona (NM)",
        validators=[validate_positive, MaxValueValidator(ADSBFI_MAX_RADIUS_NM)],
    )
    # 0 significa "superficie": sin límite inferior de altitud.
    zone_floor_m = models.FloatField(
        "altura inferior (m)",
        default=0,
        validators=[MinValueValidator(0)],
    )
    zone_ceiling_m = models.FloatField(
        "altura superior (m)",
        validators=[validate_positive],
    )
    poll_interval_s = models.PositiveIntegerField(
        "frecuencia de actualización (s)",
        validators=[MinValueValidator(MIN_POLL_INTERVAL_S)],
    )
    query_radius_nm = models.FloatField(
        "radio de la consulta y del mapa (NM)",
        validators=[validate_positive, MaxValueValidator(ADSBFI_MAX_RADIUS_NM)],
    )
    telegram_bot_token = models.CharField(
        "token del bot de Telegram",
        max_length=100,
        blank=True,
        validators=[telegram_token_validator],
    )
    telegram_chat_id = models.CharField(
        "chat id del grupo de Telegram",
        max_length=32,
        blank=True,
        validators=[telegram_chat_id_validator],
    )
    # Aeronaves que no generan alerta (código ICAO hex o matrícula), p. ej. los
    # aviones propios de un aeródromo. Se siguen viendo en el mapa.
    excluded_aircraft = models.JSONField("aeronaves que no generan alerta", default=list, blank=True)
    # Aviso de mantenimiento para todos los usuarios; vacío = sin aviso.
    maintenance_message = models.CharField("aviso de mantenimiento", max_length=300, blank=True, default="")
    # Botón «Entrar como visitante» en el login. Solo vale si además el .env
    # lo permite (GUEST_ACCESS_ENABLED); ver guest_access_enabled.
    guest_access = models.BooleanField("acceso de visitantes", default=False)
    # Aviso por Telegram cuando el equipo se calienta (ver monitor.system).
    temp_alert_enabled = models.BooleanField("aviso de temperatura", default=False)
    temp_alert_threshold_c = models.FloatField(
        "temperatura límite (°C)",
        default=75,
        validators=[MinValueValidator(MIN_TEMP_ALERT_C), MaxValueValidator(MAX_TEMP_ALERT_C)],
    )
    updated_at = models.DateTimeField("última modificación", auto_now=True)

    class Meta:
        verbose_name = "configuración"
        verbose_name_plural = "configuración"

    def __str__(self):
        return f"Configuración ({self.zone_name})"

    @property
    def zone_is_surface(self):
        return self.zone_floor_m == 0

    @property
    def telegram_enabled(self):
        return bool(self.telegram_bot_token and self.telegram_chat_id)

    @property
    def guest_access_enabled(self):
        """Si ahora se puede entrar como visitante: lo permiten el .env y la configuración."""
        return settings.GUEST_ACCESS_ALLOWED and self.guest_access

    def clean(self):
        errors = {}
        if (
            self.zone_ceiling_m is not None
            and self.zone_floor_m is not None
            and self.zone_ceiling_m <= self.zone_floor_m
        ):
            errors["zone_ceiling_m"] = "La altura superior debe ser mayor que la inferior."
        if (
            self.query_radius_nm is not None
            and self.zone_radius_nm is not None
            and self.query_radius_nm < self.zone_radius_nm
        ):
            errors["query_radius_nm"] = "El radio de la consulta no puede ser menor que el radio de la zona."
        if bool(self.telegram_bot_token) != bool(self.telegram_chat_id):
            field = "telegram_chat_id" if self.telegram_bot_token else "telegram_bot_token"
            errors[field] = "El token y el chat id de Telegram se configuran juntos (o ambos vacíos)."
        excluded_error = validate_excluded_aircraft(self.excluded_aircraft)
        if excluded_error:
            errors["excluded_aircraft"] = excluded_error
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.pk = self.SINGLETON_PK
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("La configuración no se puede borrar.")

    @classmethod
    def load(cls):
        """Devuelve la configuración; la crea desde el .env si todavía no existe."""
        try:
            return cls.objects.get(pk=cls.SINGLETON_PK)
        except cls.DoesNotExist:
            pass
        initial = cls(pk=cls.SINGLETON_PK, **settings.INITIAL_CONFIG)
        try:
            initial.full_clean()
        except ValidationError as exc:
            raise ImproperlyConfigured(
                f"Los valores iniciales de configuración del .env no son válidos: {exc.message_dict}"
            ) from exc
        obj, _ = cls.objects.get_or_create(
            pk=cls.SINGLETON_PK,
            defaults=settings.INITIAL_CONFIG,
        )
        return obj
