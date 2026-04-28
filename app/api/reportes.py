from __future__ import annotations
import csv
import io
import pytz
import datetime
import smtplib
import qrcode
import base64
from io import BytesIO
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

from flask import (Blueprint, jsonify, request, Response,
                   stream_with_context, render_template, current_app)
from app.models import (Guardia, RegistroRonda, PuntoControl,
                        DestinatarioReporte, Sede, RegistroAsistencia, AlertaPanico)
from app.extensions import db
from app.utils import token_required, rango_hoy_utc

reportes_bp = Blueprint('reportes', __name__)
ZONA_CL = pytz.timezone('America/Santiago')

# ─── HELPERS UNIFICADOS ──────────────────────────────────────────────────────

def _rango_utc(desde_str: str, hasta_str: str):
    """
    PARCHE A-4: rango SEMI-ABIERTO [desde_00:00, hasta+1día_00:00).
    Equivale a "hasta inclusive" pero sin depender de microsegundos
    que MySQL DATETIME(0) trunca. Los consumidores deben usar
    `>= desde_utc AND < hasta_utc` (NO between).
    """
    desde = ZONA_CL.localize(datetime.datetime.strptime(desde_str, '%Y-%m-%d'))
    hasta_dt = (datetime.datetime.strptime(hasta_str, '%Y-%m-%d')
                + datetime.timedelta(days=1))
    hasta = ZONA_CL.localize(hasta_dt)
    return desde.astimezone(pytz.utc), hasta.astimezone(pytz.utc)

