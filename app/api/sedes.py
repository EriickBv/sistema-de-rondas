# app/api/sedes.py
from flask import Blueprint, request, jsonify, current_app
from sqlalchemy import func
from app.extensions import db
from app.models import Sede, Guardia, PuntoControl
from app.utils import token_required

sedes_bp = Blueprint('sedes', __name__)


@sedes_bp.route('/', methods=['GET'])
@token_required
def listar_sedes():
    """
    PARCHE M-2: antes generaba 1 + 2N queries (lazy-load de .guardias y
    .puntos por cada sede). Con 50 sedes = 101 queries. Ahora se resuelve
    en UNA sola query con LEFT JOIN + GROUP BY usando subqueries para
    evitar la duplicación cartesiana entre guardias y puntos.
    """
    # Subqueries de conteo independiente para no multiplicar filas
    sub_g = (db.session.query(
                Guardia.id_sede.label('sid'),
                func.count(Guardia.id_guardia).label('ng'))
             .group_by(Guardia.id_sede).subquery())
    sub_p = (db.session.query(
                PuntoControl.id_sede.label('sid'),
                func.count(PuntoControl.id_punto).label('np'))
             .group_by(PuntoControl.id_sede).subquery())

    rows = (db.session.query(
                Sede.id_sede, Sede.nombre,
                func.coalesce(sub_g.c.ng, 0),
                func.coalesce(sub_p.c.np, 0))
            .outerjoin(sub_g, sub_g.c.sid == Sede.id_sede)
            .outerjoin(sub_p, sub_p.c.sid == Sede.id_sede)
            .order_by(Sede.nombre)
            .all())

    return jsonify([{
        "id":           id_sede,
        "nombre":       nombre,
        "num_guardias": ng,
        "num_zonas":    np,
    } for id_sede, nombre, ng, np in rows]), 200


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
    # PARCHE SRE-3: try/except con rollback obligatorio
    try:
        db.session.add(sede)
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Error al crear sede '%s'", nombre)
        return jsonify({"message": "Error interno al crear la sede"}), 500
    return jsonify({"message": f"Sede '{nombre}' creada", "id": sede.id_sede}), 201


@sedes_bp.route('/<int:id_sede>', methods=['DELETE'])
@token_required
def eliminar_sede(id_sede):
    if request.usuario_rol != 'admin':
        return jsonify({"message": "No autorizado"}), 403
    sede = Sede.query.get_or_404(id_sede)
    nombre = sede.nombre
    # PARCHE SRE-3: las tres operaciones (UPDATE guardias, UPDATE puntos,
    # DELETE sede) deben ser atómicas. Si algo falla a mitad de camino sin
    # rollback, queda la sede borrada con guardias huérfanos apuntando a
    # un id_sede que ya no existe (o viceversa). El try/except envuelve TODO.
    try:
        # Desasociar entidades relacionadas antes de eliminar
        Guardia.query.filter_by(id_sede=id_sede).update({'id_sede': None})
        PuntoControl.query.filter_by(id_sede=id_sede).update({'id_sede': None})
        db.session.delete(sede)
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Error al eliminar sede %s", id_sede)
        return jsonify({"message": "Error interno al eliminar la sede"}), 500
    return jsonify({"message": f"Sede '{nombre}' eliminada. Guardias y zonas desasociados."}), 200