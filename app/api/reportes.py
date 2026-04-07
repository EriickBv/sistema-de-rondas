# app/api/reportes.py
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
from app.models import Guardia, RegistroRonda, PuntoControl, DestinatarioReporte
from app.extensions import db
from app.utils import token_required

reportes_bp = Blueprint('reportes', __name__)
ZONA_CL = pytz.timezone('America/Santiago')


def _rango_utc(desde_str: str, hasta_str: str):
    """Convierte fechas locales 'YYYY-MM-DD' a datetimes UTC para filtrar la BD."""
    desde = ZONA_CL.localize(datetime.datetime.strptime(desde_str, '%Y-%m-%d'))
    hasta = ZONA_CL.localize(
        datetime.datetime.strptime(hasta_str, '%Y-%m-%d')
        .replace(hour=23, minute=59, second=59, microsecond=999999)
    )
    return desde.astimezone(pytz.utc), hasta.astimezone(pytz.utc)


def _query_registros(desde_utc=None, hasta_utc=None):
    q = RegistroRonda.query
    if desde_utc:
        q = q.filter(RegistroRonda.fecha_hora >= desde_utc)
    if hasta_utc:
        q = q.filter(RegistroRonda.fecha_hora <= hasta_utc)
    return q.order_by(RegistroRonda.fecha_hora.desc()).all()


def _filas_csv(registros):
    """Generador de tuplas de datos. Reutilizable para streaming y para email."""
    for r in registros:
        fecha_local = r.fecha_hora.replace(tzinfo=pytz.utc).astimezone(ZONA_CL)
        yield (
            fecha_local.strftime('%Y-%m-%d %H:%M:%S'),
            r.guardia.nombre if r.guardia else "Guardia Eliminado",
            r.guardia.rut    if r.guardia else "N/A",
            r.punto.nombre_zona if r.punto else "Zona Eliminada",
            r.observacion or '',
            f"{r.distancia_error:.2f}" if r.distancia_error else "0.00"
        )


HEADER_CSV = ('Fecha y Hora', 'Guardia', 'RUT', 'Punto de Control',
              'Observacion', 'Distancia Error (m)')



@reportes_bp.route('/', methods=['GET'])
@token_required
def obtener_reportes():
    tipo = request.args.get('tipo')

    if tipo == 'hoy':
        hoy = datetime.date.today()
        registros = RegistroRonda.query.filter(
            RegistroRonda.fecha_hora >= datetime.datetime.combine(hoy, datetime.time.min),
            RegistroRonda.fecha_hora <= datetime.datetime.combine(hoy, datetime.time.max)
        ).order_by(RegistroRonda.fecha_hora.asc()).all()

        data = []
        ultima_hora_guardia = {}
        TIEMPO_MAXIMO = 15

        for r in registros:
            if r.punto:
                nombre_zona    = r.punto.nombre_zona
                numero_orden   = r.punto.numero_orden
                radio_permitido = r.punto.radio_permitido or 20
            else:
                nombre_zona    = "🚫 Zona Eliminada"
                numero_orden   = -1
                radio_permitido = 20

            llegada_tarde  = False
            minutos_pasados = 0
            es_inicio_ronda = (numero_orden == 1)

            if r.id_guardia in ultima_hora_guardia and not es_inicio_ronda:
                delta = r.fecha_hora - ultima_hora_guardia[r.id_guardia]
                minutos_pasados = delta.total_seconds() / 60
                if minutos_pasados > TIEMPO_MAXIMO:
                    llegada_tarde = True

            ultima_hora_guardia[r.id_guardia] = r.fecha_hora
            esta_lejos = r.distancia_error > 20

            if not llegada_tarde and not esta_lejos:
                estado = "✅ Correcto"
            elif llegada_tarde and not esta_lejos:
                estado = f"⏰ Tarde (+{int(minutos_pasados - TIEMPO_MAXIMO)}m)"
            elif not llegada_tarde and esta_lejos:
                estado = f"⚠️ Lejos ({int(r.distancia_error)}m)"
            else:
                estado = "🚨 CRÍTICO (Tarde y Lejos)"

            if es_inicio_ronda:
                estado = "🏁 Inicio Ronda " + ("(Lejos)" if esta_lejos else "(Ok)")

            data.append({
                "Guardia":     r.guardia.nombre if r.guardia else "Guardia Eliminado",
                "Punto":       nombre_zona,
                "Hora":        r.fecha_hora.isoformat() + "Z",
                "Distancia":   f"{int(r.distancia_error)}m" if r.distancia_error > 0 else "0m",
                "Estado":      estado,
                "Observacion": r.observacion
            })

        data.reverse()
        return jsonify(data), 200

    elif tipo == 'guardias':
        guardias = Guardia.query.all()
        return jsonify([{
            "ID": g.id_guardia, "Nombre": g.nombre,
            "RUT": g.rut, "Email": g.email, "Activo": g.activo
        } for g in guardias]), 200

    elif tipo == 'puntos':
        puntos = PuntoControl.query.order_by(PuntoControl.numero_orden).all()
        return jsonify([{
            "ID": p.id_punto, "Nombre": p.nombre_zona,
            "Token": p.token_qr, "Latitud": float(p.latitud),
            "Longitud": float(p.longitud), "Orden": p.numero_orden
        } for p in puntos]), 200

    return jsonify({"message": "Tipo de reporte no válido"}), 400


