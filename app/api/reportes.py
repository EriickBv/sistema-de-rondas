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
from app.models import Guardia, RegistroRonda, PuntoControl, DestinatarioReporte, Sede
from app.extensions import db
from app.utils import token_required

reportes_bp = Blueprint('reportes', __name__)
ZONA_CL = pytz.timezone('America/Santiago')


def _rango_utc(desde_str: str, hasta_str: str):
    desde = ZONA_CL.localize(datetime.datetime.strptime(desde_str, '%Y-%m-%d'))
    hasta = ZONA_CL.localize(
        datetime.datetime.strptime(hasta_str, '%Y-%m-%d')
        .replace(hour=23, minute=59, second=59, microsecond=999999)
    )
    return desde.astimezone(pytz.utc), hasta.astimezone(pytz.utc)


def _query_registros(desde_utc=None, hasta_utc=None, id_sede=None):
    """
    Consulta registros con filtros opcionales de fecha y sede.
    El filtro de sede se aplica a través del guardia asociado al registro.
    """
    q = RegistroRonda.query
    if desde_utc:
        q = q.filter(RegistroRonda.fecha_hora >= desde_utc)
    if hasta_utc:
        q = q.filter(RegistroRonda.fecha_hora <= hasta_utc)
    if id_sede is not None:
        q = (q.join(Guardia, RegistroRonda.id_guardia == Guardia.id_guardia, isouter=True)
               .filter(Guardia.id_sede == id_sede))
    return q.order_by(RegistroRonda.fecha_hora.desc()).all()


# Encabezado CSV actualizado: incluye columna Sede
HEADER_CSV = ('Fecha y Hora', 'Sede', 'Guardia', 'RUT',
              'Punto de Control', 'Observacion', 'Distancia Error (m)')


def _filas_csv(registros):
    for r in registros:
        fecha_local = r.fecha_hora.replace(tzinfo=pytz.utc).astimezone(ZONA_CL)
        sede_nombre = (r.guardia.sede.nombre
                       if r.guardia and r.guardia.sede else "Sin Sede")
        yield (
            fecha_local.strftime('%Y-%m-%d %H:%M:%S'),
            sede_nombre,
            r.guardia.nombre if r.guardia else "Guardia Eliminado",
            r.guardia.rut    if r.guardia else "N/A",
            r.punto.nombre_zona if r.punto else "Zona Eliminada",
            r.observacion or '',
            f"{r.distancia_error:.2f}" if r.distancia_error else "0.00"
        )


@reportes_bp.route('/', methods=['GET'])
@token_required
def obtener_reportes():
    tipo = request.args.get('tipo')

    # Parseo del filtro de sede (opcional para todos los tipos)
    id_sede_str    = request.args.get('id_sede')
    id_sede_filter = int(id_sede_str) if id_sede_str and id_sede_str.isdigit() else None

    if tipo == 'hoy':
        hoy = datetime.date.today()
        q = RegistroRonda.query.filter(
            RegistroRonda.fecha_hora >= datetime.datetime.combine(hoy, datetime.time.min),
            RegistroRonda.fecha_hora <= datetime.datetime.combine(hoy, datetime.time.max)
        )
        if id_sede_filter:
            q = (q.join(Guardia, RegistroRonda.id_guardia == Guardia.id_guardia, isouter=True)
                   .filter(Guardia.id_sede == id_sede_filter))
        registros = q.order_by(RegistroRonda.fecha_hora.asc()).all()

        data = []
        ultima_hora_guardia = {}
        TIEMPO_MAXIMO = 15

        for r in registros:
            nombre_zona     = r.punto.nombre_zona     if r.punto else "Zona Eliminada"
            numero_orden    = r.punto.numero_orden    if r.punto else -1
            radio_permitido = r.punto.radio_permitido if r.punto else 20

            llegada_tarde   = False
            minutos_pasados = 0
            es_inicio_ronda = (numero_orden == 1)

            if r.id_guardia in ultima_hora_guardia and not es_inicio_ronda:
                delta = r.fecha_hora - ultima_hora_guardia[r.id_guardia]
                minutos_pasados = delta.total_seconds() / 60
                if minutos_pasados > TIEMPO_MAXIMO:
                    llegada_tarde = True

            ultima_hora_guardia[r.id_guardia] = r.fecha_hora
            esta_lejos = r.distancia_error > 20

            if   not llegada_tarde and not esta_lejos:
                estado = "Correcto"
            elif llegada_tarde and not esta_lejos:
                estado = f"Tarde (+{int(minutos_pasados - TIEMPO_MAXIMO)}m)"
            elif not llegada_tarde and esta_lejos:
                estado = f"Lejos ({int(r.distancia_error)}m)"
            else:
                estado = "CRITICO (Tarde y Lejos)"

            if es_inicio_ronda:
                estado = "Inicio Ronda" + (" (Lejos)" if esta_lejos else "")

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
        q = Guardia.query
        if id_sede_filter:
            q = q.filter_by(id_sede=id_sede_filter)
        guardias = q.all()
        return jsonify([{
            "ID":     g.id_guardia,
            "Nombre": g.nombre,
            "RUT":    g.rut,
            "Email":  g.email,
            "Activo": g.activo,
            "id_sede": g.id_sede
        } for g in guardias]), 200

    elif tipo == 'puntos':
        q = PuntoControl.query
        if id_sede_filter:
            q = q.filter_by(id_sede=id_sede_filter)
        puntos = q.order_by(PuntoControl.numero_orden).all()
        return jsonify([{
            "ID":       p.id_punto,
            "Nombre":   p.nombre_zona,
            "Token":    p.token_qr,
            "Latitud":  float(p.latitud),
            "Longitud": float(p.longitud),
            "Orden":    p.numero_orden,
            "id_sede":  p.id_sede
        } for p in puntos]), 200

    return jsonify({"message": "Tipo de reporte no válido"}), 400


