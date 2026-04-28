# app/validators.py
from __future__ import annotations
import re


# ─── RUT CHILENO ─────────────────────────────────────────────────────────────

_RUT_LIMPIO_RE = re.compile(r'[^0-9kK]')


def validar_rut(rut: str) -> str | None:
    if not rut or not isinstance(rut, str):
        return None
    limpio = _RUT_LIMPIO_RE.sub('', rut).upper()
    if len(limpio) < 8 or len(limpio) > 9:
        return None
    cuerpo, dv_dado = limpio[:-1], limpio[-1]
    if not cuerpo.isdigit():
        return None

    # Cálculo del dígito verificador (módulo 11 con multiplicadores 2..7)
    suma = 0
    mult = 2
    for c in reversed(cuerpo):
        suma += int(c) * mult
        mult = 2 if mult == 7 else mult + 1
    resto = 11 - (suma % 11)
    dv_esperado = {10: 'K', 11: '0'}.get(resto, str(resto))
    if dv_dado != dv_esperado:
        return None

    # Formato canónico con puntos
    cuerpo_fmt = ''
    for i, c in enumerate(reversed(cuerpo)):
        if i and i % 3 == 0:
            cuerpo_fmt = '.' + cuerpo_fmt
        cuerpo_fmt = c + cuerpo_fmt
    return f"{cuerpo_fmt}-{dv_dado}"


# ─── EMAIL ───────────────────────────────────────────────────────────────────

# Regex pragmático (no RFC-completo, pero atrapa el 99% de los typos reales).
# Si necesitas validación RFC estricta, considera la librería `email-validator`.
_EMAIL_RE = re.compile(
    r'^[a-z0-9._%+\-]+@[a-z0-9][a-z0-9.\-]*\.[a-z]{2,}$'
)


def validar_email(email: str) -> str | None:
    """
    Valida un email de forma pragmática y lo retorna en lowercase + trim.
    Retorna None si es inválido.
    """
    if not email or not isinstance(email, str):
        return None
    e = email.strip().lower()
    if len(e) > 254:   # límite RFC 5321
        return None
    if not _EMAIL_RE.match(e):
        return None
    return e


# ─── ID DE SEDE / FK ─────────────────────────────────────────────────────────

def parse_id_sede(value) -> int | None:
    """
    Convierte un valor recibido por API (string desde query/form, o int desde
    JSON) a un id_sede entero positivo, o None si:
      - No fue provisto (None, "", "null", "undefined")
      - Es 0 o negativo (FK inválida; PRIMARY KEY de sedes empieza en 1)
      - No es numérico

    Esto resuelve el bug `int(id_sede) if id_sede else None` que trataba "0"
    como truthy y dejaba pasar `id_sede=0` rompiendo la FK.
    """
    if value is None:
        return None
    s = str(value).strip().lower()
    if s in ('', 'null', 'undefined', 'none', '0'):
        return None
    try:
        n = int(s)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


# ─── PASSWORD ────────────────────────────────────────────────────────────────

def validar_password(pw: str, min_len: int = 8) -> bool:
    """
    Política mínima de password: longitud >= min_len y no completamente en
    blanco. Mantenida deliberadamente simple — endurecer aquí (mayúsculas,
    números, símbolos) si la organización lo requiere.
    """
    if not pw or not isinstance(pw, str):
        return False
    if len(pw) < min_len:
        return False
    if pw.strip() == '':
        return False
    return True


# ─── NOMBRE GENÉRICO ─────────────────────────────────────────────────────────

def validar_nombre(nombre: str, max_len: int = 100) -> str | None:
    """
    Sanea un nombre (de guardia, sede, etc): trim, rechaza vacíos y enforce
    de longitud máxima coherente con los VARCHAR del modelo.
    """
    if not nombre or not isinstance(nombre, str):
        return None
    n = nombre.strip()
    if not n or len(n) > max_len:
        return None
    return n
