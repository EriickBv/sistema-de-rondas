import sys
import re
from run import app
from app import db
from app.models import Administrador, Guardia

def formatear_rut(rut_sucio):
    """Limpia y da formato 'XX.XXX.XXX-X' al RUT."""
    limpio = re.sub(r'[^0-9kK]', '', rut_sucio)
    if len(limpio) < 2: return limpio
    cuerpo, dv = limpio[:-1], limpio[-1].upper()
    cuerpo_puntos = ""
    for i, char in enumerate(reversed(cuerpo)):
        if i > 0 and i % 3 == 0: cuerpo_puntos = "." + cuerpo_puntos
        cuerpo_puntos = char + cuerpo_puntos
    return f"{cuerpo_puntos}-{dv}"

def ejecutar():
    if len(sys.argv) < 3:
        print("❌ Uso: python gestionar_usuarios.py <tipo: admin|guardia> <identificador> <password> [nuevo_nombre_admin]")
        return

    tipo_solicitado = sys.argv[1].lower()
    identificador = sys.argv[2]
    password = sys.argv[3]

    with app.app_context():
        # --- CASO ADMINISTRADOR ---
        if tipo_solicitado == 'admin':
            user = Administrador.query.filter_by(usuario=identificador).first()
            if user:
                # Si existe, actualizamos
                if len(sys.argv) == 5: user.usuario = sys.argv[4]
                user.set_password(password)
                accion = "actualizado"
            else:
                # Si no existe, creamos uno nuevo
                nuevo_admin = Administrador(usuario=identificador)
                nuevo_admin.set_password(password)
                db.session.add(nuevo_admin)
                accion = "creado"
            
        # --- CASO GUARDIA ---
        elif tipo_solicitado == 'guardia':
            rut = formatear_rut(identificador)
            user = Guardia.query.filter_by(rut=rut).first()
            if not user:
                print(f"❌ Error: El guardia con RUT {rut} no existe. Créalo primero en el panel web.")
                return
            user.set_password(password)
            accion = "actualizado (password)"
        
        try:
            db.session.commit()
            print(f"✅ Éxito: {tipo_solicitado} '{identificador}' {accion}.")
        except Exception as e:
            db.session.rollback()
            print(f"❌ Error: {e}")

if __name__ == '__main__':
    ejecutar()