def _query_unificado(desde_utc=None, hasta_utc=None, id_sede=None):
    """
    PARCHE A-4: filtros con `< hasta_utc` (no `<=`) coherente con _rango_utc
    semi-abierto.

    PARCHE SRE-9 + SRE-21: dos mejoras combinadas.
      - SRE-21 (N+1): antes el bucle hacía r.guardia.sede.nombre que disparaba
        2 queries lazy por cada registro. Para 10k filas eso eran 20k queries
        adicionales. Ahora usamos joinedload para hidratar guardia+sede+punto
        en el mismo SELECT.
      - SRE-9 (memoria): antes se hacía registros.append() de TODO y luego
        registros.sort(), materializando todo en RAM antes de devolver. Ahora
        cada query se ordena en DB (DESC) y mergeamos los 3 streams ordenados
        con heapq.merge — no se materializa más que un buffer pequeño.

    NOTA: para evitar romper los consumidores que esperan una list, la función
    sigue devolviendo una list. Pero los consumidores que streamean (CSV) ya
    no pagan el costo del sort en Python.
    """
    import heapq
    from sqlalchemy.orm import joinedload

    def _materializar(query):
        for obj in query.yield_per(500):
            yield obj

    # 1. RONDAS
    q_rondas = (RegistroRonda.query
                .options(joinedload(RegistroRonda.guardia).joinedload(Guardia.sede),
                         joinedload(RegistroRonda.punto)))
    if desde_utc: q_rondas = q_rondas.filter(RegistroRonda.fecha_hora >= desde_utc)
    if hasta_utc: q_rondas = q_rondas.filter(RegistroRonda.fecha_hora <  hasta_utc)
    if id_sede is not None:
        q_rondas = q_rondas.join(Guardia, RegistroRonda.id_guardia == Guardia.id_guardia, isouter=True).filter(Guardia.id_sede == id_sede)
    q_rondas = q_rondas.order_by(RegistroRonda.fecha_hora.desc())

    def _gen_rondas():
        for r in _materializar(q_rondas):
            yield {
                'tipo_evento': 'ronda', 'fecha_hora': r.fecha_hora, 'guardia': r.guardia,
                'sede_nombre': r.guardia.sede.nombre if r.guardia and r.guardia.sede else 'Sin Sede',
                'punto_nombre': r.punto.nombre_zona if r.punto else 'Zona Eliminada',
                'observacion': r.observacion or '', 'distancia': r.distancia_error, 'id_ronda': r.id_ronda,
                'radio': r.punto.radio_permitido if r.punto and r.punto.radio_permitido is not None else 20
            }

    # 2. ASISTENCIA
    q_asis = (RegistroAsistencia.query
              .options(joinedload(RegistroAsistencia.guardia).joinedload(Guardia.sede)))
    if desde_utc: q_asis = q_asis.filter(RegistroAsistencia.fecha_hora >= desde_utc)
    if hasta_utc: q_asis = q_asis.filter(RegistroAsistencia.fecha_hora <  hasta_utc)
    if id_sede is not None:
        q_asis = q_asis.join(Guardia, RegistroAsistencia.id_guardia == Guardia.id_guardia, isouter=True).filter(Guardia.id_sede == id_sede)
    q_asis = q_asis.order_by(RegistroAsistencia.fecha_hora.desc())

    def _gen_asis():
        for a in _materializar(q_asis):
            yield {
                'tipo_evento': 'asistencia', 'fecha_hora': a.fecha_hora, 'guardia': a.guardia,
                'sede_nombre': a.guardia.sede.nombre if a.guardia and a.guardia.sede else 'Sin Sede',
                'punto_nombre': '📥 Entrada de Turno' if a.tipo == 'entrada' else '🚪 Salida de Turno',
                'observacion': 'Marca de GPS', 'distancia': None, 'id_ronda': None, 'radio': 0
            }

    # 3. PÁNICO
    q_pan = (AlertaPanico.query
             .options(joinedload(AlertaPanico.guardia).joinedload(Guardia.sede)))
    if desde_utc: q_pan = q_pan.filter(AlertaPanico.fecha_hora >= desde_utc)
    if hasta_utc: q_pan = q_pan.filter(AlertaPanico.fecha_hora <  hasta_utc)
    if id_sede is not None:
        q_pan = q_pan.join(Guardia, AlertaPanico.id_guardia == Guardia.id_guardia, isouter=True).filter(Guardia.id_sede == id_sede)
    q_pan = q_pan.order_by(AlertaPanico.fecha_hora.desc())

    def _gen_pan():
        for p in _materializar(q_pan):
            yield {
                'tipo_evento': 'panico', 'fecha_hora': p.fecha_hora, 'guardia': p.guardia,
                'sede_nombre': p.guardia.sede.nombre if p.guardia and p.guardia.sede else 'Sin Sede',
                'punto_nombre': '🚨 EVENTO CRÍTICO', 'observacion': 'Pánico Atendido' if p.atendida else 'Pánico Pendiente',
                'distancia': None, 'id_ronda': None, 'radio': 0
            }

    # heapq.merge: cada generador viene ordenado DESC, mergeamos en orden.
    # Esto NO materializa todo en memoria — solo 3 elementos a la vez.
    # Devolvemos una list para no romper consumidores existentes.
    merged = heapq.merge(_gen_rondas(), _gen_asis(), _gen_pan(),
                         key=lambda x: x['fecha_hora'], reverse=True)
    return list(merged)

HEADER_CSV = ('Fecha y Hora', 'Tipo Evento', 'Sede', 'Guardia', 'RUT', 'Detalle / Punto', 'Observacion', 'Distancia Error (m)')

def _filas_csv(registros):
    for r in registros:
        fecha_local = r['fecha_hora'].replace(tzinfo=pytz.utc).astimezone(ZONA_CL)
        yield (
            fecha_local.strftime('%Y-%m-%d %H:%M:%S'),
            r['tipo_evento'].upper(),
            r['sede_nombre'],
            r['guardia'].nombre if r['guardia'] else "Guardia Eliminado",
            r['guardia'].rut if r['guardia'] else "N/A",
            r['punto_nombre'],
            r['observacion'],
            f"{r['distancia']:.2f}" if r['distancia'] is not None else ""
        )

def _csv_en_memoria(registros) -> bytes:
    buf = io.StringIO()
    w   = csv.writer(buf, delimiter=';')
    w.writerow(HEADER_CSV)
    for fila in _filas_csv(registros):
        w.writerow(fila)
    return ('\ufeff' + buf.getvalue()).encode('utf-8')

# ─── REPORTES DASHBOARD ──────────────────────────────────────────────────────

