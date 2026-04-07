import secrets
import os

def generar_env():
    if os.path.exists(".env"):
        print("⚠️  El archivo .env ya existe. No se tocará.")
        return

    print("⚙️  Generando configuración de seguridad única...")
    
    # Generamos claves criptográficamente seguras
    db_pass = secrets.token_urlsafe(32)
    secret_key = secrets.token_urlsafe(50)
    
    contenido = f"""# Configuración Generada Automáticamente
# NO COMPARTIR ESTE ARCHIVO

# MySQL
MYSQL_ROOT_PASSWORD={db_pass}
MYSQL_DATABASE=sistema_rondas
MYSQL_USER=admin
MYSQL_PASSWORD={db_pass}

# Flask
FLASK_APP=run.py
FLASK_ENV=production
SECRET_KEY="{secret_key}"
DATABASE_URL=mysql+mysqlconnector://admin:{db_pass}@db/sistema_rondas
"""

    with open(".env", "w") as f:
        f.write(contenido)
    
    print("✅ Archivo .env creado con claves seguras.")
    print("🚀 Ahora puedes ejecutar: docker-compose up -d --build")

if __name__ == "__main__":
    generar_env()