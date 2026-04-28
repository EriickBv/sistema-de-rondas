# app/api/rondas.py
import pytz
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify, current_app
from sqlalchemy.exc import IntegrityError
from app.extensions import db
from app.models import PuntoControl, RegistroRonda, RondaActiva, Guardia
from app.utils import token_required, calcular_distancia, get_ruta_guardia, build_checklist, coords_punto

rondas_bp = Blueprint('rondas', __name__)
ZONA_CL   = pytz.timezone('America/Santiago')


# ─── HELPERS PRIVADOS ────────────────────────────────────────────────────────

def _ronda_activa(id_guardia, lock=False):
    """
    Retorna la RondaActiva abierta del guardia, o None.
    PARCHE C-4: si lock=True, aplica SELECT ... FOR UPDATE para bloquear
    la fila durante el flujo de finalización y evitar dobles cierres.
    """
    q = RondaActiva.query.filter_by(id_guardia=id_guardia, fecha_fin=None)
    if lock:
        q = q.with_for_update()
    return q.first()


def _validar_sede(guardia, punto):
    """
    PARCHE A-1: Si el punto pertenece a una sede, el guardia DEBE pertenecer
    a la misma sede. Si el guardia no tiene sede asignada (id_sede IS NULL)
    NO puede escanear puntos sede-específicos. Solo los puntos globales
    (punto.id_sede IS NULL) son accesibles para todos.
    """
    if punto.id_sede is None:
        return True  # punto global: cualquiera puede escanearlo
    if not guardia or guardia.id_sede != punto.id_sede:
        return False
    return True


def _parse_gps(data: dict) -> tuple:
    """
    Extrae y valida lat/lon del payload.
    Retorna (lat, lon, dist_fallback) donde dist_fallback=999999 indica GPS inválido.
    """
    try:
        lat  = float(data.get('lat'))
        lon  = float(data.get('long'))
        return lat, lon, None
    except (TypeError, ValueError):
        return None, None, 999999.0


# ─── ENDPOINT: ESTADO ────────────────────────────────────────────────────────

@rondas_bp.route('/estado', methods=['GET'])
@token_required
def estado_ronda():
    """
    Retorna el estado actual de la ronda activa del guardia.
    Usado al cargar la app para restaurar el progreso del checklist
    sin que el guardia tenga que recordar dónde quedó.
    """
    id_guardia = request.usuario_id
    guardia    = Guardia.query.get(id_guardia)
    id_sede    = guardia.id_sede if guardia else None

    ronda = _ronda_activa(id_guardia)
    if not ronda:
        return jsonify({"ronda_activa": False}), 200

    checklist, completados, total = build_checklist(ronda.id_ronda, id_sede)

    return jsonify({
        "ronda_activa":  True,
        "id_ronda":      ronda.id_ronda,
        "fecha_inicio":  ronda.fecha_inicio.isoformat() + "Z",
        "checklist":     checklist,
        "progreso":      {"completados": completados, "total": total}
    }), 200


# ─── ENDPOINT: INICIAR ───────────────────────────────────────────────────────

@rondas_bp.route('/iniciar', methods=['POST'])
@token_required
def iniciar_ronda():
    """
    Abre una nueva sesión de ronda para el guardia.
    Rechaza si ya tiene una ronda activa (evita rondas fantasma).
    """
    id_guardia = request.usuario_id

    if _ronda_activa(id_guardia):
        return jsonify({
            "status":  "error",
            "message": "Ya tienes una ronda activa. Finalízala antes de iniciar una nueva."
        }), 409

    guardia = Guardia.query.get(id_guardia)
    id_sede = guardia.id_sede if guardia else None
    ruta    = get_ruta_guardia(id_sede)

    if not ruta:
        return jsonify({
            "status":  "error",
            "message": "No hay puntos de control configurados para tu instalación."
        }), 400

    nueva_ronda = RondaActiva(
        id_guardia=id_guardia,
        fecha_inicio=datetime.now(timezone.utc),
        estado='activa'
    )
    db.session.add(nueva_ronda)
    # PARCHE C-2: si dos requests simultáneos pasan el check anterior,
    # el unique index parcial (ver migración) hace que el segundo INSERT
    # falle con IntegrityError. Lo convertimos en 409 idempotente.
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({
            "status":  "error",
            "message": "Ya tienes una ronda activa. Finalízala antes de iniciar una nueva."
        }), 409

    checklist = [
        {"id_punto": p.id_punto, "nombre": p.nombre_zona, "completado": False}
        for p in ruta
    ]

    return jsonify({
        "status":    "success",
        "message":   "Ronda iniciada. Escanea los puntos en cualquier orden.",
        "id_ronda":  nueva_ronda.id_ronda,
        "checklist": checklist,
        "progreso":  {"completados": 0, "total": len(ruta)}
    }), 201


