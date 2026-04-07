import math
import jwt
from functools import wraps
from flask import request, jsonify, current_app
# Importamos los modelos para consultar su estado real
from .models import Guardia, Administrador

def calcular_distancia(lat1, lon1, lat2, lon2):
    R = 6371000 
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return round(R * c, 2)

def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        if 'Authorization' in request.headers:
            token = request.headers['Authorization']
            if token.startswith("Bearer "):
                token = token.split(" ")[1]
        
        if not token:
            return jsonify({'message': 'Falta el Token de seguridad'}), 401
        
        try:
            data = jwt.decode(token, current_app.config['SECRET_KEY'], algorithms=["HS256"])
            if data.get('rol') == 'guardia':
                guardia_db = Guardia.query.get(data['id'])
                if not guardia_db or not guardia_db.activo:
                    return jsonify({'message': 'Tu cuenta ha sido desactivada. Contacta al admin.'}), 401
            request.usuario_id = data['id']
            request.usuario_rol = data.get('rol')
        except jwt.ExpiredSignatureError:
            return jsonify({'message': 'El token ha expirado, inicia sesión de nuevo'}), 401
        except Exception as e:
            return jsonify({'message': 'Token inválido'}), 401
        
        return f(*args, **kwargs)
    return decorated