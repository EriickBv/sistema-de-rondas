# app/api/reportes.py
import csv
import io
import pytz
import datetime
import qrcode
import base64
from io import BytesIO
from flask import Blueprint, jsonify, request, Response, stream_with_context, render_template
from app.models import Guardia, RegistroRonda, PuntoControl
from app.utils import token_required

reportes_bp = Blueprint('reportes', __name__)

@reportes_bp.route('/', methods=['GET'])
@token_required
def obtener_reportes():
    tipo = request.args.get('tipo')
    
    # 1. REPORTE DE HOY (Con lógica de seguridad anti-errores)
    if tipo == 'hoy':
        hoy = datetime.date.today()
        registros = RegistroRonda.query.filter(
            RegistroRonda.fecha_hora >= datetime.datetime.combine(hoy, datetime.time.min),
            RegistroRonda.fecha_hora <= datetime.datetime.combine(hoy, datetime.time.max)
            ).order_by(RegistroRonda.fecha_hora.asc()).all()
        
        data = []
        ultima_hora_guardia = {}
        TIEMPO_MAXIMO = 15 # minutos para llegar entre punto y punto

        for r in registros:
            # --- CORRECCIÓN DEL ERROR ---
            # Verificamos si la zona aún existe en la base de datos
            if r.punto:
                nombre_zona = r.punto.nombre_zona
                numero_orden = r.punto.numero_orden
                radio_permitido = r.punto.radio_permitido if r.punto.radio_permitido else 20
            else:
                # Si la zona fue borrada, usamos valores por defecto para no romper el reporte
                nombre_zona = "🚫 Zona Eliminada"
                numero_orden = -1 # Valor imposible para no afectar lógica
                radio_permitido = 20

            # Lógica de Tiempo
            llegada_tarde = False
            minutos_pasados = 0

            # Solo consideramos inicio de ronda si la zona existe y es la #1
            es_inicio_ronda = (numero_orden == 1)

            if r.id_guardia in ultima_hora_guardia and not es_inicio_ronda:
                delta = r.fecha_hora - ultima_hora_guardia[r.id_guardia]
                minutos_pasados = delta.total_seconds() / 60
                
                if minutos_pasados > TIEMPO_MAXIMO:
                    llegada_tarde = True

            ultima_hora_guardia[r.id_guardia] = r.fecha_hora

            esta_lejos = r.distancia_error > 20

            # Matriz de Estados
            estado_texto = ""
            
            if not llegada_tarde and not esta_lejos:
                estado_texto = "✅ Correcto"
            
            elif llegada_tarde and not esta_lejos:
                estado_texto = f"⏰ Tarde (+{int(minutos_pasados - TIEMPO_MAXIMO)}m)"
            
            elif not llegada_tarde and esta_lejos:
                estado_texto = f"⚠️ Lejos ({int(r.distancia_error)}m)"
            
            else: 
                estado_texto = f"🚨 CRÍTICO (Tarde y Lejos)"

            if es_inicio_ronda:
                 estado_texto = "🏁 Inicio Ronda " + ("(Lejos)" if esta_lejos else "(Ok)")

            data.append({
                "Guardia": r.guardia.nombre if r.guardia else "Guardia Eliminado", # Protección extra
                "Punto": nombre_zona,
                "Hora": r.fecha_hora.isoformat() + "Z",
                "Distancia": f"{int(r.distancia_error)}m" if r.distancia_error > 0 else "0m",
                "Estado": estado_texto, 
                "Observacion": r.observacion
            })
        
        data.reverse()
        return jsonify(data), 200

    # 2. LISTA DE GUARDIAS
    elif tipo == 'guardias':
        guardias = Guardia.query.all()
        data = [{
            "ID": g.id_guardia,
            "Nombre": g.nombre,
            "RUT": g.rut,
            "Email": g.email,
            "Activo": g.activo
        } for g in guardias]
        return jsonify(data), 200

    # 3. LISTA DE ZONAS / PUNTOS
    elif tipo == 'puntos':
        puntos = PuntoControl.query.order_by(PuntoControl.numero_orden).all()
        data = [{
            "ID": p.id_punto,
            "Nombre": p.nombre_zona,
            "Token": p.token_qr,
            "Latitud": float(p.latitud),
            "Longitud": float(p.longitud),
            "Orden": p.numero_orden
        } for p in puntos]
        return jsonify(data), 200

    return jsonify({"message": "Tipo de reporte no válido"}), 400

@reportes_bp.route('/exportar', methods=['GET'])
@token_required
def exportar_csv():
    if request.usuario_rol != 'admin':
        return jsonify({"message": "No permitido"}), 403
    
    def generar():
        data = io.StringIO()
        w = csv.writer(data, delimiter=';')
        yield '\ufeff'
        w.writerow(('Fecha y Hora', 'Guardia', 'RUT', 'Punto de Control', 'Observacion', 'Distancia Error (m)'))
        yield data.getvalue()
        data.seek(0)
        data.truncate(0)
        zona_chile=pytz.timezone('America/Santiago')
        registros = RegistroRonda.query.order_by(RegistroRonda.fecha_hora.desc()).all()
        for r in registros:
            fecha_utc=r.fecha_hora.replace(tzinfo=pytz.utc)
            fecha_local=fecha_utc.astimezone(zona_chile)

            nombre_zona = r.punto.nombre_zona if r.punto else "Zona Eliminada"
            nombre_guardia = r.guardia.nombre if r.guardia else "Guardia Eliminado"
            rut_guardia = r.guardia.rut if r.guardia else "N/A"

            w.writerow((
                fecha_local.strftime('%Y-%m-%d %H:%M:%S'), 
                nombre_guardia, 
                rut_guardia,
                nombre_zona, 
                r.observacion, 
                f"{r.distancia_error:.2f}"
            ))
            yield data.getvalue()
            data.seek(0)
            data.truncate(0)
    response = Response(stream_with_context(generar()), mimetype='text/csv')
    response.headers.set('Content-Disposition', 'attachment', filename='reporte_rondas.csv')
    return response

@reportes_bp.route('/imprimir_qrs', methods=['GET'])
@token_required
def imprimir_qrs():
    if request.usuario_rol != 'admin':
        return jsonify({"message": "No permitido"}), 403

    puntos = PuntoControl.query.all()
    lista_qrs = []

    for p in puntos:
        qr = qrcode.QRCode(version=1, box_size=10, border=4)
        qr.add_data(p.token_qr) 
        qr.make(fit=True)
        img = qr.make_image(fill='black', back_color='white')
        buffer = BytesIO()
        img.save(buffer, format="PNG")
        img_str = base64.b64encode(buffer.getvalue()).decode()

        lista_qrs.append({
            "nombre": p.nombre_zona,
            "token": p.token_qr,
            "img": img_str
        })
    return render_template('print_qrs.html', qrs=lista_qrs)