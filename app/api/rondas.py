from flask import Blueprint, request, jsonify
from datetime import datetime, timezone
from app.extensions import db
from app.models import PuntoControl, RegistroRonda
from app.utils import token_required, calcular_distancia

rondas_bp = Blueprint('rondas', __name__)

# --- FUNCIÓN AUXILIAR PARA CALCULAR EL PRÓXIMO PUNTO ---
def obtener_siguiente_punto_nombre(id_punto_actual):
    todos = PuntoControl.query.order_by(PuntoControl.numero_orden).all()
    if not todos: return "Fin"
    
    # Buscamos el índice del punto actual
    idx_actual = -1
    for i, p in enumerate(todos):
        if p.id_punto == id_punto_actual:
            idx_actual = i
            break
            
    # Calculamos el siguiente (circular: si es el último, vuelve al primero)
    if idx_actual != -1:
        idx_siguiente = (idx_actual + 1) % len(todos)
        return todos[idx_siguiente].nombre_zona
    return "Desconocido"


@rondas_bp.route('/saltar', methods=['POST'])
@token_required
def saltar_punto():
    data = request.get_json()
    motivo = data.get('motivo', 'Sin motivo especificado')
    id_guardia = request.usuario_id 
    
    # Buscar último registro
    ultimo_registro = RegistroRonda.query.filter_by(id_guardia=id_guardia)\
        .order_by(RegistroRonda.fecha_hora.desc()).first()
    
    todos_puntos = PuntoControl.query.order_by(PuntoControl.numero_orden).all()
    
    # Calcular cuál toca saltar
    siguiente_indice = 0
    if ultimo_registro and ultimo_registro.punto: # Validación extra por si punto fue borrado
        for i, p in enumerate(todos_puntos):
            if p.id_punto == ultimo_registro.id_punto:
                siguiente_indice = (i + 1) % len(todos_puntos)
                break
    
    # Si no hay puntos creados, evitar error
    if not todos_puntos:
        return jsonify({"message": "No hay puntos configurados"}), 400

    punto_objetivo = todos_puntos[siguiente_indice]

    # Guardar Salto
    nuevo_registro = RegistroRonda(
        fecha_hora=datetime.now(timezone.utc),
        id_guardia=id_guardia, 
        id_punto=punto_objetivo.id_punto,
        lat_real=0.0, long_real=0.0, distancia_error=0.0, 
        observacion=f"⚠️ OMITIDO: {motivo}" 
    )
    db.session.add(nuevo_registro)
    db.session.commit()

    # Calcular nombre del PRÓXIMO para la UI
    nombre_proximo = obtener_siguiente_punto_nombre(punto_objetivo.id_punto)

    return jsonify({
        'message': f'Punto "{punto_objetivo.nombre_zona}" saltado.',
        'proximo_punto': nombre_proximo,
        'status': 'success'
    }), 200


@rondas_bp.route('/', methods=['POST'])
@token_required
def registrar_ronda():
    id_guardia = request.usuario_id
    data = request.get_json()
    
    # 1. Validar QR
    punto = PuntoControl.query.filter_by(token_qr=data['token_qr']).first()
    if not punto:
        return jsonify({"status": "error", "message": "QR no registrado en el sistema"}), 404

    # 2. Calcular Distancia
    try:
        lat, lon = float(data['lat']), float(data['long'])
        dist = calcular_distancia(lat, lon, float(punto.latitud), float(punto.longitud))
    except:
        # Si fallan las coordenadas, asumimos que está LEJÍSIMOS o error
        dist = 999999.0

    # Variables de respuesta
    status_resp = "success"
    msgs = []

    # A) Validar Distancia (Permisivo)
    if dist > punto.radio_permitido:
        status_resp = "warning"
        msgs.append(f"Lejos ({int(dist)}m)")

    ultimo = RegistroRonda.query.filter(
        RegistroRonda.id_guardia == id_guardia,
        db.func.date(RegistroRonda.fecha_hora) == datetime.now().date()
    ).order_by(RegistroRonda.fecha_hora.desc()).first()

    if ultimo and ultimo.punto: # Validación extra por si punto fue borrado
        orden_anterior = ultimo.punto.numero_orden
        orden_actual = punto.numero_orden
        
        # Obtenemos el máximo orden para saber si es reinicio
        max_orden = db.session.query(db.func.max(PuntoControl.numero_orden)).scalar()
        es_reinicio = (orden_anterior == max_orden and orden_actual == 1)

        # Si no es consecutivo ni reinicio
        if not es_reinicio and orden_actual != (orden_anterior + 1):
            status_resp = "warning"
            msgs.append(f"Orden incorrecto (Tocaba el #{orden_anterior + 1})")

    # 3. Guardar en BD
    obs_usuario = data.get('observacion', '')
    obs_sistema = " | ".join(msgs)
    obs_final = f"{obs_usuario} {f'[ALERTA: {obs_sistema}]' if msgs else ''}".strip()

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
        
        # 4. Obtener nombre del SIGUIENTE punto para la App
        nombre_proximo = obtener_siguiente_punto_nombre(punto.id_punto)

        return jsonify({
            "status": status_resp, 
            "message": f"Registrado: {punto.nombre_zona}. " + ("⚠️ " + ", ".join(msgs) if msgs else ""), 
            "punto": punto.nombre_zona,
            "proximo_punto": nombre_proximo,
            "hora": datetime.now().strftime("%H:%M")
        }), 201
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500