@reportes_bp.route('/exportar', methods=['GET'])
@token_required
def exportar_csv():
    desde_str = request.args.get('desde')  # YYYY-MM-DD, opcional
    hasta_str = request.args.get('hasta')  # YYYY-MM-DD, opcional

    desde_utc = hasta_utc = None
    if desde_str and hasta_str:
        try:
            desde_utc, hasta_utc = _rango_utc(desde_str, hasta_str)
        except ValueError:
            return jsonify({"message": "Formato inválido. Usa YYYY-MM-DD"}), 400

    def stream():
        buf = io.StringIO()
        w   = csv.writer(buf, delimiter=';')
        yield '\ufeff'  # BOM para Excel
        w.writerow(HEADER_CSV)
        yield buf.getvalue(); buf.seek(0); buf.truncate(0)

        for fila in _filas_csv(_query_registros(desde_utc, hasta_utc)):
            w.writerow(fila)
            yield buf.getvalue(); buf.seek(0); buf.truncate(0)

    if desde_str and hasta_str:
        nombre = f"Reporte_{desde_str}_a_{hasta_str}.csv"
    else:
        nombre = f"Reporte_Completo_{datetime.date.today().isoformat()}.csv"

    resp = Response(stream_with_context(stream()), mimetype='text/csv')
    resp.headers.set('Content-Disposition', 'attachment', filename=nombre)
    return resp



@reportes_bp.route('/destinatarios', methods=['GET'])
@token_required
def listar_destinatarios():
    if request.usuario_rol != 'admin':
        return jsonify({"message": "No autorizado"}), 403
    items = DestinatarioReporte.query.all()
    return jsonify([{"id": d.id, "email": d.email, "activo": d.activo}
                    for d in items]), 200


@reportes_bp.route('/destinatarios', methods=['POST'])
@token_required
def agregar_destinatario():
    if request.usuario_rol != 'admin':
        return jsonify({"message": "No autorizado"}), 403
    email = (request.get_json() or {}).get('email', '').strip().lower()
    if not email or '@' not in email:
        return jsonify({"message": "Email inválido"}), 400
    if DestinatarioReporte.query.filter_by(email=email).first():
        return jsonify({"message": "Este email ya existe"}), 400
    dest = DestinatarioReporte(email=email)
    db.session.add(dest)
    db.session.commit()
    return jsonify({"message": f"'{email}' agregado", "id": dest.id}), 201


