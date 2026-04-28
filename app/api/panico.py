# app/api/panico.py
import pytz
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify, current_app
from sqlalchemy import update
from app.extensions import db
from app.models import AlertaPanico, Guardia
from app.utils import token_required

panico_bp = Blueprint('panico', __name__)
ZONA_CL = pytz.timezone('America/Santiago')


# ─── ENDPOINT GUARDIA: EMITIR ALERTA ─────────────────────────────────────────

@panico_bp.route('/', methods=['POST'])
@token_required
def emitir_alerta():
    id_guardia = request.usuario_id
    data = request.get_json(silent=True) or {}

    try:
        lat  = float(data.get('lat'))
        lon  = float(data.get('long'))
    except (TypeError, ValueError):
        lat = lon = None

    alerta = AlertaPanico(
        id_guardia=id_guardia,
        fecha_hora=datetime.now(timezone.utc),
        lat=lat,
        long=lon,
        atendida=False
    )
    # PARCHE SRE-3: la ruta de pánico es CRÍTICA — un crash aquí no solo
    # pierde la alerta, sino que envenena la sesión y rompe las próximas
    # requests del worker. Try/except obligatorio.
    try:
        db.session.add(alerta)
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Error al guardar AlertaPanico (guardia=%s)", id_guardia)
        return jsonify({
            "status":  "error",
            "message": "No se pudo registrar la alerta. Intenta de nuevo."
        }), 500

    return jsonify({
        "status":    "success",
        "message":   "🚨 Alerta enviada. El equipo de respuesta ha sido notificado.",
        "id_alerta": alerta.id
    }), 201


# ─── ENDPOINTS ADMIN ─────────────────────────────────────────────────────────

@panico_bp.route('/activas', methods=['GET'])
@token_required
def alertas_activas():
    if request.usuario_rol != 'admin':
        return jsonify({"message": "No autorizado"}), 403

    alertas = (AlertaPanico.query
               .filter_by(atendida=False)
               .order_by(AlertaPanico.fecha_hora.desc())
               .all())

    return jsonify({
        "total": len(alertas),
        "alertas": [_serializar_alerta(a) for a in alertas]
    }), 200


@panico_bp.route('/<int:id_alerta>/atender', methods=['PUT'])
@token_required
def atender_alerta(id_alerta):
    """
    Marca una alerta como atendida. Registra el timestamp de atención
    para el historial de auditoría.

    PARCHE A-3: dos admins viendo la misma consola pueden pulsar "Atender"
    a la vez. Antes el patrón check-then-write permitía a ambos pasar el
    `if alerta.atendida:` y sobrescribirse mutuamente el `atendida_en`.
    Ahora usamos UPDATE ... WHERE atendida=FALSE como operación atómica:
    solo el primer worker actualiza una fila (rowcount=1); el segundo
    obtiene rowcount=0 y responde "ya atendida".
    """
    if request.usuario_rol != 'admin':
        return jsonify({"message": "No autorizado"}), 403

    ahora = datetime.now(timezone.utc)
    # PARCHE SRE-3: try/except con rollback obligatorio
    try:
        rows = db.session.execute(
            update(AlertaPanico)
            .where(AlertaPanico.id == id_alerta,
                   AlertaPanico.atendida == False)
            .values(atendida=True, atendida_en=ahora)
        ).rowcount
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Error al atender alerta %s", id_alerta)
        return jsonify({"message": "Error interno al atender la alerta"}), 500

    if rows == 0:
        # O bien la alerta no existe, o ya fue atendida por otro admin.
        existe = db.session.query(AlertaPanico.id).filter_by(id=id_alerta).first()
        if not existe:
            return jsonify({"message": "Alerta no encontrada"}), 404
        return jsonify({"message": "Esta alerta ya fue atendida"}), 400

    return jsonify({
        "message": "Alerta marcada como atendida.",
        "atendida_en": ahora.isoformat()
    }), 200


@panico_bp.route('/historial', methods=['GET'])
@token_required
def historial_alertas():
    if request.usuario_rol != 'admin':
        return jsonify({"message": "No autorizado"}), 403

    id_sede_str  = request.args.get('id_sede')
    id_sede      = int(id_sede_str) if id_sede_str and id_sede_str.isdigit() else None
    solo_activas = bool(request.args.get('solo_activas'))

    q = AlertaPanico.query
    if solo_activas:
        q = q.filter_by(atendida=False)
    if id_sede:
        q = (q.join(Guardia,
                    AlertaPanico.id_guardia == Guardia.id_guardia,
                    isouter=True)
               .filter(Guardia.id_sede == id_sede))

    alertas = q.order_by(AlertaPanico.fecha_hora.desc()).limit(200).all()
    return jsonify([_serializar_alerta(a) for a in alertas]), 200


# ─── HELPER PRIVADO ──────────────────────────────────────────────────────────

def _serializar_alerta(a: AlertaPanico) -> dict:
    fecha_cl = a.fecha_hora.replace(tzinfo=pytz.utc).astimezone(ZONA_CL)
    return {
        "id":          a.id,
        "guardia":     a.guardia.nombre if a.guardia else "Guardia Eliminado",
        "rut":         a.guardia.rut    if a.guardia else "N/A",
        "sede":        a.guardia.sede.nombre if a.guardia and a.guardia.sede else "Sin Sede",
        "id_guardia":  a.id_guardia,
        "fecha_hora":  fecha_cl.strftime("%Y-%m-%d %H:%M:%S"),
        "fecha_iso":   a.fecha_hora.isoformat() + "Z",
        # PARCHE SRE-6: `if a.lat` falla con Decimal('0') (False truthy).
        # Una alerta en lat=0 (ecuador) o long=0 (Greenwich) se serializaba
        # como null y el supervisor perdía la geolocalización. `is not None`
        # es lo correcto.
        "lat":         float(a.lat)  if a.lat  is not None else None,
        "long":        float(a.long) if a.long is not None else None,
        "atendida":    a.atendida,
        "atendida_en": a.atendida_en.isoformat() + "Z" if a.atendida_en else None,
    }