# app/api/auth.py
import datetime
from datetime import timezone
import jwt
from flask import Blueprint, request, jsonify, current_app
from app.extensions import db
from app.models import Guardia, Administrador
from app.utils import token_required

auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/login_guardia', methods=['POST'])
def login_guardia():
    data = request.get_json()
    if not data or not data.get('rut') or not data.get('password'):
        return jsonify({"message": "Faltan datos"}), 400

    guardia = Guardia.query.filter_by(rut=data['rut']).first()

    if guardia and guardia.activo and guardia.check_password(data['password']):
        token = jwt.encode({
            'id': guardia.id_guardia,
            'rol': 'guardia',
            'exp': datetime.datetime.now(timezone.utc) + datetime.timedelta(hours=12)
        }, current_app.config['SECRET_KEY'], algorithm="HS256")

        return jsonify({'token': token, 'nombre': guardia.nombre}), 200

    return jsonify({"message": "Credenciales inválidas"}), 401

@auth_bp.route('/crear_guardia', methods=['POST'])
@token_required  # <--- AHORA PROTEGIDO
def crear_guardia():
    if request.usuario_rol != 'admin': # <--- SOLO ADMIN
        return jsonify({"message": "No autorizado"}), 403

    data = request.get_json()
    
    if Guardia.query.filter_by(rut=data['rut']).first():
        return jsonify({"message": "El RUT ya existe"}), 400

    nuevo_guardia = Guardia(
        nombre=data['nombre'],
        rut=data['rut'],
        email=data['email']
    )
    nuevo_guardia.set_password(data['password'])

    try:
        db.session.add(nuevo_guardia)
        db.session.commit()
        return jsonify({"message": "Guardia creado exitosamente"}), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({"message": str(e)}), 500

@auth_bp.route('/login_admin', methods=['POST'])
def login_admin():
    data = request.get_json()
    admin = Administrador.query.filter_by(usuario=data['usuario']).first()

    if admin and admin.check_password(data['password']):
        token = jwt.encode({
            'id': admin.id_admin,
            'rol': 'admin',
            'exp': datetime.datetime.now(timezone.utc) + datetime.timedelta(hours=4)
        }, current_app.config['SECRET_KEY'], algorithm="HS256")
        return jsonify({'token': token}), 200

    return jsonify({"message": "Admin no encontrado"}), 401

@auth_bp.route('/cambiar_estado_guardia/<int:id>', methods=['PUT'])
@token_required
def cambiar_estado_guardia(id):
    if request.usuario_rol != 'admin':
        return jsonify({"message": "No tienes permiso"}), 403
    guardia = Guardia.query.get(id)
    if not guardia:
        return jsonify({"message": "Guardia no encontrado"}), 404
    guardia.activo = not guardia.activo 
    db.session.commit()
    return jsonify({"message": f"Guardia {'activado' if guardia.activo else 'desactivado'}"}), 200

@auth_bp.route('/validar_token', methods=['GET'])
@token_required
def validar_token():
    return jsonify({"status": "ok"}), 200