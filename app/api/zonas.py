from flask import Blueprint, request, jsonify
import uuid
from app.extensions import db
from app.models import PuntoControl
from app.utils import token_required

zonas_bp = Blueprint('zonas', __name__)

# --- Función Auxiliar ---
def reordenar_zonas():
    zonas = PuntoControl.query.order_by(PuntoControl.id_punto).all()
    for index, zona in enumerate(zonas):
        zona.numero_orden = index + 1
    db.session.commit()

@zonas_bp.route('/', methods=['POST'])
@token_required
def agregar_zona():
    if request.usuario_rol != 'admin':
        return jsonify({'message': 'No autorizado'}), 403

    data   = request.get_json()
    nombre = data.get('nombre_zona')

    if not nombre:
        return jsonify({'message': 'El nombre es obligatorio'}), 400

    try:
        lat      = float(data.get('latitud', 0.0))
        long     = float(data.get('longitud', 0.0))
        id_sede  = data.get('id_sede')                             
        token_qr = str(uuid.uuid4().hex)[:12].upper()

        nueva_zona = PuntoControl(
            nombre_zona=nombre,
            token_qr=token_qr,
            latitud=lat,
            longitud=long,
            radio_permitido=20,
            numero_orden=999,
            id_sede=int(id_sede) if id_sede else None              
        )
        db.session.add(nueva_zona)
        db.session.commit()
        reordenar_zonas()

        return jsonify({'message': f'Zona "{nombre}" agregada correctamente'}), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({'message': str(e)}), 500

@zonas_bp.route('/<int:id_zona>', methods=['DELETE'])
@token_required
def eliminar_zona(id_zona):
    if request.usuario_rol != 'admin':
        return jsonify({'message': 'No autorizado'}), 403

    try:
        zona = PuntoControl.query.get_or_404(id_zona)
        nombre = zona.nombre_zona
        db.session.delete(zona)
        db.session.commit()
        reordenar_zonas()
        
        return jsonify({'message': f'Zona "{nombre}" eliminada'}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'message': str(e)}), 500