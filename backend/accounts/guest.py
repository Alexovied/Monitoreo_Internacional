"""Acceso de visitantes: entrar sin cuenta, solo para mirar.

Un visitante no es un usuario de Django: es una sesión anónima marcada con
``GUEST_SESSION_KEY``. Para la API sigue siendo anónimo (``request.user`` es
``AnonymousUser``), así que todo lo que exige sesión de usuario o rol de
administrador le responde 401. Solo puede consultar ``/api/auth/me/`` y
abrir el WebSocket del mapa, y únicamente mientras el acceso esté habilitado
en el .env (GUEST_ACCESS_ENABLED) y en la configuración: se comprueba en
cada pedido, así que apagarlo corta a los visitantes en el momento.
"""

from configuration.models import SystemConfig

GUEST_SESSION_KEY = "guest"
ROLE_GUEST = "guest"


def guest_access_enabled():
    return SystemConfig.load().guest_access_enabled


def is_guest_session(session):
    """Si la sesión es de un visitante y el acceso de visitantes sigue habilitado."""
    return bool(session.get(GUEST_SESSION_KEY)) and guest_access_enabled()


def guest_payload():
    """Lo que devuelve /api/auth/me/ para un visitante (misma forma que un usuario)."""
    return {
        "id": None,
        "username": "visitante",
        "first_name": "Visitante",
        "last_name": "",
        "role": ROLE_GUEST,
        "is_admin": False,
        "is_guest": True,
    }
