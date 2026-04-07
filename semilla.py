from run import app
from app import db
from app.models import Administrador 

def crear_datos_iniciales():
    print("🌱 Iniciando semillado de base de datos...")
    admin_existente = Administrador.query.filter_by(usuario='admin').first()

    if not admin_existente:
        nuevo_admin = Administrador(usuario="admin")
        nuevo_admin.set_password("123456")

        db.session.add(nuevo_admin)
        db.session.commit()
        print("✅ Usuario 'admin' creado con éxito.")
        print("🔑 Credenciales: admin / 123456")
    else:
        print("ℹ️ El usuario 'admin' ya existe. No se hicieron cambios.")

if __name__ == '__main__':
    with app.app_context():
        try:
            crear_datos_iniciales()
        except Exception as e:
            print(f"❌ Error al sembrar datos: {e}")