@reportes_bp.route('/exportar', methods=['GET'])
@token_required
def exportar_csv():
    desde_str   = request.args.get('desde')
    hasta_str   = request.args.get('hasta')
    id_sede_str = request.args.get('id_sede')
    id_sede     = int(id_sede_str) if id_sede_str and id_sede_str.isdigit() else None

    desde_utc = hasta_utc = None
    if desde_str and hasta_str:
        try:
            desde_utc, hasta_utc = _rango_utc(desde_str, hasta_str)
        except ValueError:
            return jsonify({"message": "Formato de fecha inválido. Usa YYYY-MM-DD"}), 400

    def stream():
        buf = io.StringIO()
        w   = csv.writer(buf, delimiter=';')
        yield '\ufeff'
        w.writerow(HEADER_CSV)
        yield buf.getvalue(); buf.seek(0); buf.truncate(0)
        for fila in _filas_csv(_query_registros(desde_utc, hasta_utc, id_sede)):
            w.writerow(fila)
            yield buf.getvalue(); buf.seek(0); buf.truncate(0)

    # Nombre dinámico del archivo
    sede_label = ""
    if id_sede:
        sede_obj = Sede.query.get(id_sede)
        if sede_obj:
            sede_label = f"_{sede_obj.nombre.replace(' ', '_')}"

    if desde_str and hasta_str:
        nombre = f"Reporte{sede_label}_{desde_str}_a_{hasta_str}.csv"
    else:
        nombre = f"Reporte{sede_label}_Completo_{datetime.date.today().isoformat()}.csv"

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
    Genera un CSV por cada sede con actividad (+ uno global sin sede)
    y los adjunta todos en un único correo enviado a los destinatarios activos.
    """
    ayer = datetime.date.today() - datetime.timedelta(days=1)
    desde_utc, hasta_utc = _rango_utc(ayer.isoformat(), ayer.isoformat())

    destinatarios = DestinatarioReporte.query.filter_by(activo=True).all()
    if not destinatarios:
        print("Sin destinatarios activos, se omite el envío.")
        return

    mail_server   = current_app.config.get('MAIL_SERVER')
    mail_port     = current_app.config.get('MAIL_PORT', 587)
    mail_user     = current_app.config.get('MAIL_USERNAME')
    mail_password = current_app.config.get('MAIL_PASSWORD')
    mail_from     = current_app.config.get('MAIL_FROM', mail_user)

    if not all([mail_server, mail_user, mail_password]):
        print("SMTP no configurado. Revisa las variables MAIL_* en .env")
        return

    # Construir adjuntos: un CSV por sede con actividad
    adjuntos = []
    sedes = Sede.query.order_by(Sede.nombre).all()

    if sedes:
        for sede in sedes:
            registros_sede = _query_registros(desde_utc, hasta_utc, id_sede=sede.id_sede)
            if registros_sede:
                nombre_archivo = f"Reporte_{sede.nombre.replace(' ', '_')}_{ayer.isoformat()}.csv"
                adjuntos.append((nombre_archivo, _csv_en_memoria(registros_sede)))

        # Registros de guardias sin sede asignada
        registros_sin_sede = _query_registros(desde_utc, hasta_utc, id_sede=None)
        # Filtramos solo los que realmente no tienen sede (la query sin id_sede trae todos)
        registros_sin_sede = [r for r in _query_registros(desde_utc, hasta_utc)
                              if not r.guardia or r.guardia.id_sede is None]
        if registros_sin_sede:
            adjuntos.append((f"Reporte_SinSede_{ayer.isoformat()}.csv",
                             _csv_en_memoria(registros_sin_sede)))
    else:
        # Sin sedes creadas: un solo CSV global
        registros = _query_registros(desde_utc, hasta_utc)
        adjuntos.append((f"Reporte_{ayer.isoformat()}.csv", _csv_en_memoria(registros)))

    total_registros = sum(
        len(_query_registros(desde_utc, hasta_utc)) for _ in [1]
    )
    emails_destino = [d.email for d in destinatarios]

    msg           = MIMEMultipart()
    msg['From']   = mail_from
    msg['To']     = ', '.join(emails_destino)
    msg['Subject'] = (f"Reporte de Rondas — {ayer.strftime('%d/%m/%Y')} "
                      f"({len(adjuntos)} archivo(s) adjunto(s))")

    sedes_cubiertas = ', '.join(a[0].replace('Reporte_', '').replace(f'_{ayer.isoformat()}.csv', '') for a in adjuntos)
    cuerpo = (
        f"Se adjunta el reporte de rondas del {ayer.strftime('%d/%m/%Y')}.\n"
        f"Sedes con actividad: {sedes_cubiertas or 'Global'}\n\n"
        f"Este mensaje fue generado automáticamente.\n"
        f"— Sistema de Control de Rondas"
    )
    msg.attach(MIMEText(cuerpo, 'plain', 'utf-8'))

    for nombre_archivo, csv_bytes in adjuntos:
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
        print(f"Reporte enviado a: {', '.join(emails_destino)}")
    except Exception as e:
        print(f"Error SMTP: {e}")


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