@reportes_bp.route('/', methods=['GET'])
@token_required
def obtener_reportes():
    tipo = request.args.get('tipo')
    id_sede_str = request.args.get('id_sede')
    id_sede_filter = int(id_sede_str) if id_sede_str and id_sede_str.isdigit() else None
    if tipo == 'hoy':
        inicio_hoy_utc, fin_hoy_utc = rango_hoy_utc()
        registros = _query_unificado(inicio_hoy_utc, fin_hoy_utc, id_sede_filter)
        data = []

        for r in registros:
            estado = "Correcto"
            if r['tipo_evento'] == 'ronda':
                if "OMITIDO" in r['observacion']:
                    estado = "Omitido"
                elif r['distancia'] and r['distancia'] > r['radio']:
                    estado = f"Lejos ({int(r['distancia'])}m)"
            elif r['tipo_evento'] == 'asistencia':
                estado = "Asistencia"
            elif r['tipo_evento'] == 'panico':
                estado = "CRITICO"

            data.append({
                "tipo_evento": r['tipo_evento'],
                "Guardia":     r['guardia'].nombre if r['guardia'] else "Guardia Eliminado",
                "Punto":       r['punto_nombre'],
                "Sede":        r['sede_nombre'],
                "Hora":        r['fecha_hora'].isoformat() + "Z",
                "Distancia":   f"{int(r['distancia'])}m" if r['distancia'] is not None and r['distancia'] > 0 else "0m",
                "Estado":      estado,
                "Observacion": r['observacion'],
                "id_ronda":    r['id_ronda']
            })

        data.reverse()
        return jsonify(data), 200
    elif tipo == 'guardias':
        q = Guardia.query
        if id_sede_filter is not None:
            q = q.filter_by(id_sede=id_sede_filter)
        guardias = q.order_by(Guardia.nombre).all()
        return jsonify([{
            "ID": g.id_guardia,
            "Nombre": g.nombre,
            "RUT": g.rut,
            "Email": g.email,
            "id_sede": g.id_sede,
            "Activo": g.activo
        } for g in guardias]), 200
    elif tipo == 'puntos':
        q = PuntoControl.query
        if id_sede_filter is not None:
            q = q.filter_by(id_sede=id_sede_filter)
        puntos = q.order_by(PuntoControl.numero_orden).all()
        return jsonify([{
            "ID": p.id_punto,
            "Orden": p.numero_orden,
            "Nombre": p.nombre_zona,
            "id_sede": p.id_sede,
            # PARCHE SRE-6: `if p.latitud` falla con Decimal('0').
            "Latitud":  float(p.latitud)  if p.latitud  is not None else 0.0,
            "Longitud": float(p.longitud) if p.longitud is not None else 0.0
        } for p in puntos]), 200
    return jsonify({"error": "Tipo de reporte no válido o no especificado"}), 400

# ─── EXPORT CSV ──────────────────────────────────────────────────────────────

@reportes_bp.route('/exportar', methods=['GET'])
@token_required
def exportar_csv():
    desde_str   = request.args.get('desde')
    hasta_str   = request.args.get('hasta')
    id_sede_str = request.args.get('id_sede')
    id_sede     = int(id_sede_str) if id_sede_str and id_sede_str.isdigit() else None

    # PARCHE M-3: rango de fechas OBLIGATORIO. Antes, sin desde/hasta se
    # cargaban TODAS las filas (millones tras meses de uso) en memoria,
    # OOM-killing al worker (gunicorn corre con --workers 1 --threads 2,
    # un solo OOM tumba el servicio).
    if not (desde_str and hasta_str):
        return jsonify({"message": "Debes indicar 'desde' y 'hasta' (YYYY-MM-DD) para exportar."}), 400

    try:
        desde_utc, hasta_utc = _rango_utc(desde_str, hasta_str)
    except ValueError:
        return jsonify({"message": "Formato de fecha inválido. Usa YYYY-MM-DD"}), 400

    # Hard cap defensivo: máximo 365 días en una sola exportación.
    if (hasta_utc - desde_utc).days > 366:
        return jsonify({"message": "El rango máximo de exportación es 1 año."}), 400

    def stream():
        buf = io.StringIO()
        w   = csv.writer(buf, delimiter=';')
        yield '\ufeff'
        w.writerow(HEADER_CSV)
        yield buf.getvalue(); buf.seek(0); buf.truncate(0)
        for fila in _filas_csv(_query_unificado(desde_utc, hasta_utc, id_sede)):
            w.writerow(fila)
            yield buf.getvalue(); buf.seek(0); buf.truncate(0)

    sede_label = ""
    if id_sede:
        sede_obj = Sede.query.get(id_sede)
        if sede_obj:
            sede_label = f"_{sede_obj.nombre.replace(' ', '_')}"

    nombre = f"Reporte{sede_label}_{desde_str}_a_{hasta_str}.csv"

    resp = Response(stream_with_context(stream()), mimetype='text/csv')
    resp.headers.set('Content-Disposition', 'attachment', filename=nombre)
    return resp


