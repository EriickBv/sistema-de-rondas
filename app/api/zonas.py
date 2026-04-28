# app/api/zonas.py
"""
PARCHES APLICADOS:
  SRE-3:  rollback ya estaba presente; añadido logging.
  SRE-5:  validación estricta de latitud/longitud (no aceptar ""/None que
          luego rompen el escaneo).
  SRE-12: serialización de reordenar_zonas() con un lock de proceso para
          evitar dos admins concurrentes asignando el mismo numero_orden.
          También se reordena dentro de la misma transacción que el INSERT
          o DELETE para garantizar atomicidad.
  SRE-16: parse_id_sede() en lugar de `int(x) if x else None`.
"""
import threading
import uuid
from flask import Blueprint, request, jsonify, current_app
from sqlalchemy.exc import IntegrityError
from app.extensions import db
from app.models import PuntoControl, Sede
from app.utils import token_required
from app.validators import parse_id_sede, validar_nombre

zonas_bp = Blueprint('zonas', __name__)


# PARCHE SRE-12: lock de proceso para serializar el reordenamiento.
# Razón: reordenar_zonas() hace un READ-MODIFY-WRITE sobre numero_orden de
# todos los puntos. Sin lock, dos admins simultáneos (o agregar_zona +
# reordenar_manual al mismo tiempo) leían la misma lista, asignaban el mismo
# numero_orden a filas distintas, y ambos commiteaban — corrupción silenciosa
# (no hay UNIQUE en numero_orden).
#
# Limitación: vale para 1 worker × N threads (ver Dockerfile). Si se escala
# a múltiples workers, hay que migrar a un lock de DB:
#   - SELECT ... FOR UPDATE sobre las filas, o
#   - GET_LOCK('reordenar_zonas', 5) en MySQL.
# Documentado para no olvidarlo si se escala.
_REORDER_LOCK = threading.Lock()


def _nuevo_token_qr() -> str:
    """
    PARCHE M-1: Token QR con 128 bits de entropía (UUID4 hex completo,
    32 chars). Antes se truncaba a 12 chars = 48 bits, vulnerable a
    colisiones por paradoja del cumpleaños (~16M generaciones) y a fuerza
    bruta dirigida. 32 chars no afectan la legibilidad del QR generado.
    """
    return uuid.uuid4().hex.upper()


def _reordenar_dentro_de_tx():
    """
    Renumera todos los puntos secuencialmente. NO hace commit — el caller
    debe hacerlo dentro de su misma transacción para que el reorden y la
    operación que lo motivó (alta/baja) sean atómicos.

    Debe llamarse SIEMPRE bajo _REORDER_LOCK.
    """
    zonas = PuntoControl.query.order_by(PuntoControl.numero_orden,
                                        PuntoControl.id_punto).all()
    for index, zona in enumerate(zonas):
        nuevo = index + 1
        if zona.numero_orden != nuevo:   # evita UPDATEs innecesarios
            zona.numero_orden = nuevo


def _validar_lat_lon(data):
    """
    PARCHE SRE-5: validación de coordenadas en la entrada (no aceptar
    string vacío, None ni valores fuera de rango). Retorna (lat, lon) o
    levanta ValueError con un mensaje útil.
    """
    lat_raw = data.get('latitud')
    lon_raw = data.get('longitud')
    if lat_raw is None or lat_raw == '' or lon_raw is None or lon_raw == '':
        raise ValueError("Latitud y longitud son obligatorias para crear/editar una zona.")
    try:
        lat = float(lat_raw)
        lon = float(lon_raw)
    except (TypeError, ValueError):
        raise ValueError("Latitud y longitud deben ser numéricas.")
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        raise ValueError("Latitud debe estar entre -90 y 90, longitud entre -180 y 180.")
    return lat, lon


