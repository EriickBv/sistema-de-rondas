# Sistema de Control de Rondas y Seguridad (Pro)

Sistema web integral corporativo para la gestión, monitoreo y auditoría de rondas de seguridad mediante geolocalización y escaneo de códigos QR. Diseñado con una arquitectura multi-sucursal y empaquetado en contenedores Docker para garantizar alta disponibilidad y facilidad de mantenimiento.

##  Stack Tecnológico
* **Backend:** Python 3.9, Flask, SQLAlchemy (ORM).
* **Base de Datos:** MySQL 8.0.
* **Servidor Web:** Gunicorn (WSGI) preparado para producción.
* **Frontend:** HTML5, CSS3, JavaScript.
* **Seguridad:** Autenticación por JWT (JSON Web Tokens) y contraseñas con Hash (Werkzeug).

##  Características Principales
* **Arquitectura Multi-Instalación:** Aislamiento lógico de guardias y zonas por sedes (sucursales).
* **Gestión de Guardias:** Alta, baja y control de usuarios activos.
* **Mapeo de Zonas:** Creación de puntos de control con validación de coordenadas GPS y radio de tolerancia.
* **Generación de QR:** Módulo nativo para visualizar e imprimir etiquetas identificadoras por sede.
* **Reportes Avanzados y Auditoría:**
    * **Visualización:** Rondas del día con estados dinámicos (Correcto, Tarde, Lejos o Crítico).
    * **Exportación:** Descarga de reportes en CSV por instalación y rango de fechas.
    * **Automatización:** Envío diario automático del reporte (vía SMTP) de la jornada anterior a las 07:00 AM.
* **App Móvil de Terreno:** Interfaz adaptativa, de bajo consumo de datos y diseñada para escaneo rápido.

---

##  Requisitos de Instalación
* **Docker** (v20.10+).
* **Docker Compose** (v1.29+).
* **Configuración SMTP:** Cuenta de correo emisor configurada (ej: Gmail con "Contraseña de Aplicación" o SMTP Corporativo).

> ** IMPORTANTE: USO DE CÁMARA Y HTTPS**
> Las políticas de seguridad de navegadores móviles (iOS/Android) bloquean el acceso a la cámara si el sitio no es seguro. Para usar el escáner QR en terreno, es **obligatorio** que el servidor de producción cuente con un Proxy Inverso (Nginx/Apache) y un certificado SSL (HTTPS).

---

##  Despliegue Paso a Paso

### 1. Configuración de Entorno
Ejecute el script en su terminal para establecer claves y credenciales:
* `python setup.py`
* *Nota: En el .env, ingrese la contraseña de aplicación de 16 caracteres sin espacios  https://myaccount.google.com/apppasswords*.

### 2. Iniciar el Sistema
Construya y levante los servicios:
* `docker-compose up -d --build`
* *Nota: El sistema utiliza 1 solo worker para evitar duplicidad de correos*.

### 3. Inicializar Base de Datos
Ejecute estos comandos en orden para crear las tablas y el administrador:
* `docker-compose exec web flask db upgrade`
> Nota: Si este paso le da un problema ejecute "docker-compose down -v" y lenvate nuevamente los servicios "docker-compose up -d --build".
* `docker-compose exec web python semilla.py`

> **Credenciales por defecto:**
> * **Usuario:** admin
> * **Contraseña:** 123456

---
## Reportes por Correo
  * Ingrese al panel de Administrador.
  * Diríjase a la sección Notificaciones y agregue los correos de los destinatarios.
  * Use el botón Enviar ahora para forzar un envío manual y validar la conectividad SMTP de inmediato.
---

## Utilidades de Administración

### Gestión de Credenciales
Este script centraliza el control de acceso desde la terminal, permitiendo administrar cuentas de Administradores y Guardias de forma segura.

* **Para Administradores (`admin`)**:
    * **Búsqueda y Creación**: Si el nombre de usuario existe, actualiza su contraseña. Si no existe, crea un nuevo perfil de administrador automáticamente.
    * **Renombrado**: Permite cambiar el identificador de una cuenta existente. Si se ingresa un cuarto argumento, el sistema sobrescribe el nombre de usuario actual por el nuevo.
    * **Uso**: `docker-compose exec web python gestionar_usuarios.py admin <usuario_actual/nuevo> <password> [nuevo_nombre]`
* **Para Guardias (`guardia`)**:
    * **Validación de RUT**: El script limpia cualquier formato previo (puntos o guiones) y normaliza el RUT al estándar oficial (`XX.XXX.XXX-X`) antes de consultar la base de datos.
    * **Restricción**: El guardia debe estar registrado previamente en el sistema a través del panel web para poder actualizar su contraseña.
    * **Uso**: `docker-compose exec web python gestionar_usuarios.py guardia <RUT> <nueva_password>`

> **Seguridad**: Las contraseñas se almacenan mediante hashing cifrado. Al no existir formularios de creación de administradores en la web, el control de privilegios queda restringido a usuarios con acceso al servidor.

### Impresión de Códigos QR físicos
*   En el Panel Web, vaya a Zonas y seleccione la instalación deseada en el filtro.
*   Haga clic en Imprimir QRs.
*   En la nueva pestaña, presione Ctrl + P (o Cmd + P en Mac). El diseño está optimizado con reglas CSS @media print para hojas tamaño A4.

---
## Acceso al Sistema
* **Servidor:** `http://localhost:5000`
* **Red Local:** `http://<IP_DEL_SERVIDOR>:5000`
---
## Comandos Útiles
* **Ver logs web:** `docker-compose logs -f web`
* **Ver logs de la DB:** `docker-compose logs -f db`
* **Detener:** `docker-compose down`
* **Reiniciar:** `docker-compose down && docker-compose up -d`
---
## Estructura del Proyecto
* `/app`: Código fuente de la aplicación Flask.
* `/migrations`: Scripts de versión de la base de datos.
* `Dockerfile`: Configuración de la imagen del servidor.
* `docker-compose.yml`: Orquestación de servicios y base de datos.