# ─── ENDPOINT: ESCANEAR PUNTO ────────────────────────────────────────────────

@rondas_bp.route('/', methods=['POST'])
@token_required
def registrar_ronda():
    """
    Registra el escaneo de un punto QR dentro de la ronda activa.
    Validaciones:
      - QR existe en el sistema.
      - El punto pertenece a la instalación del guardia.
      - Existe una ronda activa.
      - El punto no fue ya escaneado en esta ronda (anti-duplicado).
      - GPS dentro del radio permitido (warning, no bloqueo).
    """
    id_guardia = request.usuario_id
    data = request.get_json(silent=True)
    if not data or 'token_qr' not in data:
        return jsonify({"status": "error", "message": "Payload inválido o Content-Type incorrecto"}), 400

    # 1. Validar QR
    punto = PuntoControl.query.filter_by(token_qr=data['token_qr']).first()
    if not punto:
        return jsonify({"status": "error", "message": "QR no registrado en el sistema"}), 404

    # 2. Validar Sede (PARCHE A-1)
    guardia = Guardia.query.get(id_guardia)
    if not _validar_sede(guardia, punto):
        return jsonify({"status": "error", "message": "Este punto pertenece a otra instalación"}), 403

    # 3. Verificar ronda activa
    ronda = _ronda_activa(id_guardia)
    if not ronda:
        return jsonify({
            "status":  "error",
            "message": "No tienes una ronda activa. Inicia una ronda primero."
        }), 409

    # 4. Anti-duplicado: ¿ya fue escaneado en esta ronda?
    ya_escaneado = RegistroRonda.query.filter_by(
        id_ronda=ronda.id_ronda,
        id_punto=punto.id_punto
    ).first()
    if ya_escaneado:
        return jsonify({
            "status":  "warning",
            "message": f"'{punto.nombre_zona}' ya fue registrado en esta ronda."
        }), 409

    # 5. GPS
    # PARCHE SRE-5: punto.latitud/longitud son nullable. Antes `float(None)`
    # crashaba con TypeError y `dist > None` era TypeError en Py3, lo que
    # rompía la ronda completa del guardia y le hacía perder progreso.
    p_lat, p_lon, radio = coords_punto(punto)
    if p_lat is None:
        return jsonify({
            "status":  "error",
            "message": f"El punto '{punto.nombre_zona or punto.id_punto}' no tiene coordenadas configuradas. Avisa al admin."
        }), 422

    lat, lon, dist_fallback = _parse_gps(data)
    if dist_fallback is not None:
        dist = dist_fallback
    else:
        dist = calcular_distancia(lat, lon, p_lat, p_lon)

    msgs        = []
    status_resp = "success"
    if dist > radio:
        status_resp = "warning"
        msgs.append(f"Lejos ({int(dist)}m)")

    # 6. Guardar registro
    obs_usuario = data.get('observacion', '')
    obs_sistema = " | ".join(msgs)
    obs_final   = f"{obs_usuario} {f'[ALERTA: {obs_sistema}]' if msgs else ''}".strip()

    nuevo = RegistroRonda(
        id_guardia=id_guardia,
        id_punto=punto.id_punto,
        id_ronda=ronda.id_ronda,
        lat_real=lat,
        long_real=lon,
        distancia_error=dist,
        observacion=obs_final,
        fecha_hora=datetime.now(timezone.utc)
    )
    db.session.add(nuevo)
    # PARCHE C-3: el unique constraint (id_ronda, id_punto) blinda contra
    # races. Si dos escaneos del mismo QR llegan en la misma milésima,
    # el segundo recibe IntegrityError y respondemos como duplicado.
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({
            "status":  "warning",
            "message": f"'{punto.nombre_zona}' ya fue registrado en esta ronda."
        }), 409

    # 7. Checklist actualizado
    id_sede = guardia.id_sede if guardia else None
    checklist, completados, total = build_checklist(ronda.id_ronda, id_sede)

    return jsonify({
        "status":    status_resp,
        "message":   f"✅ {punto.nombre_zona} registrado." + (f" ⚠️ {', '.join(msgs)}" if msgs else ""),
        "punto":     punto.nombre_zona,
        "checklist": checklist,
        "progreso":  {"completados": completados, "total": total},
        "hora":      datetime.now(ZONA_CL).strftime("%H:%M")
    }), 201


# ─── ENDPOINT: SALTAR PUNTO ──────────────────────────────────────────────────

