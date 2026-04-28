# app/api/asistencia.py
import pytz
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify, current_app
from app.extensions import db
from app.models import RegistroAsistencia, Guardia, PuntoControl
from app.utils import token_required, rango_hoy_utc, calcular_distancia, coords_punto

asistencia_bp = Blueprint('asistencia', __name__)
ZONA_CL = pytz.timezone('America/Santiago')

def _validar_qr_asistencia(id_guardia, data):
    """Helper interno: Valida que el QR exista y calcula si el GPS está cerca."""
    token_qr = data.get('token_qr')
    if not token_qr:
        return {"error": "Debes escanear el QR de la portería."}, 400

    punto = PuntoControl.query.filter_by(token_qr=token_qr).first()
    if not punto:
        return {"error": "QR no registrado en el sistema."}, 404

    guardia = Guardia.query.get(id_guardia)

    # PARCHE A-1: si el punto pertenece a una sede, el guardia DEBE pertenecer
    # a la misma sede. Guardias sin sede asignada no pueden marcar en sedes
    # específicas (solo en puntos globales con id_sede=NULL).
    if punto.id_sede is not None:
        if not guardia or guardia.id_sede != punto.id_sede:
            return {"error": "Este QR pertenece a otra instalación."}, 403

    # PARCHE C-5: GPS es OBLIGATORIO para asistencia (el control de radio
    # actúa como gate, no como warning). Si el cliente omite lat/long o envía
    # null, RECHAZAR. Antes el `if lat and lon` permitía bypass enviando
    # `{"token_qr": "..."}`  sin GPS y marcaba entrada/salida desde cualquier lugar.
    lat, lon = _parse_gps(data)
    if lat is None or lon is None:
        return {"error": "GPS requerido para marcar asistencia. Activa la ubicación e intenta de nuevo."}, 400

    # PARCHE SRE-5: el punto puede no tener coordenadas si fue creado con
    # GPS denegado. Antes float(None) crashaba la marca de entrada/salida
    # con un 500 silencioso desde el punto de vista del guardia.
    p_lat, p_lon, radio = coords_punto(punto)
    if p_lat is None:
        return {"error": "Esta portería no tiene coordenadas configuradas. Avisa al admin."}, 422

    dist = calcular_distancia(lat, lon, p_lat, p_lon)
    if dist > radio:
        return {"error": f"Estás muy lejos de la portería ({int(dist)}m). Acércate para marcar."}, 403

    return {"lat": lat, "lon": lon}, 200


@asistencia_bp.route('/entrada', methods=['POST'])
@token_required
def registrar_entrada():
    id_guardia = request.usuario_id
    data = request.get_json(silent=True) or {}

    validacion, status = _validar_qr_asistencia(id_guardia, data)
    if status != 200:
        return jsonify({"status": "error", "message": validacion["error"]}), status

    inicio_hoy, fin_hoy = rango_hoy_utc()
    # PARCHE A-4: rango_hoy_utc retorna ahora [inicio, mañana_00:00).
    # Usar >= y < en vez de .between() (que es inclusivo en ambos extremos
    # y combinado con la precisión por defecto de MySQL DATETIME(0) puede
    # ocultar registros de los últimos microsegundos del día).
    ultimo_hoy = RegistroAsistencia.query.filter(
        RegistroAsistencia.id_guardia == id_guardia,
        RegistroAsistencia.fecha_hora >= inicio_hoy,
        RegistroAsistencia.fecha_hora <  fin_hoy
    ).order_by(RegistroAsistencia.fecha_hora.desc()).first()

    advertencia = None
    if ultimo_hoy and ultimo_hoy.tipo == 'entrada':
        advertencia = "Ya tenías una entrada registrada hoy."

    nuevo = RegistroAsistencia(
        id_guardia=id_guardia, tipo='entrada',
        fecha_hora=datetime.now(timezone.utc),
        lat=validacion["lat"], long=validacion["lon"]
    )
    # PARCHE SRE-3: try/except con rollback
    try:
        db.session.add(nuevo)
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Error al registrar entrada (guardia=%s)", id_guardia)
        return jsonify({"status": "error", "message": "No se pudo registrar la entrada. Intenta de nuevo."}), 500

    hora_local = datetime.now(ZONA_CL).strftime("%H:%M")
    respuesta = {
        "status": "success", "message": f"✅ Entrada registrada a las {hora_local}.", "tipo": "entrada"
    }
    if advertencia: respuesta["advertencia"] = advertencia
    return jsonify(respuesta), 201


