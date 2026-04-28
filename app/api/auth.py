# app/api/auth.py
from __future__ import annotations
import datetime
import threading
import time
from collections import defaultdict, deque
from datetime import timezone
import jwt
from flask import Blueprint, request, jsonify, current_app
from app.extensions import db
from app.models import Guardia, Administrador, Sede
from app.utils import token_required
from app.validators import (
    validar_rut, validar_email, parse_id_sede,
    validar_password, validar_nombre,
)

auth_bp = Blueprint('auth', __name__)


_RATE_LOCK     = threading.Lock()
_FAILED_ATTEMPTS: dict = defaultdict(deque)   # (ip, key) -> deque[timestamps]
_BLOCKED_UNTIL:  dict = {}                    # (ip, key) -> timestamp expiración

_MAX_FAILS    = 10
_WINDOW_SEC   = 300    # 5 min
_BLOCK_SEC    = 900    # 15 min


def _bucket_key(identificador: str) -> tuple:
    """Identifica el bucket de rate limit. IP + usuario para no penalizar a
    usuarios reales por culpa de un atacante en otra red."""
    ip = request.headers.get('X-Forwarded-For', request.remote_addr or 'unknown')
    ip = ip.split(',')[0].strip()
    return (ip, (identificador or '').lower())


def _rate_limit_check(identificador: str) -> bool:
    """Retorna True si la request puede proceder, False si está bloqueada."""
    key = _bucket_key(identificador)
    ahora = time.monotonic()
    with _RATE_LOCK:
        bloqueado_hasta = _BLOCKED_UNTIL.get(key)
        if bloqueado_hasta and ahora < bloqueado_hasta:
            return False
        elif bloqueado_hasta:
            # Expiró el bloqueo: limpiar
            _BLOCKED_UNTIL.pop(key, None)
            _FAILED_ATTEMPTS.pop(key, None)
        return True


def _rate_limit_record_fail(identificador: str) -> None:
    """Registra un fallo. Activa el bloqueo si supera el umbral."""
    key = _bucket_key(identificador)
    ahora = time.monotonic()
    with _RATE_LOCK:
        cola = _FAILED_ATTEMPTS[key]
        # Purga timestamps fuera de la ventana
        while cola and cola[0] < ahora - _WINDOW_SEC:
            cola.popleft()
        cola.append(ahora)
        if len(cola) >= _MAX_FAILS:
            _BLOCKED_UNTIL[key] = ahora + _BLOCK_SEC
            cola.clear()


def _rate_limit_record_success(identificador: str) -> None:
    """Login exitoso: limpiar el contador."""
    key = _bucket_key(identificador)
    with _RATE_LOCK:
        _FAILED_ATTEMPTS.pop(key, None)
        _BLOCKED_UNTIL.pop(key, None)


# ─── ENDPOINTS ───────────────────────────────────────────────────────────────

@auth_bp.route('/login_guardia', methods=['POST'])
def login_guardia():
    data = request.get_json(silent=True) or {}
    rut_raw  = (data.get('rut')      or '').strip()
    password = (data.get('password') or '')

    if not rut_raw or not password:
        return jsonify({"message": "Faltan datos"}), 400

    # PARCHE SRE-13: normalizar el RUT antes de buscar para que
    # "12345678-5" y "12.345.678-5" matcheen el mismo registro en DB.
    # Si el RUT es inválido respondemos como credencial inválida (no
    # damos pista al atacante de si el formato existe o no).
    rut_normalizado = validar_rut(rut_raw)
    if not rut_normalizado:
        return jsonify({"message": "Credenciales inválidas"}), 401

    # PARCHE SRE-14: rate limit
    if not _rate_limit_check(rut_normalizado):
        return jsonify({
            "message": "Demasiados intentos fallidos. Intenta de nuevo en unos minutos."
        }), 429

    guardia = Guardia.query.filter_by(rut=rut_normalizado).first()

    if guardia and guardia.activo and guardia.check_password(password):
        _rate_limit_record_success(rut_normalizado)
        token = jwt.encode({
            'id': guardia.id_guardia,
            'rol': 'guardia',
            'exp': datetime.datetime.now(timezone.utc) + datetime.timedelta(hours=12)
        }, current_app.config['SECRET_KEY'], algorithm="HS256")
        return jsonify({'token': token, 'nombre': guardia.nombre}), 200

    _rate_limit_record_fail(rut_normalizado)
    return jsonify({"message": "Credenciales inválidas"}), 401


