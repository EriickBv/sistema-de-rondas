"""Parches concurrencia: unique indexes para anti-race en rondas

Revision ID: 0003_concurr
Revises: 0002_checklist_features
Create Date: 2026-04-23

Aplica:
- C-2: UNIQUE INDEX parcial en rondas_activas para impedir 2 rondas
       abiertas simultáneamente para el mismo guardia.
- C-3: UNIQUE CONSTRAINT (id_ronda, id_punto) en registros_ronda para
       blindar el anti-duplicado a nivel DB.

Limpieza preventiva: si ya existen registros que violarían los nuevos
constraints, esta migración FALLARÁ a propósito para que el operador
los inspeccione antes de continuar (ver bloque ASSERT al inicio).
"""
from alembic import op
import sqlalchemy as sa


revision = '0003_concurr'
down_revision = '0002_checklist_features'  # ajusta al revision_id real
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()

    # ─── PRE-CHECKS ───────────────────────────────────────────────────────
    # Aborta si ya hay datos inconsistentes — mejor fallar la migración
    # que crear el constraint a la fuerza ignorando datos sucios.

    # 1) Guardias con más de una ronda activa
    dup_rondas = bind.execute(sa.text("""
        SELECT id_guardia, COUNT(*) AS n
        FROM rondas_activas
        WHERE fecha_fin IS NULL AND id_guardia IS NOT NULL
        GROUP BY id_guardia
        HAVING COUNT(*) > 1
    """)).fetchall()
    if dup_rondas:
        raise RuntimeError(
            f"Existen {len(dup_rondas)} guardias con MÁS DE UNA ronda activa. "
            f"Cierra las rondas huérfanas antes de aplicar esta migración. "
            f"Detalles: {dup_rondas}"
        )

    # 2) Pares (id_ronda, id_punto) duplicados en registros_ronda
    dup_regs = bind.execute(sa.text("""
        SELECT id_ronda, id_punto, COUNT(*) AS n
        FROM registros_ronda
        WHERE id_ronda IS NOT NULL AND id_punto IS NOT NULL
        GROUP BY id_ronda, id_punto
        HAVING COUNT(*) > 1
    """)).fetchall()
    if dup_regs:
        raise RuntimeError(
            f"Existen {len(dup_regs)} pares (ronda, punto) duplicados en "
            f"registros_ronda. Limpia los duplicados antes de aplicar. "
            f"Detalles (top 5): {dup_regs[:5]}"
        )

    # ─── C-2: UNIQUE INDEX parcial sobre rondas_activas ───────────────────
    # MySQL 8.0+ soporta functional/expression indexes. La expresión
    # CASE devuelve id_guardia solo cuando fecha_fin IS NULL, NULL en
    # otro caso. MySQL trata múltiples NULL como distintos en UNIQUE
    # INDEX, así que solo las filas activas compiten por unicidad.
    op.execute("""
        CREATE UNIQUE INDEX ux_ronda_activa_por_guardia
        ON rondas_activas ((
            CASE WHEN fecha_fin IS NULL THEN id_guardia ELSE NULL END
        ))
    """)

    # ─── C-3: UNIQUE CONSTRAINT (id_ronda, id_punto) ──────────────────────
    op.create_unique_constraint(
        'uq_ronda_punto_unico',
        'registros_ronda',
        ['id_ronda', 'id_punto']
    )


def downgrade():
    op.drop_constraint('uq_ronda_punto_unico', 'registros_ronda', type_='unique')
    op.execute("DROP INDEX ux_ronda_activa_por_guardia ON rondas_activas")
