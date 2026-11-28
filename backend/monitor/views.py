"""API del registro de incidentes y de los avistamientos del Modo cielo."""

import base64
import binascii
import io
import time
from html import escape

from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from PIL import Image, UnidentifiedImageError
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from accounts.permissions import IsAdmin
from accounts.roles import is_admin
from configuration.models import SystemConfig

from . import behavior, broadcast, records, sightings, simulate
from .alerts import alert_clock, public_base_url
from .models import Incident
from .service import monitor
from .zone import Zone

LIST_LIMIT = 200
PHOTO_MAX_BYTES = 900 * 1024  # lo que llega (base64 ya decodificado)
PHOTO_MAX_SIDE = 1280
TRAFFIC_LIMIT = 60  # aeronaves que se guardan con un avistamiento

_COMPASS = ["N", "NE", "E", "SE", "S", "SO", "O", "NO"]


class IncidentListView(APIView):
    """Últimos incidentes, del más nuevo al más viejo (sin el recorrido)."""

    def get(self, request):
        queryset = Incident.objects.all()
        kind = request.query_params.get("kind")
        if kind:
            queryset = queryset.filter(kind=kind)
        # El recorrido y la foto pesan y la lista no los necesita.
        queryset = queryset.defer("track", "photo", "data")
        return Response([incident.to_summary() for incident in queryset[:LIST_LIMIT]])


class IncidentDetailView(APIView):
    def get(self, request, pk):
        return Response(get_object_or_404(Incident, pk=pk).to_detail())

    def delete(self, request, pk):
        if not is_admin(request.user):
            return Response({"detail": IsAdmin.message}, status=status.HTTP_403_FORBIDDEN)
        get_object_or_404(Incident, pk=pk).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class IncidentPhotoView(APIView):
    def get(self, request, pk):
        incident = get_object_or_404(Incident, pk=pk)
        if not incident.has_photo:
            return Response({"detail": "Este incidente no tiene foto."}, status=status.HTTP_404_NOT_FOUND)
        response = HttpResponse(bytes(incident.photo), content_type="image/jpeg")
        response["Cache-Control"] = "private, max-age=86400"
        return response


class SimulateSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=sorted(simulate.PROFILES))


class SimulateView(APIView):
    """Crea un incidente de mentira (marcado como simulacro). Solo administradores."""

    permission_classes = [IsAdmin]

    def post(self, request):
        serializer = SimulateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        kind = serializer.validated_data["kind"]
        zone = Zone.from_config(SystemConfig.load())
        alert, detail, track, occurred_at = simulate.build(kind, zone, time.time())
        incident = records.create_incident(
            kind, zone, occurred_at=occurred_at, alert=alert, detail=detail, track=track,
            summary=f"SIMULACRO · {alert['summary']}", simulated=True, closed=True)
        event = {**alert, **incident.to_summary()}
        broadcast.send_incident_sync(event)
        return Response(incident.to_summary(), status=status.HTTP_201_CREATED)


class ObserverSerializer(serializers.Serializer):
    # "gps": la persona está ahí. "zone": mira desde el centro de la zona (está lejos o sin GPS).
    source = serializers.ChoiceField(choices=["gps", "zone"])
    lat = serializers.FloatField(min_value=-90, max_value=90, required=False)
    lon = serializers.FloatField(min_value=-180, max_value=180, required=False)
    alt = serializers.FloatField(min_value=-500, max_value=9000, required=False, allow_null=True)

    def validate(self, attrs):
        if attrs["source"] == "gps" and ("lat" not in attrs or "lon" not in attrs):
            raise serializers.ValidationError("Falta la ubicación (lat y lon).")
        return attrs


class SightingSerializer(serializers.Serializer):
    observer = ObserverSerializer()
    azimuth = serializers.FloatField(min_value=0, max_value=360)
    elevation = serializers.FloatField(min_value=-90, max_value=90)
    lift = serializers.FloatField(min_value=1, max_value=sightings.MAX_LIFT, default=1)
    note = serializers.CharField(max_length=300, allow_blank=True, default="", trim_whitespace=True)
    photo = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    # Guardar aunque haya una aeronave identificada en esa dirección ("no es esa").
    force = serializers.BooleanField(default=False)

    def validate_photo(self, value):
        return decode_photo(value) if value else None


def decode_photo(data_url):
    """De ``data:image/jpeg;base64,...`` a un JPEG limpio (reencodeado) de tamaño acotado."""
    encoded = data_url.split(",", 1)[1] if data_url.startswith("data:") and "," in data_url else data_url
    if len(encoded) > PHOTO_MAX_BYTES * 4 // 3 + 16:
        raise serializers.ValidationError("La foto es demasiado grande.")
    try:
        raw = base64.b64decode(encoded, validate=True)
        image = Image.open(io.BytesIO(raw))
        image.load()
    except (binascii.Error, ValueError, UnidentifiedImageError, OSError):
        raise serializers.ValidationError("La foto no es una imagen válida.") from None
    image = image.convert("RGB")
    image.thumbnail((PHOTO_MAX_SIDE, PHOTO_MAX_SIDE))
    out = io.BytesIO()
    image.save(out, "JPEG", quality=80)
    return out.getvalue()