@auth_bp.route('/crear_guardia', methods=['POST'])
@token_required
def crear_guardia():
    """
    PARCHE SRE-4: validación estricta antes de tocar la DB. Antes había
    KeyError si faltaba cualquier campo, y `return jsonify(str(e))` filtraba
    SQL al cliente.
    """
    if request.usuario_rol != 'admin':
        return jsonify({"message": "No autorizado"}), 403

    data = request.get_json(silent=True) or {}

    nombre = validar_nombre(data.get('nombre'))
    if not nombre:
        return jsonify({"message": "El nombre es obligatorio (1-100 caracteres)"}), 400

    rut = validar_rut(data.get('rut'))
    if not rut:
        return jsonify({"message": "RUT inválido. Verifica el formato y el dígito verificador."}), 400

    password = data.get('password') or ''
    if not validar_password(password):
        return jsonify({"message": "La contraseña debe tener al menos 8 caracteres."}), 400

    # Email es opcional, pero si viene debe ser válido
    email_raw = data.get('email')
    email_normalizado = None
    if email_raw:
        email_normalizado = validar_email(email_raw)
        if not email_normalizado:
            return jsonify({"message": "Email con formato inválido."}), 400

    # PARCHE SRE-16: parse_id_sede en lugar de `int(x) if x else None`
    id_sede = parse_id_sede(data.get('id_sede'))
    if id_sede is not None and not Sede.query.get(id_sede):
        return jsonify({"message": "La sede indicada no existe."}), 400

    # Pre-check de duplicado (TOCTOU posible, pero la UNIQUE constraint cubre
    # la race en el INSERT — el except IntegrityError lo convierte en 409).
    if Guardia.query.filter_by(rut=rut).first():
        return jsonify({"message": "El RUT ya existe"}), 409
    if email_normalizado and Guardia.query.filter_by(email=email_normalizado).first():
        return jsonify({"message": "El email ya está registrado"}), 409

    nuevo_guardia = Guardia(
        nombre=nombre,
        rut=rut,
        email=email_normalizado,
        id_sede=id_sede
    )
    nuevo_guardia.set_password(password)

    # PARCHE SRE-3: rollback obligatorio + sin filtrar str(e) al cliente
    try:
        db.session.add(nuevo_guardia)
        db.session.commit()
        return jsonify({"message": "Guardia creado exitosamente"}), 201
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Error al crear guardia rut=%s", rut)
        return jsonify({
            "message": "No se pudo crear el guardia. Es posible que el RUT o email ya existan."
        }), 500


@auth_bp.route('/login_admin', methods=['POST'])
def login_admin():
    data = request.get_json(silent=True) or {}
    usuario  = (data.get('usuario')  or '').strip()
    password = (data.get('password') or '')

    if not usuario or not password:
        return jsonify({"message": "Faltan datos"}), 400

    # PARCHE SRE-14: rate limit por usuario+IP
    if not _rate_limit_check(usuario):
        return jsonify({
            "message": "Demasiados intentos fallidos. Intenta de nuevo en unos minutos."
        }), 429

    admin = Administrador.query.filter_by(usuario=usuario).first()

    if admin and admin.check_password(password):
        _rate_limit_record_success(usuario)
        token = jwt.encode({
            'id': admin.id_admin,
            'rol': 'admin',
            'exp': datetime.datetime.now(timezone.utc) + datetime.timedelta(hours=4)
        }, current_app.config['SECRET_KEY'], algorithm="HS256")
        return jsonify({'token': token}), 200

    _rate_limit_record_fail(usuario)
    # Mensaje genérico (no decir si el usuario existe o no)
    return jsonify({"message": "Credenciales inválidas"}), 401


@auth_bp.route('/cambiar_estado_guardia/<int:id>', methods=['PUT'])
@token_required
def cambiar_estado_guardia(id):
    if request.usuario_rol != 'admin':
        return jsonify({"message": "No tienes permiso"}), 403
    guardia = Guardia.query.get(id)
    if not guardia:
        return jsonify({"message": "Guardia no encontrado"}), 404
    guardia.activo = not guardia.activo
    # PARCHE SRE-3: try/except con rollback
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Error al cambiar estado del guardia %s", id)
        return jsonify({"message": "Error interno al cambiar el estado"}), 500
    return jsonify({"message": f"Guardia {'activado' if guardia.activo else 'desactivado'}"}), 200


@auth_bp.route('/cambiar_sede_guardia/<int:id>', methods=['PUT'])
@token_required
def cambiar_sede_guardia(id):
    if request.usuario_rol != 'admin':
        return jsonify({"message": "No autorizado"}), 403
    guardia = Guardia.query.get_or_404(id)
    data    = request.get_json(silent=True) or {}

    # PARCHE SRE-16: parse_id_sede + verificación de existencia
    id_sede = parse_id_sede(data.get('id_sede'))
    if id_sede is not None and not Sede.query.get(id_sede):
        return jsonify({"message": "La sede indicada no existe."}), 400
    guardia.id_sede = id_sede

    # PARCHE SRE-3: try/except con rollback
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Error al cambiar sede del guardia %s", id)
        return jsonify({"message": "Error interno al cambiar la sede"}), 500
    return jsonify({"message": "Sede actualizada correctamente"}), 200


@auth_bp.route('/validar_token', methods=['GET'])
@token_required
def validar_token():
    return jsonify({"status": "ok"}), 200
