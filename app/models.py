# app/models.py
from .extensions import db
from datetime import datetime, timezone
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

class RondaActiva(db.Model):
    __tablename__ = 'rondas_activas'
    __table_args__ = (
        db.Index('ix_ronda_activa_guardia_fin', 'id_guardia', 'fecha_fin'),
    )

    id_ronda           = db.Column(db.Integer, primary_key=True)
    id_guardia         = db.Column(db.Integer,
                                   db.ForeignKey('guardias.id_guardia', ondelete='SET NULL'),
                                   nullable=True)
    fecha_inicio       = db.Column(db.DateTime, nullable=False)
    fecha_fin          = db.Column(db.DateTime, nullable=True)   # NULL = ronda en curso
    estado             = db.Column(db.String(30), default='activa', nullable=False)
    observacion_cierre = db.Column(db.Text, nullable=True)       # justificación si hay pendientes

    guardia   = db.relationship('Guardia',       backref='rondas_activas')
    registros = db.relationship('RegistroRonda', backref='ronda', lazy='dynamic')


class RegistroRonda(db.Model):
    __tablename__ = 'registros_ronda'
    __table_args__ = (
        db.Index('ix_ronda_guardia_fecha', 'id_guardia', 'fecha_hora'),
        db.Index('ix_ronda_fecha_hora',    'fecha_hora'),
        db.Index('ix_ronda_id_punto',      'id_punto'),
        db.Index('ix_ronda_id_ronda',      'id_ronda'),
        # PARCHE C-3: anti-duplicado a nivel DB. Un punto solo puede aparecer
        # UNA vez en una ronda dada (sea escaneo, salto u OMITIDO AL CIERRE).
        # Esto blinda contra dobles taps, retries de red y races de finalizar.
        db.UniqueConstraint('id_ronda', 'id_punto', name='uq_ronda_punto_unico'),
    )

    id_registro     = db.Column(db.BigInteger, primary_key=True)
    id_guardia      = db.Column(db.Integer,
                                db.ForeignKey('guardias.id_guardia', ondelete='SET NULL'),
                                nullable=True)
    id_punto        = db.Column(db.Integer,
                                db.ForeignKey('puntos_control.id_punto', ondelete='SET NULL'),
                                nullable=True)
    id_ronda        = db.Column(db.Integer,
                                db.ForeignKey('rondas_activas.id_ronda', ondelete='SET NULL'),
                                nullable=True)
    fecha_hora      = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    lat_real        = db.Column(db.Numeric(10, 8))
    long_real       = db.Column(db.Numeric(11, 8))
    distancia_error = db.Column(db.Numeric(10, 2))
    observacion     = db.Column(db.Text)

    guardia = db.relationship('Guardia',      backref='registros')
    punto   = db.relationship('PuntoControl', backref='registros')


class RegistroAsistencia(db.Model):
    __tablename__ = 'registros_asistencia'
    __table_args__ = (
        db.Index('ix_asistencia_guardia_fecha', 'id_guardia', 'fecha_hora'),
    )

    id         = db.Column(db.Integer, primary_key=True)
    id_guardia = db.Column(db.Integer,
                           db.ForeignKey('guardias.id_guardia', ondelete='SET NULL'),
                           nullable=True)
    tipo       = db.Column(db.String(10), nullable=False)   # 'entrada' | 'salida'
    fecha_hora = db.Column(db.DateTime, nullable=False)
    lat        = db.Column(db.Numeric(10, 8), nullable=True)
    long       = db.Column(db.Numeric(11, 8), nullable=True)

    guardia = db.relationship('Guardia', backref='asistencias')

class AlertaPanico(db.Model):
    __tablename__ = 'alertas_panico'
    __table_args__ = (
        # Optimiza el polling del admin: WHERE atendida = FALSE ORDER BY fecha_hora DESC
        db.Index('ix_panico_atendida_fecha', 'atendida', 'fecha_hora'),
    )

    id          = db.Column(db.Integer, primary_key=True)
    id_guardia  = db.Column(db.Integer,
                            db.ForeignKey('guardias.id_guardia', ondelete='SET NULL'),
                            nullable=True)
    fecha_hora  = db.Column(db.DateTime, nullable=False)
    lat         = db.Column(db.Numeric(10, 8), nullable=True)
    long        = db.Column(db.Numeric(11, 8), nullable=True)
    atendida    = db.Column(db.Boolean, default=False, nullable=False)
    atendida_en = db.Column(db.DateTime, nullable=True)

    guardia = db.relationship('Guardia', backref='alertas_panico')


class DestinatarioReporte(db.Model):
    __tablename__ = 'destinatarios_reporte'
    id        = db.Column(db.Integer, primary_key=True)
    email     = db.Column(db.String(150), unique=True, nullable=False)
    activo    = db.Column(db.Boolean, default=True)
    creado_en = db.Column(db.DateTime, default=datetime.now)
    # NUEVA COLUMNA: NULL = destinatario global (recibe todos los reportes).
    # Si tiene id_sede, solo recibe el CSV de esa instalación.
    id_sede   = db.Column(db.Integer,
                          db.ForeignKey('sedes.id_sede', ondelete='SET NULL'),
                          nullable=True)
    sede = db.relationship('Sede', backref='destinatarios_reporte')