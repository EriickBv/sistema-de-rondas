# app/api/sedes.py
from flask import Blueprint, request, jsonify
from app.extensions import db
from app.models import Sede, Guardia, PuntoControl
from app.utils import token_required

sedes_bp = Blueprint('sedes', __name__)


@sedes_bp.route('/', methods=['GET'])
@token_required
def listar_sedes():
    sedes = Sede.query.order_by(Sede.nombre).all()
    return jsonify([{
        "id":           s.id_sede,
        "nombre":       s.nombre,
        "num_guardias": len(s.guardias),
        "num_zonas":    len(s.puntos)
    } for s in sedes]), 200


@sedes_bp.route('/', methods=['POST'])
@token_required
def crear_sede():
    if request.usuario_rol != 'admin':
        return jsonify({"message": "No autorizado"}), 403
    nombre = (request.get_json() or {}).get('nombre', '').strip()
    if not nombre:
        return jsonify({"message": "El nombre es obligatorio"}), 400
    if Sede.query.filter_by(nombre=nombre).first():
        return jsonify({"message": "Ya existe una sede con ese nombre"}), 400
    sede = Sede(nombre=nombre)
    db.session.add(sede)
    db.session.commit()
    return jsonify({"message": f"Sede '{nombre}' creada", "id": sede.id_sede}), 201


@sedes_bp.route('/<int:id_sede>', methods=['DELETE'])
@token_required
def eliminar_sede(id_sede):
    if request.usuario_rol != 'admin':
        return jsonify({"message": "No autorizado"}), 403
    sede = Sede.query.get_or_404(id_sede)
    nombre = sede.nombre
    # Desasociar entidades relacionadas antes de eliminar
    Guardia.query.filter_by(id_sede=id_sede).update({'id_sede': None})
    PuntoControl.query.filter_by(id_sede=id_sede).update({'id_sede': None})
    db.session.delete(sede)
    db.session.commit()
    return jsonify({"message": f"Sede '{nombre}' eliminada. Guardias y zonas desasociados."}), 200