@rondas_bp.route('/saltar', methods=['POST'])
@token_required
def saltar_punto():
    """
    Omite un punto específico de la ronda actual.
    A diferencia del sistema anterior, el guardia elige QUÉ punto saltar
    (id_punto en el payload) en lugar de saltarse el "siguiente en orden".
    Esto es coherente con el modelo de checklist aleatorio.
    """
    data   = request.get_json(silent=True) or {}
    motivo = data.get('motivo', '').strip()
    id_punto_saltar = data.get('id_punto')

    if not motivo:
        return jsonify({"status": "error", "message": "El motivo es obligatorio para omitir un punto"}), 400
    if not id_punto_saltar:
        return jsonify({"status": "error", "message": "Debes indicar qué punto deseas omitir (id_punto)"}), 400

    id_guardia = request.usuario_id
    guardia    = Guardia.query.get(id_guardia)
    id_sede    = guardia.id_sede if guardia else None

    ronda = _ronda_activa(id_guardia)
    if not ronda:
        return jsonify({"status": "error", "message": "No tienes una ronda activa"}), 409

    punto = PuntoControl.query.get(id_punto_saltar)
    if not punto:
        return jsonify({"status": "error", "message": "Punto no encontrado"}), 404

    # PARCHE A-1: validar que el guardia pueda saltar puntos solo de su sede
    if not _validar_sede(guardia, punto):
        return jsonify({"status": "error", "message": "Este punto pertenece a otra instalación"}), 403

    omision = RegistroRonda(
        id_guardia=id_guardia,
        id_punto=id_punto_saltar,
        id_ronda=ronda.id_ronda,
        lat_real=0.0,
        long_real=0.0,
        distancia_error=0.0,
        observacion=f"⚠️ OMITIDO: {motivo}",
        fecha_hora=datetime.now(timezone.utc)
    )
    db.session.add(omision)
    # PARCHE C-3: anti-duplicado a nivel DB
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({
            "status":  "warning",
            "message": "Este punto ya fue registrado en la ronda actual."
        }), 409

    checklist, completados, total = build_checklist(ronda.id_ronda, id_sede)

    return jsonify({
        "status":    "success",
        "message":   f"Punto '{punto.nombre_zona}' omitido.",
        "checklist": checklist,
        "progreso":  {"completados": completados, "total": total}
    }), 200


# ─── ENDPOINT: FINALIZAR ─────────────────────────────────────────────────────

