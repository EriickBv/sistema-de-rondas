# Manual de Arquitectura Técnica
## Sistema de Control de Rondas de Seguridad — v1.0

**Clasificación:** Documento Interno Técnico  
**Audiencia:** Ingenieros de Software, Equipo de Desarrollo, Auditores Técnicos  
**Stack:** Python 3.9 · Flask · SQLAlchemy · MySQL 8.0 · Docker · JWT

---

## Índice

1. [Visión General del Sistema](#1-visión-general-del-sistema)
2. [Modelo Entidad-Relación](#2-modelo-entidad-relación)
3. [Motor de Rutas Secuenciales](#3-motor-de-rutas-secuenciales)
4. [Lógica Temporal y Manejo de Zonas Horarias](#4-lógica-temporal-y-manejo-de-zonas-horarias)
5. [Arquitectura Multi-Sede](#5-arquitectura-multi-sede)
6. [Seguridad Integral](#6-seguridad-integral)
7. [Sistema de Reportes y Jobs Programados](#7-sistema-de-reportes-y-jobs-programados)
8. [Consideraciones de Performance](#8-consideraciones-de-performance)
9. [Limitaciones Conocidas y Deuda Técnica](#9-limitaciones-conocidas-y-deuda-técnica)

---

## 1. Visión General del Sistema

El sistema sigue el patrón **Application Factory** de Flask, que centraliza la creación de la instancia de la aplicación y permite instanciar configuraciones distintas (desarrollo, producción, testing) sin modificar código de producción.

```
run.py
└── create_app(config_name)       # app/__init__.py
    ├── app.config.from_object()   # config.py
    ├── db.init_app(app)           # SQLAlchemy
    ├── cors.init_app(app)         # Flask-CORS
    ├── migrate.init_app(app, db)  # Flask-Migrate (Alembic)
    ├── scheduler.init_app(app)    # APScheduler
    └── app.register_blueprint()  # 5 blueprints de API + 1 de vistas
```

### Blueprints registrados

| Blueprint    | Prefijo         | Responsabilidad |
|---|---|---|
| `auth_bp`    | `/api/auth`     | Autenticación, creación y gestión de usuarios |
| `rondas_bp`  | `/api/rondas`   | Motor de ruta secuencial, saltos de punto |
| `zonas_bp`   | `/api/zonas`    | CRUD de puntos de control, reordenamiento, QR |
| `sedes_bp`   | `/api/sedes`    | CRUD de instalaciones |
| `reportes_bp`| `/api/reportes` | Dashboard, export CSV, email, impresión QR |
| `web_bp`     | `/`             | Servir los templates HTML estáticos |

El servidor de producción es **Gunicorn** (`-w 1 --threads 2`). El uso de un único worker es una decisión deliberada: `APScheduler` no es compatible con múltiples workers de Gunicorn sin un backend de almacenamiento distribuido (como Redis). Con un solo worker y dos threads se mantiene la concurrencia suficiente para el caso de uso mientras se evita la duplicación de jobs programados.

---

## 2. Modelo Entidad-Relación

### 2.1 Diagrama de Tablas

```sql
sedes (id_sede PK, nombre UNIQUE, creado_en)
    │
    ├─── guardias (id_guardia PK, nombre, email UNIQUE, rut UNIQUE,
    │              password_hash, activo BOOL, id_sede FK → sedes)
    │
    └─── puntos_control (id_punto PK, nombre_zona, token_qr UNIQUE,
                         latitud DECIMAL(10,8), longitud DECIMAL(11,8),
                         radio_permitido INT DEFAULT 20,
                         numero_orden INT, id_sede FK → sedes)

registros_ronda (id_registro BIGINT PK,
                 id_guardia FK → guardias ON DELETE SET NULL,
                 id_punto   FK → puntos_control ON DELETE SET NULL,
                 fecha_hora DATETIME,   ← almacenada en UTC
                 lat_real DECIMAL(10,8), long_real DECIMAL(11,8),
                 distancia_error DECIMAL(10,2),
                 observacion TEXT)

administradores (id_admin PK, usuario UNIQUE, password_hash)

destinatarios_reporte (id PK, email UNIQUE, activo BOOL, creado_en)
```

### 2.2 `RegistroRonda` como Núcleo Transaccional

`RegistroRonda` es la entidad de mayor importancia estratégica del sistema. Actúa simultáneamente como:

- **Log de auditoría:** Registro inmutable de cada evento de escaneo.
- **Estado de progreso:** La consulta `ORDER BY fecha_hora DESC LIMIT 1` sobre esta tabla determina en qué punto de la ronda se encuentra cada guardia en tiempo real.
- **Fuente de verdad para reportes:** Todo el pipeline de CSV, dashboard y emails lo usa como fuente primaria.

La decisión de usar `ON DELETE SET NULL` (en lugar del `CASCADE` más común) es crítica para la **integridad de la auditoría**: eliminar un guardia o zona del sistema no debe borrar el historial de eventos. Los registros históricos permanecen con `id_guardia = NULL` y `id_punto = NULL`, y las consultas muestran `"Guardia Eliminado"` / `"Zona Eliminada"` en los reportes.

```python
# Comprobación en capa de presentación (reportes.py)
r.guardia.nombre if r.guardia else "Guardia Eliminado"
r.punto.nombre_zona if r.punto else "Zona Eliminada"
```

### 2.3 Tipos de Datos y Precisión GPS

Las coordenadas se almacenan como `DECIMAL(10,8)` (latitud) y `DECIMAL(11,8)` (longitud), lo que provee una precisión de ~1.1mm, más que suficiente para cualquier validación de presencia física. El uso de `DECIMAL` en lugar de `FLOAT` evita los errores de redondeo de punto flotante en comparaciones y cálculos acumulativos.

---

## 3. Motor de Rutas Secuenciales

### 3.1 Definición de Ruta

Una "ruta" en este sistema no es una entidad en base de datos sino una **secuencia virtual** construida en memoria a partir del atributo `numero_orden` de `PuntoControl`. Esto permite máxima flexibilidad: el administrador puede reordenar la ruta en tiempo real desde el panel web sin afectar los registros históricos.

### 3.2 Pipeline de Validación (`POST /api/rondas/`)

El endpoint de escaneo ejecuta el siguiente pipeline en cada request:

```
Request (token_qr, lat, long, observacion)
    │
    ▼
[1] Autenticación JWT → obtener id_guardia, rol
    │
    ▼
[2] Lookup de PuntoControl por token_qr
    → 404 si no existe
    │
    ▼
[3] Validación de Sede
    → guardia.id_sede != punto.id_sede → 403 Forbidden
    │
    ▼
[4] Cálculo de Distancia Haversine
    dist = calcular_distancia(lat_guardia, long_guardia,
                              punto.latitud, punto.longitud)
    → dist > radio_permitido → status = "warning", alerta GPS
    │
    ▼
[5] Consulta de Estado de Ronda
    ultimo = RegistroRonda WHERE id_guardia = X
             AND fecha_hora BETWEEN inicio_jornada_utc AND fin_jornada_utc
             ORDER BY fecha_hora DESC LIMIT 1
    │
    ▼
[6] Validación de Orden Secuencial
    orden_esperado = ultimo.punto.numero_orden + 1
    es_reinicio = (orden_anterior == max_orden AND orden_actual == 1)
    → orden incorrecto y no es reinicio → alerta de secuencia
    │
    ▼
[7] Escritura en BD
    RegistroRonda(obs = f"{obs_usuario} [ALERTA: {alertas}]")
    │
    ▼
[8] Response: { status, message, proximo_punto, hora_local }
```

### 3.3 Fórmula Haversine

La distancia entre dos coordenadas geográficas se calcula usando la fórmula de Haversine, que provee la distancia de círculo máximo en la superficie esférica de la Tierra:

```python
def calcular_distancia(lat1, lon1, lat2, lon2):
    R = 6_371_000  # Radio de la Tierra en metros
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi       = math.radians(lat2 - lat1)
    dlambda    = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2)**2
         + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2)**2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return round(R * c, 2)  # Resultado en metros
```

La precisión de esta implementación es de ±0.3% para distancias menores a 20km, exacta para el caso de uso de rondas de seguridad dentro de un predio.

### 3.4 Mecanismo de Saltos (`POST /api/rondas/saltar`)

Cuando un punto es inaccesible, el guardia puede omitirlo con justificación obligatoria. El sistema:

1. Determina cuál era el punto objetivo (siguiente en la secuencia del último registro del guardia).
2. Crea un `RegistroRonda` en ese punto con `lat_real=0, long_real=0, distancia_error=0` y `observacion="⚠️ OMITIDO: <motivo>"`.
3. Avanza el cursor de la ronda a `siguiente_indice + 1`.

Esto garantiza que la **ronda no quede bloqueada** por un punto inaccesible y que el historial de auditoría refleje fielmente lo ocurrido.

---

## 4. Lógica Temporal y Manejo de Zonas Horarias

### 4.1 El Problema del Turno Nocturno

Chile (UTC-4 en CLST, UTC-3 en CLT) presenta el siguiente caso límite: un guardia inicia ronda a las **23:00 CLT** (02:00 UTC del día siguiente). Sin manejo explícito de zona horaria, una query de tipo `WHERE fecha_hora >= '2024-01-15 00:00:00'` (en UTC) no captura los registros del guardia de las 23:00-23:59 CLT, que en UTC corresponden a `00:00-00:59` del día siguiente.

### 4.2 Solución Implementada

Todos los timestamps se almacenan en **UTC puro** (no hay columnas `DATETIME` con zona horaria en MySQL 8.0 sin configuración explícita). La función `_rango_hoy_utc()` construye el rango del día laboral en hora local y lo convierte a UTC:

```python
ZONA_CL = pytz.timezone('America/Santiago')

def _rango_hoy_utc() -> tuple[datetime, datetime]:
    """
    Construye los límites UTC del día de hoy según la zona horaria de Santiago.
    Maneja correctamente el cambio de horario de verano (CLST/CLT).
    """
    hoy_local = datetime.now(ZONA_CL).date()
    inicio = ZONA_CL.localize(
        datetime.combine(hoy_local, dt_time.min)  # 00:00:00 CLT
    ).astimezone(pytz.utc)
    fin = ZONA_CL.localize(
        datetime.combine(hoy_local, dt_time.max)  # 23:59:59.999999 CLT
    ).astimezone(pytz.utc)
    return inicio, fin
```

`pytz.localize()` es crítico aquí: no es equivalente a `replace(tzinfo=...)`. El método `localize` de pytz resuelve correctamente la ambigüedad de horas durante el cambio de horario de verano (DST), mientras que `replace` puede asignar el offset incorrecto en las horas ambiguas del cambio.

### 4.3 Conversión en Capa de Presentación

La conversión a hora local ocurre únicamente al generar reportes, nunca al almacenar:

```python
# En el pipeline CSV (reportes.py)
fecha_local = r.fecha_hora.replace(tzinfo=pytz.utc).astimezone(ZONA_CL)
yield fecha_local.strftime('%Y-%m-%d %H:%M:%S')
```

### 4.4 Defecto Conocido en `reportes.py`

El endpoint `GET /api/reportes?tipo=hoy` usa actualmente datetimes naive para el filtro:

```python
# INCORRECTO — datetime naive, asume que UTC == hora local
q.filter(fecha_hora >= datetime.combine(hoy, time.min))
```

La corrección consiste en mover `_rango_hoy_utc()` a `utils.py` y usarla en ambos módulos:

```python
# CORRECTO
from app.utils import rango_hoy_utc
inicio_utc, fin_utc = rango_hoy_utc()
q.filter(RegistroRonda.fecha_hora.between(inicio_utc, fin_utc))
```

---

## 5. Arquitectura Multi-Sede

### 5.1 Modelo de Aislamiento

El sistema implementa un **multi-tenant lógico** (no aislamiento a nivel de schema o base de datos). El discriminador de tenant es la columna `id_sede`, presente en `Guardia` y `PuntoControl`. El aislamiento se garantiza en dos niveles:

**Nivel de API (runtime):**

```python
# app/api/rondas.py — registrar_ronda()
guardia = Guardia.query.get(id_guardia)
if guardia.id_sede and punto.id_sede and guardia.id_sede != punto.id_sede:
    return jsonify({"message": "Este punto pertenece a otra instalación"}), 403
```

La condición `guardia.id_sede and punto.id_sede` (ambos deben ser no-nulos) es una decisión de diseño deliberada: los guardias sin sede asignada pueden escanear puntos de cualquier instalación. Esto permite escenarios de guardias flotantes o períodos de transición al configurar el sistema.

**Nivel de eliminación (`DELETE /api/sedes/<id>`):**

```python
# app/api/sedes.py
Guardia.query.filter_by(id_sede=id_sede).update({'id_sede': None})
PuntoControl.query.filter_by(id_sede=id_sede).update({'id_sede': None})
db.session.delete(sede)
```

La sede se elimina sin cascade: guardias y zonas quedan desasociados (sin sede) pero no eliminados, preservando la continuidad operacional.

### 5.2 Rutas Virtuales por Sede

Las "rutas" son secuencias virtuales construidas en memoria. Para que el aislamiento multi-sede sea completo, todas las consultas de `PuntoControl` dentro del motor de rondas deben filtrar por el `id_sede` del guardia autenticado:

```python
# VERSIÓN CON AISLAMIENTO CORRECTO (corrección recomendada)
def _get_puntos_sede(id_sede: int | None):
    q = PuntoControl.query.order_by(PuntoControl.numero_orden)
    if id_sede:
        q = q.filter_by(id_sede=id_sede)
    return q.all()

def obtener_siguiente_punto_nombre(id_punto_actual: int, id_sede: int | None) -> str:
    todos = _get_puntos_sede(id_sede)
    if not todos:
        return "Fin de Ronda"
    idx = next((i for i, p in enumerate(todos) if p.id_punto == id_punto_actual), -1)
    return todos[(idx + 1) % len(todos)].nombre_zona if idx != -1 else "Desconocido"
```

La misma corrección aplica al endpoint `/saltar` y al cálculo de `max_orden` para la detección de reinicio de ronda.

### 5.3 Aislamiento en Reportes

Los endpoints de reporte reciben un parámetro opcional `id_sede` que aplica un `JOIN` adicional para filtrar por la sede del guardia:

```python
def _query_registros(desde_utc=None, hasta_utc=None, id_sede=None):
    q = RegistroRonda.query
    if id_sede is not None:
        q = (q.join(Guardia, RegistroRonda.id_guardia == Guardia.id_guardia, isouter=True)
               .filter(Guardia.id_sede == id_sede))
    return q.order_by(RegistroRonda.fecha_hora.desc()).all()
```

El `isouter=True` (LEFT OUTER JOIN) garantiza que los registros con `id_guardia = NULL` (guardias eliminados) también sean incluidos en el resultado.

---

## 6. Seguridad Integral

### 6.1 Autenticación JWT

El sistema utiliza dos tipos de tokens con parámetros distintos según la criticidad del rol:

| Parámetro | Admin (`rol: 'admin'`) | Guardia (`rol: 'guardia'`) |
|---|---|---|
| Expiración | 4 horas | 12 horas |
| Algoritmo | HS256 | HS256 |
| Claims | `id`, `rol`, `exp` | `id`, `rol`, `exp` |
| Almacenamiento (cliente) | `localStorage` | `localStorage` |

Los tokens se firman con `SECRET_KEY` generada por `secrets.token_urlsafe(50)` (50 bytes = 400 bits de entropía), lo que excede ampliamente las recomendaciones mínimas para HMAC-SHA256.

```python
# app/api/auth.py
token = jwt.encode({
    'id':  admin.id_admin,
    'rol': 'admin',
    'exp': datetime.now(timezone.utc) + timedelta(hours=4)
}, current_app.config['SECRET_KEY'], algorithm="HS256")
```

El middleware `@token_required` verifica la firma y la expiración en cada request protegido. Para guardias, realiza una validación adicional consultando la base de datos para verificar que la cuenta siga activa (un guardia desactivado en el panel invalida inmediatamente su token vigente):

```python
# app/utils.py — token_required
if data.get('rol') == 'guardia':
    guardia_db = Guardia.query.get(data['id'])
    if not guardia_db or not guardia_db.activo:
        return jsonify({'message': 'Tu cuenta ha sido desactivada.'}), 401
```

Esta validación en base de datos (un query adicional por request de guardia) es el costo de no tener un mecanismo de revocación explícita de JWTs. Es un trade-off aceptable para este caso de uso.

### 6.2 Hashing de Contraseñas (PBKDF2-SHA256)

Las contraseñas se almacenan usando `werkzeug.security.generate_password_hash`, que por defecto aplica **PBKDF2-SHA256** con 260,000 iteraciones (configurable). El formato almacenado es `method$salt$hash`, donde el salt es único por contraseña:

```python
# app/models.py
def set_password(self, password):
    self.password_hash = generate_password_hash(password)  # PBKDF2-SHA256

def check_password(self, password):
    return check_password_hash(self.password_hash, password)
```

PBKDF2 es una función de derivación de clave diseñada para ser computacionalmente costosa, lo que hace inviables los ataques de fuerza bruta y diccionario incluso con hardware especializado.

### 6.3 Protocolo de Tokens QR y Prevención de Fraude

Cada `PuntoControl` tiene un `token_qr` generado como los primeros 12 caracteres de un UUID hex:

```python
# app/api/zonas.py — agregar_zona()
token_qr = str(uuid.uuid4().hex)[:12].upper()
# Ejemplo: "A3F7B2C91D4E"
```

12 caracteres hexadecimales = 48 bits de entropía. El espacio de búsqueda es 16^12 ≈ 281 billones de combinaciones, lo que hace inviable cualquier ataque de fuerza bruta sobre el endpoint de escaneo.

**Protocolo de Regeneración:** Si se sospecha que un guardia fotografió un QR para escanearlo remotamente, el administrador puede invalidar el token existente con un único endpoint:

```python
# app/api/zonas.py — regenerar_qr()
@zonas_bp.route('/<int:id_zona>/regenerar_qr', methods=['PUT'])
@token_required
def regenerar_qr(id_zona):
    zona = PuntoControl.query.get_or_404(id_zona)
    zona.token_qr = str(uuid.uuid4().hex)[:12].upper()  # Nuevo token
    db.session.commit()
    # El token anterior queda inmediatamente inválido
```

El código QR físico instalado en la pared sigue siendo el mismo objeto impreso, pero el token que contiene ya no existe en la base de datos, por lo que cualquier intento de escanearlo (legítimo o fraudulento) retorna `404 Not Found`.

### 6.4 Autorización por Roles (RBAC Básico)

El sistema implementa un control de acceso basado en roles con dos niveles: `admin` y `guardia`. La verificación se hace en cada endpoint según el claim `rol` del JWT:

```python
# Ejemplo de guard de autorización
if request.usuario_rol != 'admin':
    return jsonify({"message": "No autorizado"}), 403
```

No existe un sistema de permisos granular (e.g., admin de solo lectura). Todos los administradores tienen acceso completo a todas las instalaciones. Para organizaciones que requieran aislamiento entre administradores de distintas sedes, se requeriría extender el modelo `Administrador` con una FK a `Sede`.

---

## 7. Sistema de Reportes y Jobs Programados

### 7.1 APScheduler y Unicidad del Job

El reporte diario se ejecuta a través de `Flask-APScheduler`, que wrappea `APScheduler` para integrarse con el contexto de la aplicación Flask:

```python
# app/jobs.py
@scheduler.task('cron', id='reporte_diario', hour=7, minute=0,
                misfire_grace_time=3600)
def reporte_diario():
    from app.api.reportes import enviar_reporte_diario
    enviar_reporte_diario()
```

`misfire_grace_time=3600` indica que si el job no se ejecutó en el momento exacto (e.g., el servidor estaba caído), APScheduler lo ejecutará si el tiempo transcurrido desde la hora programada es menor a 1 hora.

La restricción de **1 solo worker de Gunicorn** previene que múltiples procesos registren el mismo job y ejecuten envíos duplicados de correo. En un escenario de escalabilidad horizontal, se requeriría migrar el scheduler a un backend distribuido (e.g., Celery + Redis).

### 7.2 Pipeline de Email Multi-Sede

`enviar_reporte_diario()` genera un CSV por cada sede con actividad en la jornada anterior, adjuntando todos los archivos en un único correo:

```
Para cada Sede con actividad ayer:
    → Generar CSV con _filas_csv(registros_de_esa_sede)
    → Adjuntar al MIMEMultipart

Para guardias sin sede asignada:
    → Si hay actividad, adjuntar CSV "SinSede"

Enviar via SMTP (STARTTLS) a todos los DestinatarioReporte activos
```

Los CSV usan BOM UTF-8 (`'\ufeff'`) para garantizar compatibilidad con Microsoft Excel, que sin el BOM interpreta incorrectamente caracteres especiales como acentos y la letra ñ.

### 7.3 Export CSV Streaming

Para evitar timeouts con datasets grandes, el export CSV usa `stream_with_context`:

```python
def stream():
    buf = io.StringIO()
    w   = csv.writer(buf, delimiter=';')
    yield '\ufeff'          # BOM UTF-8
    w.writerow(HEADER_CSV)
    yield buf.getvalue(); buf.seek(0); buf.truncate(0)
    for fila in _filas_csv(_query_registros(...)):
        w.writerow(fila)
        yield buf.getvalue(); buf.seek(0); buf.truncate(0)

return Response(stream_with_context(stream()), mimetype='text/csv')
```

Cada fila se genera y envía al cliente de forma incremental. El objeto `buf` se reutiliza en cada iteración (seek + truncate) para evitar acumulación de memoria.

---

## 8. Consideraciones de Performance

### 8.1 Índices en `RegistroRonda`

```python
__table_args__ = (
    db.Index('ix_ronda_guardia_fecha', 'id_guardia', 'fecha_hora'),
    db.Index('ix_ronda_fecha_hora',    'fecha_hora'),
    db.Index('ix_ronda_id_punto',      'id_punto'),
)
```

- **`ix_ronda_guardia_fecha`:** Optimiza las consultas del motor de rutas (`WHERE id_guardia = X ORDER BY fecha_hora DESC LIMIT 1`) y el dashboard de actividad por guardia.
- **`ix_ronda_fecha_hora`:** Optimiza las consultas de rango temporal del export CSV y los reportes diarios.
- **`ix_ronda_id_punto`:** Optimiza el análisis de actividad por zona de control.

Con estos índices, el tiempo de respuesta de las consultas críticas se mantiene en O(log n) incluso con millones de registros.

### 8.2 `id_registro` como `BigInteger`

Un sistema con 10 guardias haciendo 5 rondas diarias de 10 puntos cada una genera 500 registros por día, o ~182,500 por año. Un `Integer` de 32 bits soporta ~2.1 mil millones de registros, equivalente a ~11,500 años de operación en este escenario. No obstante, el uso de `BigInteger` (64 bits) es una práctica defensiva correcta que elimina cualquier riesgo de desbordamiento a largo plazo o en despliegues de mayor escala.

---