@zonas_bp.route('/', methods=['POST'])
@token_required
def agregar_zona():
    if request.usuario_rol != 'admin':
        return jsonify({'message': 'No autorizado'}), 403
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'message': 'Payload inválido o Content-Type incorrecto'}), 400

    nombre = validar_nombre(data.get('nombre_zona'))
    if not nombre:
        return jsonify({'message': 'El nombre es obligatorio (1-100 caracteres)'}), 400

    try:
        lat, lon = _validar_lat_lon(data)
    except ValueError as e:
        return jsonify({'message': str(e)}), 400

    id_sede = parse_id_sede(data.get('id_sede'))
    if id_sede is not None and not Sede.query.get(id_sede):
        return jsonify({'message': 'La sede indicada no existe.'}), 400

    # PARCHE SRE-12: el INSERT y el reorden deben ser atómicos y serializados.
    # El lock asegura que solo un admin reordene a la vez. La transacción
    # única garantiza que si el reorden falla, no quede una fila huérfana
    # con numero_orden=999.
    with _REORDER_LOCK:
        try:
            nueva_zona = PuntoControl(
                nombre_zona=nombre,
                token_qr=_nuevo_token_qr(),
                latitud=lat,
                longitud=lon,
                radio_permitido=20,
                numero_orden=999,
                id_sede=id_sede
            )
            db.session.add(nueva_zona)
            db.session.flush()         # asigna id_punto sin commitear
            _reordenar_dentro_de_tx()  # renumera incluyendo la nueva
            db.session.commit()
            return jsonify({'message': f'Zona "{nombre}" agregada correctamente'}), 201
        except IntegrityError:
            db.session.rollback()
            current_app.logger.exception("IntegrityError al agregar zona '%s'", nombre)
            return jsonify({'message': 'Conflicto al generar el QR. Reintenta.'}), 409
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Error al agregar zona '%s'", nombre)
            return jsonify({'message': 'Error interno al crear la zona'}), 500


@zonas_bp.route('/<int:id_zona>', methods=['DELETE'])
@token_required
def eliminar_zona(id_zona):
    if request.usuario_rol != 'admin':
        return jsonify({'message': 'No autorizado'}), 403

    # PARCHE SRE-12: DELETE + reorden atómicos y serializados.
    with _REORDER_LOCK:
        try:
            zona = PuntoControl.query.get_or_404(id_zona)
            nombre = zona.nombre_zona
            db.session.delete(zona)
            db.session.flush()
            _reordenar_dentro_de_tx()
            db.session.commit()
            return jsonify({'message': f'Zona "{nombre}" eliminada'}), 200
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Error al eliminar zona %s", id_zona)
            return jsonify({'message': 'Error interno al eliminar la zona'}), 500


@zonas_bp.route('/reordenar', methods=['PUT'])
@token_required
def reordenar_manual():
    if request.usuario_rol != 'admin':
        return jsonify({'message': 'No autorizado'}), 403

    data = request.get_json(silent=True)
    if not data:
        return jsonify({'message': 'Payload inválido o Content-Type incorrecto'}), 400

    orden_ids = data.get('orden', [])
    if not isinstance(orden_ids, list):
        return jsonify({'message': 'El campo "orden" debe ser una lista de id_zona'}), 400

    # PARCHE SRE-12: reorden serializado para no chocar con agregar_zona /
    # eliminar_zona concurrentes.
    with _REORDER_LOCK:
        try:
            # Pre-validar que todos los IDs existan (para no quedar a medio
            # reorden si uno no existe)
            zonas_map = {z.id_punto: z for z in
                         PuntoControl.query.filter(PuntoControl.id_punto.in_(orden_ids)).all()}
            ids_invalidos = [i for i in orden_ids if i not in zonas_map]
            if ids_invalidos:
                return jsonify({
                    'message': f'IDs de zona inexistentes: {ids_invalidos}'
                }), 400

            for index, id_zona in enumerate(orden_ids):
                zonas_map[id_zona].numero_orden = index + 1
            db.session.commit()
            return jsonify({'message': 'Orden actualizado correctamente'}), 200
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Error al reordenar zonas")
            return jsonify({'message': 'Error interno al reordenar'}), 500


@zonas_bp.route('/<int:id_zona>/regenerar_qr', methods=['PUT'])
@token_required
def regenerar_qr(id_zona):
    if request.usuario_rol != 'admin':
        return jsonify({'message': 'No autorizado'}), 403

    try:
        zona = PuntoControl.query.get_or_404(id_zona)
        zona.token_qr = _nuevo_token_qr()  # PARCHE M-1: 128 bits de entropía
        db.session.commit()
        return jsonify({'message': f'QR de "{zona.nombre_zona}" regenerado'}), 200
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Error al regenerar QR de zona %s", id_zona)
        return jsonify({'message': 'Error interno al regenerar el QR'}), 500