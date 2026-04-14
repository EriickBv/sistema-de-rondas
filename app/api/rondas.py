# app/api/rondas.py
import pytz
from datetime import datetime, timezone, time as dt_time
from sqlalchemy import or_
from flask import Blueprint, request, jsonify
from app.extensions import db
from app.models import PuntoControl, RegistroRonda, Guardia
from app.utils import token_required, calcular_distancia

rondas_bp = Blueprint('rondas', __name__)
ZONA_CL = pytz.timezone('America/Santiago')


def _rango_hoy_utc() -> tuple:
    """
    Calcula los límites UTC del 'día de hoy' en hora de Santiago.
    Crítico para turnos nocturnos.
    """
    hoy_local = datetime.now(ZONA_CL).date()
    inicio = ZONA_CL.localize(datetime.combine(hoy_local, dt_time.min)).astimezone(pytz.utc)
    fin    = ZONA_CL.localize(datetime.combine(hoy_local, dt_time.max)).astimezone(pytz.utc)
    return inicio, fin


def _ruta_guardia(id_sede_guardia) -> list:
    """
    Retorna la secuencia de puntos accesibles para un guardia, ordenada por numero_orden.
    - Guardia sin sede: todos los puntos (comportamiento legacy, consistente con la
      validación de acceso que no restringe a estos guardias).
    - Guardia con sede: puntos de su sede + puntos sin sede (compartidos/globales).
    """
    if id_sede_guardia is None:
        return PuntoControl.query.order_by(PuntoControl.numero_orden).all()
    return PuntoControl.query.filter(
        or_(
            PuntoControl.id_sede == id_sede_guardia,
            PuntoControl.id_sede.is_(None)
        )
    ).order_by(PuntoControl.numero_orden).all()


def obtener_siguiente_punto_nombre(id_punto_actual, id_sede_guardia) -> str:
    """
    Retorna el nombre del siguiente punto en la ruta del guardia,
    usando índices sobre el subconjunto filtrado (no numero_orden global).
    """
    ruta = _ruta_guardia(id_sede_guardia)
    if not ruta:
        return "Fin"
    idx = next((i for i, p in enumerate(ruta) if p.id_punto == id_punto_actual), -1)
    if idx != -1:
        return ruta[(idx + 1) % len(ruta)].nombre_zona
    return "Desconocido"


@rondas_bp.route('/saltar', methods=['POST'])
@token_required
def saltar_punto():
    data       = request.get_json()
    motivo     = data.get('motivo', 'Sin motivo especificado')
    id_guardia = request.usuario_id

    # Obtener la ruta filtrada por sede del guardia
    guardia         = Guardia.query.get(id_guardia)
    id_sede_guardia = guardia.id_sede if guardia else None
    ruta            = _ruta_guardia(id_sede_guardia)

    if not ruta:
        return jsonify({"message": "No hay puntos configurados para esta instalación"}), 400

    ultimo_registro = RegistroRonda.query \
        .filter_by(id_guardia=id_guardia) \
        .order_by(RegistroRonda.fecha_hora.desc()) \
        .first()

    siguiente_indice = 0
    if ultimo_registro and ultimo_registro.punto:
        for i, p in enumerate(ruta):
            if p.id_punto == ultimo_registro.id_punto:
                siguiente_indice = (i + 1) % len(ruta)
                break

    punto_objetivo = ruta[siguiente_indice]

    nuevo_registro = RegistroRonda(
        fecha_hora=datetime.now(timezone.utc),
        id_guardia=id_guardia,
        id_punto=punto_objetivo.id_punto,
        lat_real=0.0, long_real=0.0, distancia_error=0.0,
        observacion=f"⚠️ OMITIDO: {motivo}"
    )
    db.session.add(nuevo_registro)
    db.session.commit()

    nombre_proximo = obtener_siguiente_punto_nombre(punto_objetivo.id_punto, id_sede_guardia)
    return jsonify({
        'message':       f'Punto "{punto_objetivo.nombre_zona}" saltado.',
        'proximo_punto': nombre_proximo,
        'status':        'success'
    }), 200


@rondas_bp.route('/', methods=['POST'])
@token_required
def registrar_ronda():
    id_guardia = request.usuario_id
    data       = request.get_json()

    # 1. Validar QR
    punto = PuntoControl.query.filter_by(token_qr=data['token_qr']).first()
    if not punto:
        return jsonify({"status": "error", "message": "QR no registrado en el sistema"}), 404

    # 2. Validación de Sede
    guardia = Guardia.query.get(id_guardia)
    if guardia and guardia.id_sede and punto.id_sede and guardia.id_sede != punto.id_sede:
        return jsonify({
            "status":  "error",
            "message": "Este punto pertenece a otra instalación"
        }), 403

    # 3. Calcular Distancia
    try:
        lat  = float(data['lat'])
        lon  = float(data['long'])
        dist = calcular_distancia(lat, lon, float(punto.latitud), float(punto.longitud))
    except Exception:
        dist = 999999.0

    status_resp = "success"
    msgs        = []

    if dist > punto.radio_permitido:
        status_resp = "warning"
        msgs.append(f"Lejos ({int(dist)}m)")
    id_sede_guardia = guardia.id_sede if guardia else None
    ruta            = _ruta_guardia(id_sede_guardia)

    inicio_hoy_utc, fin_hoy_utc = _rango_hoy_utc()
    ultimo = RegistroRonda.query.filter(
        RegistroRonda.id_guardia == id_guardia,
        RegistroRonda.fecha_hora >= inicio_hoy_utc,
        RegistroRonda.fecha_hora <= fin_hoy_utc
    ).order_by(RegistroRonda.fecha_hora.desc()).first()

    if ultimo and ultimo.punto and ruta:
        idx_anterior = next((i for i, p in enumerate(ruta) if p.id_punto == ultimo.id_punto), -1)
        idx_actual   = next((i for i, p in enumerate(ruta) if p.id_punto == punto.id_punto), -1)

        # Solo validamos si ambos puntos pertenecen a la misma ruta (índices válidos)
        if idx_anterior != -1 and idx_actual != -1:
            esperado    = (idx_anterior + 1) % len(ruta)
            es_reinicio = (idx_anterior == len(ruta) - 1 and idx_actual == 0)

            if not es_reinicio and idx_actual != esperado:
                status_resp = "warning"
                msgs.append(f"Orden incorrecto (Tocaba '{ruta[esperado].nombre_zona}')")

    # 5. Guardar en BD
    obs_usuario = data.get('observacion', '')
    obs_sistema = " | ".join(msgs)
    obs_final   = f"{obs_usuario} {f'[ALERTA: {obs_sistema}]' if msgs else ''}".strip()

    nuevo = RegistroRonda(
        id_guardia=id_guardia,
        id_punto=punto.id_punto,
        lat_real=data['lat'], long_real=data['long'],
        distancia_error=dist,
        observacion=obs_final,
        fecha_hora=datetime.now(timezone.utc)
    )

    try:
        db.session.add(nuevo)
        db.session.commit()
        nombre_proximo = obtener_siguiente_punto_nombre(punto.id_punto, id_sede_guardia)
        return jsonify({
            "status":        status_resp,
            "message":       f"Registrado: {punto.nombre_zona}. " + ("⚠️ " + ", ".join(msgs) if msgs else ""),
            "punto":         punto.nombre_zona,
            "proximo_punto": nombre_proximo,
            "hora":          datetime.now(ZONA_CL).strftime("%H:%M")
        }), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500