# ─── DESTINATARIOS ───────────────────────────────────────────────────────────

@reportes_bp.route('/destinatarios', methods=['GET'])
@token_required
def listar_destinatarios():
    if request.usuario_rol != 'admin':
        return jsonify({"message": "No autorizado"}), 403

    items = DestinatarioReporte.query.all()
    return jsonify([{
        "id":      d.id,
        "email":   d.email,
        "activo":  d.activo,
        "id_sede": d.id_sede,
        # Cambia esta lógica para usar la relación:
        "sede_nombre": d.sede.nombre if d.id_sede and d.sede else "Global"
    } for d in items]), 200


@reportes_bp.route('/destinatarios', methods=['POST'])
@token_required
def agregar_destinatario():
    if request.usuario_rol != 'admin':
        return jsonify({"message": "No autorizado"}), 403

    payload = request.get_json() or {}
    email   = payload.get('email', '').strip().lower()
    if not email or '@' not in email:
        return jsonify({"message": "Email inválido"}), 400
    if DestinatarioReporte.query.filter_by(email=email).first():
        return jsonify({"message": "Este email ya existe"}), 400

    # NUEVO: id_sede opcional. None = global (recibe todos los reportes)
    id_sede_str = payload.get('id_sede')
    id_sede     = int(id_sede_str) if id_sede_str else None

    dest = DestinatarioReporte(email=email, id_sede=id_sede)
    # PARCHE SRE-3: try/except con rollback
    try:
        db.session.add(dest)
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Error al agregar destinatario '%s'", email)
        return jsonify({"message": "Error interno al agregar el destinatario"}), 500
    return jsonify({"message": f"'{email}' agregado", "id": dest.id}), 201


@reportes_bp.route('/destinatarios/<int:id>', methods=['DELETE'])
@token_required
def eliminar_destinatario(id):
    if request.usuario_rol != 'admin':
        return jsonify({"message": "No autorizado"}), 403
    dest = DestinatarioReporte.query.get_or_404(id)
    # PARCHE SRE-3: try/except con rollback
    try:
        db.session.delete(dest)
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Error al eliminar destinatario %s", id)
        return jsonify({"message": "Error interno al eliminar el destinatario"}), 500
    return jsonify({"message": "Eliminado"}), 200


# ─── EMAIL DIARIO — NUEVA LÓGICA DE DISTRIBUCIÓN POR SEDE ────────────────────

def _construir_y_enviar_correo(emails_destino: list, asunto: str,
                                cuerpo: str, adjuntos: list,
                                mail_cfg: dict):
    """
    Helper interno que construye y envía un MIMEMultipart.
    adjuntos: lista de (nombre_archivo: str, csv_bytes: bytes)
    """
    msg           = MIMEMultipart()
    msg['From']   = mail_cfg['from']
    msg['To']     = ', '.join(emails_destino)
    msg['Subject'] = asunto
    msg.attach(MIMEText(cuerpo, 'plain', 'utf-8'))

    for nombre_archivo, csv_bytes in adjuntos:
        part = MIMEBase('application', 'octet-stream')
        part.set_payload(csv_bytes)
        encoders.encode_base64(part)
        part.add_header('Content-Disposition', 'attachment', filename=nombre_archivo)
        msg.attach(part)

    # PARCHE SRE-7: timeout obligatorio. Sin él, si el SMTP no responde
    # (firewall corporativo, DNS roto, rate-limiting de Gmail) el hilo de
    # gunicorn queda colgado por horas. Con --threads 2, dos correos
    # colgados = servicio caído. 30s es suficiente para EHLO+STARTTLS+LOGIN
    # incluso en redes lentas.
    with smtplib.SMTP(mail_cfg['server'], mail_cfg['port'], timeout=30) as server:
        server.ehlo()
        server.starttls()
        server.login(mail_cfg['user'], mail_cfg['password'])
        server.sendmail(mail_cfg['from'], emails_destino, msg.as_string())


