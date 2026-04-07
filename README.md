# Sistema de Control de Rondas y Seguridad

Sistema web integral para la gestión, monitoreo y auditoría de rondas de seguridad mediante escaneo de códigos QR. Diseñado para ser desplegado mediante contenedores Docker para garantizar estabilidad y facilidad de mantenimiento.

## Características Principales
* **Gestión de Guardias:** Alta, baja y control de usuarios activos.
* **Mapeo de Zonas:** Creación de puntos de control con coordenadas GPS.
* **Generación de QR:** Módulo para visualizar e imprimir etiquetas identificadoras.
* **Reportes Avanzados:**
    * **Visualización:** Rondas del día con estados (Correcto, Tarde, Lejos o Crítico).
    * **Exportación:** Descarga de reportes en CSV por rango de fechas.
    * **Automatización:** Envío diario automático del reporte de la jornada anterior a las 07:00 AM.
* **App Móvil (Web):** Interfaz adaptativa para el escaneo desde celulares.

---

## Requisitos de Instalación
* **Docker** (v20.10+).
* **Docker Compose** (v1.29+).
* **Configuración SMTP:** Cuenta de correo (ej: Gmail con "Contraseña de Aplicación").

> **IMPORTANTE: USO DE CÁMARA Y HTTPS**
> Los navegadores móviles bloquean el acceso a la cámara si el sitio no es seguro. Para usar el escáner QR es **obligatorio** configurar un Proxy Inverso con certificado SSL.

---

## Despliegue Paso a Paso

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
1. Ingrese al panel de **Administrador**.
2. En la sección **Reportes Automáticos**, agregue los emails.
3. Use el botón **Enviar ahora** para validar la configuración de inmediato.

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

### Impresión de QR
1. En el Panel, clic en **🖨️ Imprimir QRs**.
2. Presione `Ctrl + P` en la vista de impresión.

---

## Acceso al Sistema
* **Servidor:** `http://localhost:5000`
* **Red Local:** `http://<IP_DEL_SERVIDOR>:5000`

---

## Comandos Útiles
* **Ver logs:** `docker-compose logs -f web`
* **Detener:** `docker-compose down`
* **Reiniciar:** `docker-compose down && docker-compose up -d`

---

## Estructura del Proyecto
* `/app`: Código fuente de la aplicación Flask.
* `/migrations`: Scripts de versión de la base de datos.
* `Dockerfile`: Configuración de la imagen del servidor.
* `docker-compose.yml`: Orquestación de servicios y base de datos.