@asistencia_bp.route('/salida', methods=['POST'])
@token_required
def registrar_salida():
    id_guardia = request.usuario_id
    data = request.get_json(silent=True) or {}

    validacion, status = _validar_qr_asistencia(id_guardia, data)
    if status != 200:
        return jsonify({"status": "error", "message": validacion["error"]}), status

    nuevo = RegistroAsistencia(
        id_guardia=id_guardia, tipo='salida',
        fecha_hora=datetime.now(timezone.utc),
        lat=validacion["lat"], long=validacion["lon"]
    )
    # PARCHE SRE-3: try/except con rollback
    try:
        db.session.add(nuevo)
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Error al registrar salida (guardia=%s)", id_guardia)
        return jsonify({"status": "error", "message": "No se pudo registrar la salida. Intenta de nuevo."}), 500

    hora_local = datetime.now(ZONA_CL).strftime("%H:%M")
    return jsonify({
        "status": "success", "message": f"✅ Salida registrada a las {hora_local}.", "tipo": "salida"
    }), 201

@asistencia_bp.route('/estado', methods=['GET'])
@token_required
def estado_asistencia():
    """
    Retorna el estado de asistencia del guardia en la jornada de hoy.
    Usado al cargar la app para mostrar el botón correcto (Entrada/Salida).
    """
    id_guardia = request.usuario_id
    inicio_hoy, fin_hoy = rango_hoy_utc()

    # PARCHE A-4: rango semi-abierto consistente
    registros_hoy = RegistroAsistencia.query.filter(
        RegistroAsistencia.id_guardia == id_guardia,
        RegistroAsistencia.fecha_hora >= inicio_hoy,
        RegistroAsistencia.fecha_hora <  fin_hoy
    ).order_by(RegistroAsistencia.fecha_hora.asc()).all()

    ultimo = registros_hoy[-1] if registros_hoy else None

    return jsonify({
        "en_turno":      ultimo is not None and ultimo.tipo == 'entrada',
        "ultimo_tipo":   ultimo.tipo if ultimo else None,
        "ultima_hora":   (ultimo.fecha_hora
                         .replace(tzinfo=pytz.utc)
                         .astimezone(ZONA_CL)
                         .strftime("%H:%M")) if ultimo else None,
        "total_hoy":     len(registros_hoy)
    }), 200


# ─── ENDPOINT ADMIN ──────────────────────────────────────────────────────────

@asistencia_bp.route('/hoy', methods=['GET'])
@token_required
def asistencia_hoy():
    """
    Admin: listado de todas las marcas de asistencia del día,
    con filtro opcional por sede.
    """
    if request.usuario_rol != 'admin':
        return jsonify({"message": "No autorizado"}), 403

    inicio_hoy, fin_hoy = rango_hoy_utc()
    id_sede_str = request.args.get('id_sede')
    id_sede     = int(id_sede_str) if id_sede_str and id_sede_str.isdigit() else None

    # PARCHE A-4: rango semi-abierto consistente
    q = RegistroAsistencia.query.filter(
        RegistroAsistencia.fecha_hora >= inicio_hoy,
        RegistroAsistencia.fecha_hora <  fin_hoy
    )

    if id_sede:
        q = (q.join(Guardia,
                    RegistroAsistencia.id_guardia == Guardia.id_guardia,
                    isouter=True)
               .filter(Guardia.id_sede == id_sede))

    registros = q.order_by(RegistroAsistencia.fecha_hora.desc()).all()

    return jsonify([{
        "id":       r.id,
        "guardia":  r.guardia.nombre if r.guardia else "Guardia Eliminado",
        "rut":      r.guardia.rut    if r.guardia else "N/A",
        "sede":     r.guardia.sede.nombre if r.guardia and r.guardia.sede else "Sin Sede",
        "tipo":     r.tipo,
        "hora":     (r.fecha_hora
                    .replace(tzinfo=pytz.utc)
                    .astimezone(ZONA_CL)
                    .strftime("%H:%M:%S")),
        # PARCHE SRE-6: ver explicación en panico.py
        "lat":      float(r.lat)  if r.lat  is not None else None,
        "long":     float(r.long) if r.long is not None else None,
    } for r in registros]), 200


# ─── HELPER PRIVADO ──────────────────────────────────────────────────────────

def _parse_gps(data: dict) -> tuple:
    try:
        return float(data.get('lat')), float(data.get('long'))
    except (TypeError, ValueError):
        return None, None