def enviar_reporte_diario():
    """
    Lógica de distribución:
      1. Se generan CSVs por sede (solo sedes con actividad).
      2. Destinatarios GLOBALES (id_sede=NULL): reciben UN correo con TODOS los CSVs adjuntos.
      3. Destinatarios de SEDE específica: reciben UN correo con SOLO el CSV de su sede.
         Si su sede no tuvo actividad, no reciben nada.
    """
    ayer = datetime.date.today() - datetime.timedelta(days=1)
    desde_utc, hasta_utc = _rango_utc(ayer.isoformat(), ayer.isoformat())

    # PARCHE SRE-13: print() → current_app.logger.* en todo este bloque.
    # En contenedor con gunicorn, los print iban a stdout sin nivel/timestamp,
    # por lo que un fallo en el envío de reportes era invisible. Ahora salen
    # con nivel y se integran con el resto de logs de Flask.
    log = current_app.logger

    destinatarios_activos = DestinatarioReporte.query.filter_by(activo=True).all()
    if not destinatarios_activos:
        log.info("Sin destinatarios activos, se omite el envío.")
        return

    mail_cfg = {
        'server':   current_app.config.get('MAIL_SERVER'),
        'port':     current_app.config.get('MAIL_PORT', 587),
        'user':     current_app.config.get('MAIL_USERNAME'),
        'password': current_app.config.get('MAIL_PASSWORD'),
        'from':     current_app.config.get('MAIL_FROM'),
    }
    if not all([mail_cfg['server'], mail_cfg['user'], mail_cfg['password']]):
        log.error("SMTP no configurado. Revisa las variables MAIL_* en .env")
        return
    # PARCHE SRE-19: validar que MAIL_FROM no quedó None (algunos SMTP
    # rechazan From vacío y eso lanza excepción no capturada).
    if not mail_cfg['from']:
        mail_cfg['from'] = mail_cfg['user']

    fecha_str = ayer.strftime('%d/%m/%Y')

    # Construir mapa { id_sede → (nombre_sede, csv_bytes) } solo para sedes con actividad
    sedes          = Sede.query.order_by(Sede.nombre).all()
    csvs_por_sede  = {}   # { id_sede: (nombre_sede, csv_bytes) }
    todos_adjuntos = []   # lista completa para el correo global

    for sede in sedes:
        registros = _query_unificado(desde_utc, hasta_utc, id_sede=sede.id_sede)
        if not registros:
            continue
        nombre_archivo = f"Reporte_{sede.nombre.replace(' ', '_')}_{ayer.isoformat()}.csv"
        csv_bytes      = _csv_en_memoria(registros)
        csvs_por_sede[sede.id_sede] = (sede.nombre, nombre_archivo, csv_bytes)
        todos_adjuntos.append((nombre_archivo, csv_bytes))

    # Guardiass sin sede
    registros_sin_sede = [
        r for r in _query_unificado(desde_utc, hasta_utc)
        if not r['guardia'] or r['guardia'].id_sede is None
    ]
    if registros_sin_sede:
        nombre_global = f"Reporte_SinSede_{ayer.isoformat()}.csv"
        bytes_global  = _csv_en_memoria(registros_sin_sede)
        csvs_por_sede[None] = ("Sin Sede", nombre_global, bytes_global)
        todos_adjuntos.append((nombre_global, bytes_global))

    if not todos_adjuntos:
        log.info("Sin actividad para %s. No se envía reporte.", fecha_str)
        return

    # Separar destinatarios globales de específicos
    emails_globales   = [d.email for d in destinatarios_activos if d.id_sede is None]
    por_sede          = {}   # { id_sede: [emails] }
    for d in destinatarios_activos:
        if d.id_sede is not None:
            por_sede.setdefault(d.id_sede, []).append(d.email)

    # Enviar correo global (todos los CSVs)
    if emails_globales and todos_adjuntos:
        sedes_cubiertas = ', '.join(v[0] for v in csvs_por_sede.values())
        asunto  = f"Reporte de Rondas — {fecha_str} ({len(todos_adjuntos)} archivo(s))"
        cuerpo  = (
            f"Se adjunta el reporte de rondas del {fecha_str}.\n"
            f"Instalaciones con actividad: {sedes_cubiertas}\n\n"
            f"Este mensaje fue generado automáticamente.\n"
            f"— Sistema de Control de Rondas"
        )
        try:
            _construir_y_enviar_correo(emails_globales, asunto, cuerpo,
                                       todos_adjuntos, mail_cfg)
            log.info("[Global] Reporte enviado a: %s", ', '.join(emails_globales))
        except Exception:
            log.exception("[Global] Error SMTP enviando reporte diario")

    # Enviar correo por sede a destinatarios específicos
    for id_sede, emails in por_sede.items():
        if id_sede not in csvs_por_sede:
            log.info("[Sede %s] Sin actividad ayer, no se envía.", id_sede)
            continue
        nombre_sede, nombre_archivo, csv_bytes = csvs_por_sede[id_sede]
        asunto = f"Reporte de Rondas — {nombre_sede} — {fecha_str}"
        cuerpo = (
            f"Se adjunta el reporte de rondas de {nombre_sede} del {fecha_str}.\n\n"
            f"Este mensaje fue generado automáticamente.\n"
            f"— Sistema de Control de Rondas"
        )
        try:
            _construir_y_enviar_correo(emails, asunto, cuerpo,
                                       [(nombre_archivo, csv_bytes)], mail_cfg)
            log.info("[%s] Reporte enviado a: %s", nombre_sede, ', '.join(emails))
        except Exception:
            log.exception("[%s] Error SMTP enviando reporte de sede", nombre_sede)


