# app/models.py
from .extensions import db
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash


class Sede(db.Model):
    __tablename__ = 'sedes'
    id_sede   = db.Column(db.Integer, primary_key=True)
    nombre    = db.Column(db.String(100), unique=True, nullable=False)
    creado_en = db.Column(db.DateTime, default=datetime.now)

    guardias = db.relationship('Guardia',      backref='sede', lazy=True)
    puntos   = db.relationship('PuntoControl', backref='sede', lazy=True)


class Administrador(db.Model):
    __tablename__ = 'administradores'
    id_admin      = db.Column(db.Integer, primary_key=True)
    usuario       = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Guardia(db.Model):
    __tablename__ = 'guardias'
    id_guardia    = db.Column(db.Integer, primary_key=True)
    nombre        = db.Column(db.String(100))
    email         = db.Column(db.String(100), unique=True)
    rut           = db.Column(db.String(20), unique=True)
    password_hash = db.Column(db.String(255))
    activo        = db.Column(db.Boolean, default=True)
    id_sede       = db.Column(db.Integer, db.ForeignKey('sedes.id_sede'), nullable=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class PuntoControl(db.Model):
    __tablename__   = 'puntos_control'
    id_punto        = db.Column(db.Integer, primary_key=True)
    nombre_zona     = db.Column(db.String(100))
    token_qr        = db.Column(db.String(100), unique=True)
    latitud         = db.Column(db.Numeric(10, 8))
    longitud        = db.Column(db.Numeric(11, 8))
    radio_permitido = db.Column(db.Integer, default=20)
    numero_orden    = db.Column(db.Integer)
    id_sede         = db.Column(db.Integer, db.ForeignKey('sedes.id_sede'), nullable=True)


class RegistroRonda(db.Model):
    __tablename__   = 'registros_ronda'
    id_registro     = db.Column(db.BigInteger, primary_key=True)
    id_guardia      = db.Column(db.Integer, db.ForeignKey('guardias.id_guardia'))
    id_punto        = db.Column(db.Integer, db.ForeignKey('puntos_control.id_punto'))
    fecha_hora      = db.Column(db.DateTime, default=datetime.now)
    lat_real        = db.Column(db.Numeric(10, 8))
    long_real       = db.Column(db.Numeric(11, 8))
    distancia_error = db.Column(db.Numeric(10, 2))
    observacion     = db.Column(db.Text)

    guardia = db.relationship('Guardia',      backref='registros')
    punto   = db.relationship('PuntoControl', backref='registros')


class DestinatarioReporte(db.Model):
    __tablename__ = 'destinatarios_reporte'
    id        = db.Column(db.Integer, primary_key=True)
    email     = db.Column(db.String(150), unique=True, nullable=False)
    activo    = db.Column(db.Boolean, default=True)
    creado_en = db.Column(db.DateTime, default=datetime.now)