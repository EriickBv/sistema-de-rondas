import os
from flask import Blueprint, render_template
from .models import PuntoControl 

web_bp = Blueprint('web', __name__)

@web_bp.route('/')
def index():
    return render_template('guardia.html')


ruta_admin = os.getenv('RUTA_ADMIN', '/admin')
@web_bp.route(ruta_admin)
def admin():
    lista_zonas = PuntoControl.query.all()
    return render_template('admin.html', zonas=lista_zonas)