@reportes_bp.route('/enviar_reporte', methods=['POST'])
@token_required
def enviar_reporte_manual():
    if request.usuario_rol != 'admin':
        return jsonify({"message": "No autorizado"}), 403
    try:
        enviar_reporte_diario()
        return jsonify({"message": "Reporte enviado. Revisa la bandeja de los destinatarios."}), 200
    except Exception as e:
        return jsonify({"message": f"Error: {str(e)}"}), 500


@reportes_bp.route('/imprimir_qrs', methods=['GET'])
@token_required
def imprimir_qrs():
    if request.usuario_rol != 'admin':
        return jsonify({"message": "No permitido"}), 403
    id_sede_str = request.args.get('id_sede')
    id_sede     = int(id_sede_str) if id_sede_str and id_sede_str.isdigit() else None
    q = PuntoControl.query
    if id_sede:
        q = q.filter_by(id_sede=id_sede)
    puntos = q.order_by(PuntoControl.numero_orden).all()

    sede_titulo = "Todas las Instalaciones"
    if id_sede:
        sede_obj = Sede.query.get(id_sede)
        if sede_obj:
            sede_titulo = sede_obj.nombre

    lista_qrs = []
    for p in puntos:
        qr = qrcode.QRCode(version=1, box_size=10, border=4)
        qr.add_data(p.token_qr)
        qr.make(fit=True)
        img    = qr.make_image(fill='black', back_color='white')
        buffer = BytesIO()
        img.save(buffer, format="PNG")
        lista_qrs.append({
            "id":     p.id_punto,
            "nombre": p.nombre_zona,
            "token":  p.token_qr,
            "img":    base64.b64encode(buffer.getvalue()).decode()
        })

    return render_template('print_qrs.html', qrs=lista_qrs, sede_titulo=sede_titulo)