def resolve_observer(data, zone):
    """Desde dónde se mira y con cuánto "cielo levantado"."""
    observer = data["observer"]
    if observer["source"] == "zone":
        return {"source": "zone", "lat": zone.lat, "lon": zone.lon, "alt": 0.0}, data["lift"]
    # Con la ubicación real los ángulos son los reales.
    return {"source": "gps", "lat": observer["lat"], "lon": observer["lon"], "alt": observer.get("alt") or 0.0}, 1.0


def evaluate(data, zone):
    observer, lift = resolve_observer(data, zone)
    aircraft = monitor.aircraft_now()
    result = sightings.evaluate(aircraft, observer, data["azimuth"] % 360, data["elevation"], lift)
    return observer, lift, aircraft, result


class SightingCheckView(APIView):
    """¿Hay alguna aeronave con transponder hacia donde apunta la cámara? No guarda nada."""

    def post(self, request):
        serializer = SightingSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        zone = Zone.from_config(SystemConfig.load())
        _observer, _lift, aircraft, result = evaluate(serializer.validated_data, zone)
        return Response({**result, "aircraft_seen": len(aircraft), "feed_ok": monitor.status.get("ok") is True})


class SightingThrottle(UserRateThrottle):
    scope = "sighting"


def sighting_text(incident, config):
    """Mensaje de Telegram de un avistamiento."""
    detail = incident.data["detail"]
    lines = [
        "<b>AVISTAMIENTO SIN TRANSPONDER</b>",
        f"Zona: {escape(config.zone_name)}",
        "",
        f"<i>{escape(incident.summary)}</i>",
    ]
    if detail.get("note"):
        lines += ["", f"Nota: {escape(detail['note'])}"]
    lines += ["", f"Informó: {escape(detail.get('reported_by') or 'sin nombre')}",
              f"Hora: {alert_clock(incident.occurred_at)}"]
    base = public_base_url(settings.SITE_URL)
    if base:
        lines += ["", f'<a href="{escape(base, quote=True)}/registro/{incident.pk}">Ver en el registro</a>']
    return "\n".join(lines)


class SightingView(APIView):
    """Guarda un avistamiento: algo que se ve en el cielo y no figura con transponder."""

    throttle_classes = [SightingThrottle]

    def post(self, request):
        serializer = SightingSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        config = SystemConfig.load()
        zone = Zone.from_config(config)
        observer, lift, aircraft, result = evaluate(data, zone)
        if result["match"] and not data["force"]:
            return Response(
                {"detail": f"En esa dirección está {result['match']['label']}.", **result},
                status=status.HTTP_409_CONFLICT)

        azimuth, elevation = data["azimuth"] % 360, data["elevation"]
        now = time.time()
        nearby = sorted(aircraft, key=lambda ac: ac.dst)[:TRAFFIC_LIMIT]
        detail = {
            "observer": observer,
            "azimuth": round(azimuth, 1),
            "elevation": round(elevation, 1),
            "lift": lift,
            "note": data["note"],
            "reported_by": request.user.get_full_name() or request.user.get_username(),
            "candidates": result["candidates"],
            "threshold": result["threshold"],
            "forced": bool(result["match"]),
            "aircraft_seen": len(aircraft),
            # Lo que el sistema sí veía en ese momento (para la repetición).
            "traffic": [[ac.hex, ac.label, ac.type, ac.category, round(ac.lat, 5), round(ac.lon, 5),
                         ac.alt_baro, ac.track] for ac in nearby],
        }
        point = _COMPASS[round(azimuth / 45) % 8]
        where = f"hacia el {point} ({azimuth:03.0f}°), {abs(elevation):.0f}° {'sobre' if elevation >= 0 else 'bajo'} el horizonte"
        summary = f"Objeto sin transponder {where}"
        if result["match"]:
            summary += f" (lo más cercano con transponder: {result['match']['label']})"
        incident = records.create_incident(
            behavior.SIGHTING, zone, occurred_at=now, detail=detail, summary=summary, photo=data.get("photo"),
            alert={"label": "Sin identificar", "zone": zone.name, "time": now}, closed=True)
        event = {**incident.to_summary(), "detail": {k: v for k, v in detail.items() if k != "traffic"}}
        broadcast.send_incident_sync(event)
        if getattr(config, "behavior_alerts", False):
            monitor.notify_from_thread({
                "label": "avistamiento", "text": sighting_text(incident, config),
                "photo": data.get("photo"),
            })
        return Response(incident.to_summary(), status=status.HTTP_201_CREATED)