@rondas_bp.route('/finalizar', methods=['POST'])
@token_required
def finalizar_ronda():
    """
    Cierra la ronda activa del guardia.

    Flujo de dos pasos para el caso con puntos pendientes:
      Paso 1 — El guardia llama sin 'justificacion':
               Si hay pendientes → HTTP 200 con status='pendientes'.
               El frontend muestra el diálogo de justificación.
      Paso 2 — El guardia llama con 'justificacion' en el payload:
               Los puntos pendientes se registran como OMITIDO AL CIERRE
               y la ronda se cierra con estado='finalizada_con_pendientes'.

    Si no hay puntos pendientes → cierra directamente con estado='completada'.
    """
    data       = request.get_json(silent=True) or {}
    id_guardia = request.usuario_id
    guardia    = Guardia.query.get(id_guardia)
    id_sede    = guardia.id_sede if guardia else None

    # PARCHE C-4: lock pesimista para serializar finalizaciones concurrentes.
    # SELECT ... FOR UPDATE bloquea la fila de la ronda hasta el commit,
    # de modo que un retry/segundo worker espera y al liberar el lock
    # ve fecha_fin != NULL y responde 404 limpiamente.
    ronda = _ronda_activa(id_guardia, lock=True)
    if not ronda:
        return jsonify({"status": "error", "message": "No hay una ronda activa para finalizar"}), 404

    checklist, completados, total = build_checklist(ronda.id_ronda, id_sede)
    ruta = get_ruta_guardia(id_sede)
    ids_completados = {p["id_punto"] for p in checklist if p["completado"]}
    pendientes      = [p for p in ruta if p.id_punto not in ids_completados]

    if pendientes:
        justificacion = data.get('justificacion', '').strip()
        if not justificacion:
            # Paso 1: pedir justificación al frontend
            # NOTA: liberamos el lock haciendo rollback (no hay cambios pendientes).
            db.session.rollback()
            return jsonify({
                "status":           "pendientes",
                "message":          "Hay puntos sin completar. Proporciona una justificación para cerrar la ronda.",
                "puntos_pendientes": [p.nombre_zona for p in pendientes],
                "cantidad":         len(pendientes)
            }), 200  # 200 intencional: no es un error, es un step del flujo

        # Paso 2: auto-omitir los pendientes y cerrar
        # PARCHE C-3: con el unique constraint, si por alguna razón un pendiente
        # ya fue registrado entre Paso 1 y Paso 2, el commit fallará y
        # se hace rollback limpio. Insertamos uno por uno en lugar de bulk
        # para poder filtrar duplicados antes del commit final.
        for p in pendientes:
            omision = RegistroRonda(
                id_guardia=id_guardia,
                id_punto=p.id_punto,
                id_ronda=ronda.id_ronda,
                lat_real=0.0, long_real=0.0,
                distancia_error=0.0,
                observacion=f"⚠️ OMITIDO AL CIERRE: {justificacion}",
                fecha_hora=datetime.now(timezone.utc)
            )
            db.session.add(omision)

        ronda.estado             = 'finalizada_con_pendientes'
        ronda.observacion_cierre = justificacion
    else:
        ronda.estado = 'completada'

    # PARCHE C-1: fecha_inicio viene NAIVE de MySQL (DateTime sin timezone),
    # mientras que datetime.now(timezone.utc) es AWARE. Restar aware - naive
    # lanza TypeError. Reetiquetamos fecha_inicio como UTC antes de operar
    # (el contenido SÍ es UTC, lo escribimos así en iniciar_ronda).
    #
    # PARCHE SRE-5: defensa contra fecha_inicio = NULL. El modelo declara
    # nullable=False pero eso solo se valida al INSERT. Una migración
    # parcial, restore manual o INSERT desde MySQL CLI puede dejar la
    # columna en NULL. Sin esta defensa, .replace() crashea con AttributeError
    # y el guardia se queda sin poder cerrar su ronda.
    ahora = datetime.now(timezone.utc)
    if ronda.fecha_inicio is None:
        current_app.logger.error(
            "Ronda %s con fecha_inicio NULL — usando 'ahora' como fallback",
            ronda.id_ronda
        )
        ronda.fecha_inicio = ahora
        duracion_min = 0
    else:
        inicio_aware = ronda.fecha_inicio.replace(tzinfo=timezone.utc)
        duracion_min = int((ahora - inicio_aware).total_seconds() / 60)
    ronda.fecha_fin = ahora

    try:
        db.session.commit()
    except IntegrityError:
        # Algún OMITIDO AL CIERRE colisionó con un registro existente.
        # La ronda no quedó cerrada — el cliente debe reintentar.
        db.session.rollback()
        return jsonify({
            "status":  "error",
            "message": "Conflicto al cerrar la ronda. Intenta nuevamente."
        }), 409

    return jsonify({
        "status":           "success",
        "message":          f"Ronda finalizada. Duración: {duracion_min} min.",
        "estado":           ronda.estado,
        "duracion_minutos": duracion_min
    }), 200


# ─── ENDPOINT: ADMIN — RONDAS DEL DÍA ───────────────────────────────────────

@rondas_bp.route('/admin/activas', methods=['GET'])
@token_required
def rondas_activas_admin():
    """
    Vista de supervisor: rondas abiertas en este momento para todas las sedes.
    Permite al Admin saber qué guardias están en ronda y cuánto llevan.
    """
    if request.usuario_rol != 'admin':
        return jsonify({"message": "No autorizado"}), 403

    rondas = RondaActiva.query.filter_by(fecha_fin=None).all()
    ahora  = datetime.now(timezone.utc)

    resultado = []
    for r in rondas:
        # PARCHE SRE-5: skip defensivo. Si una fila tiene fecha_inicio NULL,
        # no debe romper el loop entero (eso dejaba al admin sin consola).
        # La logueamos para que el equipo pueda investigar la inconsistencia.
        if r.fecha_inicio is None:
            current_app.logger.error("RondaActiva %s con fecha_inicio NULL — saltando en /admin/activas", r.id_ronda)
            continue
        guardia = r.guardia
        try:
            minutos = int((ahora - r.fecha_inicio.replace(tzinfo=timezone.utc)).total_seconds() / 60)
        except Exception:
            current_app.logger.exception("Error calculando minutos para ronda %s", r.id_ronda)
            minutos = 0
        id_sede = guardia.id_sede if guardia else None
        try:
            _, completados, total = build_checklist(r.id_ronda, id_sede)
        except Exception:
            current_app.logger.exception("Error construyendo checklist para ronda %s", r.id_ronda)
            completados, total = 0, 0

        resultado.append({
            "id_ronda":    r.id_ronda,
            "guardia":     guardia.nombre if guardia else "Guardia Eliminado",
            "sede":        guardia.sede.nombre if guardia and guardia.sede else "Sin Sede",
            "inicio":      r.fecha_inicio.isoformat() + "Z",
            "minutos":     minutos,
            "progreso":    {"completados": completados, "total": total}
        })

    return jsonify(resultado), 200