@reportes_bp.route('/destinatarios/<int:id>', methods=['DELETE'])
@token_required
def eliminar_destinatario(id):
    if request.usuario_rol != 'admin':
        return jsonify({"message": "No autorizado"}), 403
    dest = DestinatarioReporte.query.get_or_404(id)
    db.session.delete(dest)
    db.session.commit()
    return jsonify({"message": "Eliminado"}), 200



def _csv_en_memoria(registros) -> bytes:
    buf = io.StringIO()
    w   = csv.writer(buf, delimiter=';')
    w.writerow(HEADER_CSV)
    for fila in _filas_csv(registros):
        w.writerow(fila)
    return ('\ufeff' + buf.getvalue()).encode('utf-8')


def enviar_reporte_diario():
    """
    Envía el CSV del día anterior a todos los destinatarios activos.
    Debe ejecutarse dentro de app context (APScheduler lo garantiza).
    """
    ayer = datetime.date.today() - datetime.timedelta(days=1)
    desde_utc, hasta_utc = _rango_utc(ayer.isoformat(), ayer.isoformat())
    registros    = _query_registros(desde_utc, hasta_utc)
    destinatarios = DestinatarioReporte.query.filter_by(activo=True).all()

    if not destinatarios:
        print("ℹ️  Sin destinatarios activos, se omite el envío.")
        return

    mail_server   = current_app.config.get('MAIL_SERVER')
    mail_port     = current_app.config.get('MAIL_PORT', 587)
    mail_user     = current_app.config.get('MAIL_USERNAME')
    mail_password = current_app.config.get('MAIL_PASSWORD')
    mail_from     = current_app.config.get('MAIL_FROM', mail_user)

    if not all([mail_server, mail_user, mail_password]):
        print("❌  SMTP no configurado. Revisa las variables MAIL_* en .env")
        return

    emails_destino = [d.email for d in destinatarios]
    csv_bytes      = _csv_en_memoria(registros)
    nombre_archivo = f"Reporte_{ayer.isoformat()}.csv"
    n              = len(registros)

    msg           = MIMEMultipart()
    msg['From']   = mail_from
    msg['To']     = ', '.join(emails_destino)
    msg['Subject'] = (f"📋 Reporte de Rondas — {ayer.strftime('%d/%m/%Y')} "
                      f"({n} registro{'s' if n != 1 else ''})")

    cuerpo = (
        f"Estimado/a,\n\n"
        f"Adjunto encontrará el reporte de rondas del {ayer.strftime('%d/%m/%Y')}.\n"
        f"Total de registros: {n}\n\n"
        f"Este mensaje fue generado automáticamente.\n"
        f"— Sistema de Control de Rondas"
    )
    msg.attach(MIMEText(cuerpo, 'plain', 'utf-8'))

    part = MIMEBase('application', 'octet-stream')
    part.set_payload(csv_bytes)
    encoders.encode_base64(part)
    part.add_header('Content-Disposition', 'attachment', filename=nombre_archivo)
    msg.attach(part)

    try:
        with smtplib.SMTP(mail_server, mail_port) as server:
            server.ehlo()
            server.starttls()
            server.login(mail_user, mail_password)
            server.sendmail(mail_from, emails_destino, msg.as_string())
        print(f"✅  Reporte enviado a: {', '.join(emails_destino)}")
    except Exception as e:
        print(f"❌  Error SMTP: {e}")


@reportes_bp.route('/enviar_reporte', methods=['POST'])
@token_required
def enviar_reporte_manual():
    """Trigger manual desde el panel de admin para probar el envío."""
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

    puntos    = PuntoControl.query.all()
    lista_qrs = []

    for p in puntos:
        qr = qrcode.QRCode(version=1, box_size=10, border=4)
        qr.add_data(p.token_qr)
        qr.make(fit=True)
        img    = qr.make_image(fill='black', back_color='white')
        buffer = BytesIO()
        img.save(buffer, format="PNG")
        lista_qrs.append({
            "nombre": p.nombre_zona,
            "token":  p.token_qr,
            "img":    base64.b64encode(buffer.getvalue()).decode()
        })

    return render_template('print_qrs.html', qrs=lista_qrs)