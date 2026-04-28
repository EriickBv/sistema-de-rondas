# app/utils.py
import math
import pytz
import jwt
from datetime import datetime, time as dt_time, timedelta
from functools import wraps
from flask import request, jsonify, current_app
from sqlalchemy import or_
from .models import Guardia

ZONA_CL = pytz.timezone('America/Santiago')


def rango_hoy_utc() -> tuple:
    """
    Retorna (inicio_utc, fin_utc) del día de hoy en hora de Santiago.

    PARCHE A-4: Rango SEMI-ABIERTO [inicio_hoy, inicio_mañana).
    Razón: MySQL DATETIME por defecto tiene precisión de 0 fracciones de segundo,
    así que `dt_time.max` (23:59:59.999999) se trunca a 23:59:59 y eventos
    en el último microsegundo del día podían quedar fuera del rango.
    Con rango semi-abierto el problema desaparece y se debe usar el operador
    `>= inicio AND < fin` (NO `between`) en los consumidores.

    pytz.localize() resuelve correctamente la ambigüedad de DST (CLST/CLT).
    NO usar datetime.replace(tzinfo=...) como sustituto.
    """
    hoy_local = datetime.now(ZONA_CL).date()
    manana_local = hoy_local + timedelta(days=1)
    inicio = ZONA_CL.localize(
        datetime.combine(hoy_local, dt_time.min)
    ).astimezone(pytz.utc)
    fin = ZONA_CL.localize(
        datetime.combine(manana_local, dt_time.min)
    ).astimezone(pytz.utc)
    return inicio, fin


# ─────────────────────────────────────────────────────────────────────────────
# UTILIDAD GPS
# ─────────────────────────────────────────────────────────────────────────────
def calcular_distancia(lat1, lon1, lat2, lon2) -> float:
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi    = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return round(R * c, 2)


def coords_punto(punto):
    """
    PARCHE SRE-5: extrae (lat, lon, radio) de un PuntoControl tolerando NULLs.

    Antes el código hacía `float(p.latitud)` directo, pero `latitud`/`longitud`
    son nullable. Si el admin creó un punto sin GPS (porque el JS de captura
    falló o el navegador denegó el permiso de ubicación) toda la lógica de
    ronda crashea con TypeError al escanear ese QR.

    Retorna (None, None, None) si el punto no tiene coordenadas válidas, lo
    cual el llamador debe manejar respondiendo 422 con un mensaje claro.
    `radio_permitido` cae al default 20m si está NULL.
    """
    if punto is None:
        return None, None, None
    if punto.latitud is None or punto.longitud is None:
        return None, None, None
    try:
        lat = float(punto.latitud)
        lon = float(punto.longitud)
    except (TypeError, ValueError):
        return None, None, None
    radio = punto.radio_permitido if punto.radio_permitido is not None else 20
    return lat, lon, radio


# ─────────────────────────────────────────────────────────────────────────────
# LÓGICA DE RUTA (reubicada desde rondas.py para uso compartido)
# ─────────────────────────────────────────────────────────────────────────────
def get_ruta_guardia(id_sede_guardia) -> list:

    from .models import PuntoControl
    
    if id_sede_guardia is None:
        puntos = PuntoControl.query.order_by(PuntoControl.numero_orden).all()
    else:
        puntos = PuntoControl.query.filter(
            or_(
                PuntoControl.id_sede == id_sede_guardia,
                PuntoControl.id_sede.is_(None)
            )
        ).order_by(PuntoControl.numero_orden).all()
    ruta_limpia = []
    palabras_excluidas = ["turno", "asistencia", "marcar"]
    for p in puntos:
        # PARCHE SRE-5: nombre_zona es nullable. Una sola fila con NULL
        # rompía .lower() y reventaba TODOS los endpoints de ronda
        # (iniciar, escanear, saltar, finalizar, estado, admin/activas).
        nombre = (p.nombre_zona or '').lower()
        if not any(palabra in nombre for palabra in palabras_excluidas):
            ruta_limpia.append(p)

    return ruta_limpia


def build_checklist(id_ronda: int, id_sede_guardia) -> tuple:
    """
    Construye el estado actual del checklist para una ronda activa.
    Retorna (checklist: list[dict], completados: int, total: int).

    Un punto se considera completado si tiene al menos un RegistroRonda
    vinculado a esta ronda (incluyendo los OMITIDO / saltos).
    """
    from .models import RegistroRonda
    ruta = get_ruta_guardia(id_sede_guardia)

    ids_registrados = {
        r.id_punto
        for r in RegistroRonda.query.filter_by(id_ronda=id_ronda).all()
        if r.id_punto is not None
    }

    checklist = [
        {
            "id_punto":   p.id_punto,
            "nombre":     p.nombre_zona,
            "completado": p.id_punto in ids_registrados,
        }
        for p in ruta
    ]
    total       = len(checklist)
    completados = sum(1 for p in checklist if p["completado"])
    return checklist, completados, total


# ─────────────────────────────────────────────────────────────────────────────
# MIDDLEWARE JWT
# ─────────────────────────────────────────────────────────────────────────────
def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        if 'Authorization' in request.headers:
            token = request.headers['Authorization']
            if token.startswith("Bearer "):
                token = token.split(" ")[1]

        if not token:
            return jsonify({'message': 'Falta el Token de seguridad'}), 401

        try:
            data = jwt.decode(
                token,
                current_app.config['SECRET_KEY'],
                algorithms=["HS256"]
            )
        except jwt.ExpiredSignatureError:
            return jsonify({'message': 'El token ha expirado, inicia sesión de nuevo'}), 401
        except Exception:
            return jsonify({'message': 'Token inválido'}), 401

        if data.get('rol') == 'guardia':
            try:
                guardia_db = Guardia.query.get(data['id'])
                if not guardia_db or not guardia_db.activo:
                    return jsonify({
                        'message': 'Tu cuenta ha sido desactivada. Contacta al admin.'
                    }), 401
            except Exception:
                return jsonify({
                    'message': 'Error interno al verificar credenciales'
                }), 503

        request.usuario_id  = data['id']
        request.usuario_rol = data.get('rol')
        return f(*args, **kwargs)

    return decorated