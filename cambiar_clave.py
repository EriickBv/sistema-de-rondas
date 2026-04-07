import sys
from run import app
from app import db
from app.models import Administrador 

def resetear_clave():
    if len(sys.argv) != 3:
        print("❌ Error de uso.")
        print("Uso correcto: python cambiar_clave.py <usuario> <nueva_password>")
        return

    usuario_input = sys.argv[1]
    password_input = sys.argv[2]

    print(f"🔄 Buscando administrador: {usuario_input}...")

    admin = Administrador.query.filter_by(usuario=usuario_input).first()

    if not admin:
        print(f"❌ Error: El administrador '{usuario_input}' no existe.")
        return

    # Usamos tu método set_password
    admin.set_password(password_input)
    
    try:
        db.session.commit()
        print(f"✅ ¡Éxito! La contraseña de '{usuario_input}' ha sido actualizada.")
    except Exception as e:
        db.session.rollback()
        print(f"❌ Error al guardar en base de datos: {e}")

if __name__ == '__main__':
    with app.app_context():
        resetear_clave()