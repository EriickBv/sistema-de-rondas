"""Rondas checklist, asistencia, panico, destinatarios por sede

Revision ID: 0002_checklist_features
Revises: 49120c7140d6
Create Date: 2026-04-22

Cambios:
  Nuevas tablas:   rondas_activas, registros_asistencia, alertas_panico
  Columnas nuevas: registros_ronda.id_ronda (FK → rondas_activas, SET NULL)
                   destinatarios_reporte.id_sede (FK → sedes, SET NULL)

Todas las nuevas columnas son NULLABLE: migración no destructiva y
100% compatible con datos históricos (registros anteriores quedan con NULL).
"""
from alembic import op
import sqlalchemy as sa


revision      = '0002_checklist_features'
down_revision = '49120c7140d6'
branch_labels = None
depends_on    = None


def upgrade():
    # ── 1. Nueva tabla: rondas_activas ───────────────────────────────────────
    op.create_table(
        'rondas_activas',
        sa.Column('id_ronda',           sa.Integer(),     nullable=False),
        sa.Column('id_guardia',         sa.Integer(),     nullable=True),
        sa.Column('fecha_inicio',       sa.DateTime(),    nullable=False),
        sa.Column('fecha_fin',          sa.DateTime(),    nullable=True),
        sa.Column('estado',             sa.String(30),    nullable=False, server_default='activa'),
        sa.Column('observacion_cierre', sa.Text(),        nullable=True),
        sa.ForeignKeyConstraint(['id_guardia'], ['guardias.id_guardia'],
                                ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id_ronda'),
    )
    op.create_index('ix_ronda_activa_guardia_fin',
                    'rondas_activas', ['id_guardia', 'fecha_fin'], unique=False)

    # ── 2. Nueva columna: registros_ronda.id_ronda ───────────────────────────
    # Se agrega como nullable para no afectar registros históricos.
    with op.batch_alter_table('registros_ronda') as batch_op:
        batch_op.add_column(
            sa.Column('id_ronda', sa.Integer(), nullable=True)
        )
        batch_op.create_foreign_key(
            'fk_registro_ronda_activa',
            'rondas_activas', ['id_ronda'], ['id_ronda'],
            ondelete='SET NULL'
        )
        batch_op.create_index('ix_ronda_id_ronda', ['id_ronda'], unique=False)

    # ── 3. Nueva tabla: registros_asistencia ─────────────────────────────────
    op.create_table(
        'registros_asistencia',
        sa.Column('id',         sa.Integer(),         nullable=False),
        sa.Column('id_guardia', sa.Integer(),         nullable=True),
        sa.Column('tipo',       sa.String(10),        nullable=False),
        sa.Column('fecha_hora', sa.DateTime(),        nullable=False),
        sa.Column('lat',        sa.Numeric(10, 8),    nullable=True),
        sa.Column('long',       sa.Numeric(11, 8),    nullable=True),
        sa.ForeignKeyConstraint(['id_guardia'], ['guardias.id_guardia'],
                                ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_asistencia_guardia_fecha',
                    'registros_asistencia', ['id_guardia', 'fecha_hora'], unique=False)

    # ── 4. Nueva tabla: alertas_panico ───────────────────────────────────────
    op.create_table(
        'alertas_panico',
        sa.Column('id',          sa.Integer(),      nullable=False),
        sa.Column('id_guardia',  sa.Integer(),      nullable=True),
        sa.Column('fecha_hora',  sa.DateTime(),     nullable=False),
        sa.Column('lat',         sa.Numeric(10, 8), nullable=True),
        sa.Column('long',        sa.Numeric(11, 8), nullable=True),
        sa.Column('atendida',    sa.Boolean(),      nullable=False, server_default='0'),
        sa.Column('atendida_en', sa.DateTime(),     nullable=True),
        sa.ForeignKeyConstraint(['id_guardia'], ['guardias.id_guardia'],
                                ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_panico_atendida_fecha',
                    'alertas_panico', ['atendida', 'fecha_hora'], unique=False)

    # ── 5. Nueva columna: destinatarios_reporte.id_sede ──────────────────────
    with op.batch_alter_table('destinatarios_reporte') as batch_op:
        batch_op.add_column(
            sa.Column('id_sede', sa.Integer(), nullable=True)
        )
        batch_op.create_foreign_key(
            'fk_destinatario_sede',
            'sedes', ['id_sede'], ['id_sede'],
            ondelete='SET NULL'
        )


def downgrade():
    # Revertir en orden inverso

    with op.batch_alter_table('destinatarios_reporte') as batch_op:
        batch_op.drop_constraint('fk_destinatario_sede', type_='foreignkey')
        batch_op.drop_column('id_sede')

    op.drop_index('ix_panico_atendida_fecha',      table_name='alertas_panico')
    op.drop_table('alertas_panico')

    op.drop_index('ix_asistencia_guardia_fecha',   table_name='registros_asistencia')
    op.drop_table('registros_asistencia')

    with op.batch_alter_table('registros_ronda') as batch_op:
        batch_op.drop_index('ix_ronda_id_ronda')
        batch_op.drop_constraint('fk_registro_ronda_activa', type_='foreignkey')
        batch_op.drop_column('id_ronda')

    op.drop_index('ix_ronda_activa_guardia_fin',   table_name='rondas_activas')
    op.drop_table('rondas_activas')
