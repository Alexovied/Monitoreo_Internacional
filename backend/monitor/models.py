from django.db import models

from . import behavior


class Incident(models.Model):
    """Algo que pasó y conviene poder volver a mirar.

    Un ingreso a la zona, un comportamiento raro (ver ``behavior``) o un
    avistamiento sin transponder cargado desde el Modo cielo. Guarda el
    recorrido de la aeronave para la repetición en 3D.
    """

    KIND_CHOICES = [(kind, title) for kind, title in behavior.KIND_TITLES.items()]

    kind = models.CharField("tipo", max_length=20, choices=KIND_CHOICES)
    occurred_at = models.FloatField("hora (s Unix)", db_index=True)
    hex = models.CharField("código ICAO", max_length=10, blank=True, default="")
    label = models.CharField("identificación", max_length=40, blank=True, default="")
    registration = models.CharField("matrícula", max_length=20, blank=True, default="")
    aircraft_type = models.CharField("tipo de aeronave", max_length=10, blank=True, default="")
    category = models.CharField("categoría ADS-B", max_length=4, blank=True, default="")
    summary = models.CharField("resumen", max_length=250)
    # Zona vigente en ese momento: {name, lat, lon, radius_nm, floor_m, ceiling_m}.
    zone = models.JSONField("zona", default=dict)
    # Datos del aviso (los mismos que van al navegador) y el detalle de la señal.
    data = models.JSONField("datos", default=dict)
    # [[hora, lat, lon, altitud ft | "ground" | null, kt, rumbo], ...]
    track = models.JSONField("recorrido", default=list)
    closed = models.BooleanField("recorrido completo", default=False)
    simulated = models.BooleanField("simulacro", default=False)
    photo = models.BinaryField("foto (JPEG)", null=True, blank=True, editable=False)
    # Repetidos acá para listar sin leer el recorrido ni la foto, que pesan.
    points = models.PositiveIntegerField("puntos del recorrido", default=0)
    has_photo = models.BooleanField("tiene foto", default=False)

    class Meta:
        ordering = ["-occurred_at", "-id"]
        verbose_name = "incidente"

    def save(self, *args, **kwargs):
        self.points = len(self.track)
        self.has_photo = self.photo is not None
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.get_kind_display()}: {self.label or self.hex or self.pk}"

    def to_summary(self):
        """Lo que se lista y lo que se difunde a los navegadores (sin el recorrido)."""
        return {
            "id": self.pk,
            "kind": self.kind,
            "title": behavior.KIND_TITLES.get(self.kind, self.kind),
            "time": self.occurred_at,
            "hex": self.hex,
            "label": self.label,
            "registration": self.registration,
            "type": self.aircraft_type,
            "category": self.category,
            "summary": self.summary,
            "zone": self.zone.get("name", ""),
            "simulated": self.simulated,
            "points": self.points,
            "closed": self.closed,
            "has_photo": self.has_photo,
        }

    def to_detail(self):
        return {**self.to_summary(), "zone": self.zone, "data": self.data, "track": self.track}
