# Sistema de Control de Rondas y Seguridad

Sistema web integral para la gestión, monitoreo y auditoría de rondas de seguridad mediante escaneo de códigos QR. Diseñado para ser desplegado fácilmente en servidores locales (On-Premise) utilizando contenedores Docker.

## Características Principales
* **Gestión de Guardias:** Alta, baja y control de usuarios.
* **Mapeo de Zonas:** Creación de puntos de control.
* **Generación de QR:** Módulo para imprimir etiquetas identificadoras de zonas.
* **Reportes:** Visualización de rondas completadas, incompletas y tiempos de recorrido.
* **App Móvil (Web):** Interfaz adaptativa para el escaneo desde celulares.

---

## Requisitos de Instalación

El sistema está contenerizado para evitar conflictos de dependencias. Solo se requiere:
1.  **Docker** (v20.10+)
2.  **Docker Compose** (v1.29+)
3.  Conexión a red local (LAN) para acceso desde móviles.

> **⚠️ IMPORTANTE: USO DE CÁMARA Y HTTPS**
> Los navegadores móviles bloquean el acceso a la cámara si el sitio no es seguro.
> Para usar el escáner QR a través de internet, es **obligatorio** configurar un Proxy Inverso (Nginx/Apache) con certificado SSL que apunte al puerto `5000` del contenedor.

---

## Despliegue Rápido (Paso a Paso)

Configuración Inicial
Ejecutar **python setup.py** en la terminal Esto configurará las claves de seguridad automáticamente.

### 1. Iniciar el Sistema
Abra una terminal en la carpeta del proyecto y ejecute:

```bash
docker-compose up -d --build
```
*Esto descargará las imágenes, configurará la base de datos MySQL y levantará el servidor web (Gunicorn).*

### 2. Inicializar la Base de Datos (Solo la primera vez)
Una vez que los contenedores estén corriendo, debe crear las tablas y el usuario administrador inicial. Ejecute estos comandos en orden:

```bash
# Aplicar migraciones (Crear tablas)
docker-compose exec web flask db upgrade

# Cargar datos semilla (Usuario Admin por defecto)
docker-compose exec web python semilla.py
```

> **Credenciales por defecto:**
> * **Usuario:** `admin`
> * **Contraseña:** `123456`

---

## ¿Olvidó la contraseña de Admin?

Si necesita resetear la contraseña de algún usuario (incluido el admin) desde el servidor, use el script de utilidad incluido:

1. Abra una terminal en la carpeta del proyecto.
2. Ejecute el siguiente comando reemplazando los valores:

```bash
# Sintaxis: python cambiar_clave.py <USUARIO> <NUEVA_CLAVE>
docker-compose exec web python cambiar_clave.py admin nuevaSuperClave2024
```

---

## Acceso al Sistema

### Desde el Servidor:
Acceda a: `http://localhost:5000`

### Desde la Red Local (Celulares/Guardias):
1.  Identifique la IP del servidor (ej: ejecutando `ipconfig` o `ifconfig`).
2.  Desde el navegador del móvil ingrese a: `http://<IP_DEL_SERVIDOR>:5000`

> **Nota:** Asegúrese de que el puerto **5000** esté abierto en el Firewall de Windows/Linux del servidor.


---

## Impresión de Códigos QR
Para imprimir las etiquetas de las zonas:
1.  Ingrese al sistema como Administrador.
2.  Vaya al Panel de Control.
3.  Haga clic en el botón naranja **"🖨️ Imprimir QRs"**.
4.  Se abrirá una vista de impresión optimizada. Presione `Ctrl + P` o use el botón de imprimir.

---

## Comandos Útiles

**Ver logs del sistema (para depuración):**
```bash
docker-compose logs -f web
```

**Detener el sistema:**
```bash
docker-compose down
```

**Entrar a la consola del contenedor:**
```bash
docker-compose exec web /bin/bash
```

---

## Estructura del Proyecto
* `/app`: Código fuente de la aplicación Flask.
* `/migrations`: Scripts de migración de base de datos.
* `Dockerfile`: Configuración de la imagen del servidor web.
* `docker-compose.yml`: Orquestación de servicios (Web + MySQL).
* `requirements.txt`: Lista de librerías Python.