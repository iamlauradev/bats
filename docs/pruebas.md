# Pruebas — Asistenciator IoT

Referencia operativa de todos los casos de prueba del proyecto.  
Actualizar esta hoja antes de cualquier cambio importante para confirmar que no hay regresiones.

**Convenciones:**
- Los comandos `curl` asumen que la aplicación corre en `http://localhost:5000` (acceso local) o en `https://TU_DOMINIO` (a través del túnel Cloudflare).
- `SCHEDULER_KEY` es el valor de la variable de entorno configurada en `.env`.
- `SESSION_COOKIE` es el valor de la cookie `session` capturada tras un login exitoso.

---

## 1. Autenticación y sesiones

### A1 — Login con credenciales válidas

**Cómo probarlo:**
1. Abre el navegador y ve a `http://localhost:5000/login`.
2. Introduce un usuario y contraseña correctos.
3. Pulsa *Entrar*.

**Qué observar:**
- Redirige a `/` (dashboard).
- En las DevTools → Application → Cookies, la cookie `session` debe tener los flags `HttpOnly` y `Secure` activados.

**Verificación alternativa con curl:**
```bash
curl -i -c cookies.txt \
  -X POST http://asistance:5000/login \
  -d "email=admin@centro.es&password=TU_PASSWORD&csrf_token=<TOKEN>"
```
Output esperado: `HTTP/1.1 302 FOUND` con `Location: /`.

**Resultado:** ✅

---

### A2 — Login con contraseña incorrecta

**Cómo probarlo:**
1. Ve a `/login` e introduce un email existente con una contraseña errónea.
2. Pulsa *Entrar*.

**Qué observar:**
- Aparece el mensaje genérico **"Credenciales inválidas"** (no revela si el email existe).
- No se produce redirección; se queda en `/login`.

```bash
curl -i -X POST http://localhost:5000/login \
  -d "email=admin@centro.es&password=MALA&csrf_token=<TOKEN>"
# → HTTP 200, body contiene "Credenciales inválidas"
```

**Resultado:** ✅

---

### A3 — Login con usuario inactivo

**Cómo probarlo:**
1. Desde el panel *Usuarios*, desactiva una cuenta (toggle a "inactivo").
2. Cierra sesión e intenta autenticarte con esa cuenta.

**Qué observar:**
- Mensaje **"Cuenta deshabilitada"** en la página de login.
- No se produce redirección al dashboard.

**Resultado:** ✅

---

### A4 — Rate-limit: 5 logins fallidos en 10 minutos

**Cómo probarlo:**
```bash
for i in 1 2 3 4 5 6; do
  curl -s -o /dev/null -w "Intento $i → HTTP %{http_code}\n" \
    -X POST http://localhost:5000/login \
    -d "email=admin@centro.es&password=MALA&csrf_token=x"
done
```

**Output esperado:**
```
Intento 1 → HTTP 200
Intento 2 → HTTP 200
Intento 3 → HTTP 200
Intento 4 → HTTP 200
Intento 5 → HTTP 200
Intento 6 → HTTP 429
```
El intento 6 devuelve 429 y muestra la página de error personalizada.

**Resultado:** ✅

---

### A5 — Cookie de sesión caducada por inactividad (2 h)

**Cómo probarlo:**
1. Inicia sesión y copia el valor de la cookie `session`.
2. Espera más de 2 horas sin actividad (o modifica `PERMANENT_SESSION_LIFETIME` temporalmente a 5 s para agilizar la prueba).
3. Intenta acceder a `/` con esa cookie.

**Verificación rápida (sesión falsa):**
```bash
curl -i -H "Cookie: session=COOKIE_CADUCADA" http://localhost:5000/
# → HTTP 302 Location: /login
```

**Qué observar:**
- Redirige a `/login`. El usuario debe volver a autenticarse.

**Resultado:** ✅

---

### A6 — Acceso a `/setup` con la BD vacía

**Cómo probarlo:**
1. Arranca los contenedores con la BD vacía (o borra la tabla `usuarios`).
2. Ve a `http://localhost:5000/setup`.

**Qué observar:**
- Se muestra el wizard de creación del primer administrador.
- El formulario pide nombre, email y contraseña.

**Resultado:** ✅

---

### A7 — Acceso a `/setup` con admin ya creado

**Cómo probarlo:**
```bash
curl -i http://localhost:5000/setup
# → HTTP 403
```

**Qué observar:**
- Devuelve 403 con la página de error personalizada.
- No filtra ni lista usuarios existentes.

**Resultado:** ✅

---

### A8 — Logout

**Cómo probarlo:**
1. Inicia sesión.
2. Haz clic en *Cerrar sesión* en el menú lateral.

**Verificación con curl:**
```bash
# Con cookie de sesión válida:
curl -i -b cookies.txt http://localhost:5000/logout
# → HTTP 302 Location: /login

# Intentar reutilizar esa cookie después:
curl -i -b cookies.txt http://localhost:5000/
# → HTTP 302 Location: /login  (sesión destruida)
```

**Resultado:** ✅

---

## 2. Autorización por roles

### R1 — Profesor accede al dashboard

**Cómo probarlo:**
1. Inicia sesión con una cuenta de rol `profesor`.
2. Ve a `http://localhost:5000/`.

**Qué observar:**
- HTTP 200. Se muestra el dashboard con los datos de asistencia.

**Resultado:** ✅

---

### R2 — Profesor intenta `POST /alumnos/add`

**Cómo probarlo:**
```bash
curl -i -b cookies_profesor.txt \
  -X POST http://localhost:5000/alumnos/add \
  -d "nombre=Test&apellidos=Test&mac=AA:BB:CC:DD:EE:FF&csrf_token=<TOKEN>"
# → HTTP 403
```

**Qué observar:**
- HTTP 403 con página personalizada.
- En el sidebar de la web, el enlace *Alumnos (añadir)* no aparece para este rol.

**Resultado:** ✅

---

### R3 — Tutor intenta `POST /usuarios/add`

**Cómo probarlo:**
```bash
curl -i -b cookies_tutor.txt \
  -X POST http://localhost:5000/usuarios/add \
  -d "nombre=Test&email=t@t.es&rol=admin&csrf_token=<TOKEN>"
# → HTTP 403
```

**Qué observar:**
- HTTP 403.
- En el sidebar, la sección *Usuarios* no aparece para el rol `tutor`.

**Resultado:** ✅

---

### R4 — Tutor intenta borrar permanentemente un usuario

**Cómo probarlo:**
```bash
curl -i -b cookies_tutor.txt \
  -X POST http://localhost:5000/usuarios/delete/2 \
  -d "csrf_token=<TOKEN>"
# → HTTP 403
```

**Resultado:** ✅

---

### R5 — Admin borra al último admin del sistema

**Cómo probarlo:**
1. Asegúrate de que solo hay un usuario con rol `admin`.
2. Como ese admin, intenta borrarlo desde *Usuarios → Eliminar*.

**Qué observar:**
- Mensaje claro: **"No se puede eliminar el último administrador"**.
- La cuenta se preserva; no se ejecuta el DELETE.

**Resultado:** ✅

---

### R6 — Admin intenta borrarse a sí mismo

**Cómo probarlo:**
1. Inicia sesión como admin.
2. Ve a *Usuarios* e intenta eliminar tu propia cuenta.

**Qué observar:**
- Mensaje claro: **"No puedes eliminar tu propia cuenta"**.
- La cuenta no se borra.

**Resultado:** ✅

---

### R7 — Tutor accede a `/informes`

**Cómo probarlo:**
```bash
curl -i -b cookies_tutor.txt http://localhost:5000/informes
# → HTTP 200 con lista de informes
```

**Resultado:** ✅

---

### R8 — Profesor accede a `/informes`

**Cómo probarlo:**
```bash
curl -i -b cookies_profesor.txt http://localhost:5000/informes
# → HTTP 403
```

**Resultado:** ✅

---

## 3. Escaneo Bluetooth y registro de asistencia

> Para estos casos el sistema debe tener al menos 2 alumnos con MAC registrada y Bluetooth disponible en el host.  
> El `SCHEDULER_KEY` se obtiene del `.env`.

### E1 — Pasar lista con alumnos presentes

**Cómo probarlo:**
```bash
curl -i -X POST http://localhost:5000/escanear \
  -H "X-Scheduler-Key: $SCHEDULER_KEY"
```

**Output esperado (HTTP 200):**
```json
{
  "status": "ok",
  "presentes": 2,
  "ausentes": 0,
  "total": 2,
  "asignatura": "Nombre de la clase"
}
```

**Qué verificar en la BD:**
```sql
SELECT * FROM asistencia ORDER BY timestamp DESC LIMIT 5;
SELECT * FROM estado_alumno_dia WHERE fecha = CURDATE();
```
Debe haber una fila nueva en `asistencia` y `estado_alumno_dia` actualizado a `PRESENTE`.

**Resultado:** ✅

---

### E2 — Escaneo durante recreo configurado

**Cómo probarlo:**
1. Configura un recreo en *Configuración* para la hora actual.
2. Lanza el escaneo:
```bash
curl -i -X POST http://localhost:5000/escanear \
  -H "X-Scheduler-Key: $SCHEDULER_KEY"
```

**Output esperado (HTTP 423):**
```json
{"status": "recreo", "mensaje": "Hora de recreo (10:30–11:00)."}
```

**Resultado:** ✅

---

### E3 — Escaneo con sistema en pausa

**Cómo probarlo:**
1. En *Configuración*, activa la pausa del escaneo.
2. Lanza el escaneo:
```bash
curl -i -X POST http://localhost:5000/escanear \
  -H "X-Scheduler-Key: $SCHEDULER_KEY"
# → HTTP 423
```

**Output esperado:**
```json
{"status": "pausado", "mensaje": "El escaneo está pausado."}
```

**Resultado:** ✅

---

### E4 — Escaneo fuera de horario lectivo

**Cómo probarlo:**
1. Lanza el escaneo a una hora sin clase configurada (ej. 8:10 con la primera clase a las 8:15).
```bash
curl -i -X POST http://localhost:5000/escanear \
  -H "X-Scheduler-Key: $SCHEDULER_KEY"
# → HTTP 423
```

**Output esperado:**
```json
{"status": "fuera_horario", "mensaje": "No hay clase en este momento."}
```

**Qué observar en la web:**
- El banner de aviso aparece en el dashboard indicando que el escaneo está fuera de horario.

**Resultado:** ✅ (corregido — antes fallaba a las 8:10 incluso con clase a las 8:00)

---

### E5 — Control de frecuencia de escaneo

**Cómo probarlo:**
1. Configura la frecuencia mínima a 10 minutos en *Configuración*.
2. Lanza dos escaneos con menos de 10 minutos de diferencia:
```bash
# Primer escaneo (pasa)
curl -i -X POST http://localhost:5000/escanear \
  -H "X-Scheduler-Key: $SCHEDULER_KEY"
# → HTTP 200

# Segundo escaneo inmediato (bloqueado por frecuencia)
curl -i -X GET http://localhost:5000/estado-franja \
  -H "X-Scheduler-Key: $SCHEDULER_KEY"
# → HTTP 200, "franja_activa": false, "razon": "frecuencia"
```

**Qué observar:**
- `/estado-franja` devuelve `razon: "frecuencia"` hasta que pasen los 10 minutos.
- El script `escaneo.sh` sale silenciosamente sin loguear (motivo `frecuencia` se ignora).

**Resultado:** ✅ (corregido)

---

### E6 — Dos escaneos consecutivos con alumno ausente → notificación Telegram

**Cómo probarlo:**
1. Asegúrate de que el alumno de prueba tiene la MAC apagada/no accesible.
2. Lanza dos escaneos separados por la frecuencia mínima:
```bash
# Primer escaneo → registra AUSENTE, notificado=FALSE
curl -X POST http://localhost:5000/escanear -H "X-Scheduler-Key: $SCHEDULER_KEY"

# Espera el tiempo de frecuencia mínima, luego:
# Segundo escaneo → dispara Telegram y pone notificado=TRUE
curl -X POST http://localhost:5000/escanear -H "X-Scheduler-Key: $SCHEDULER_KEY"
```

**Qué observar:**
- Llega un mensaje al chat de Telegram configurado.
- En BD: `notificado = TRUE` para ese alumno en la fecha actual.

```sql
SELECT nombre, estado, notificado FROM estado_alumno_dia
WHERE fecha = CURDATE();
```

**Resultado:** ✅

---

### E7 — Alumno vuelve: AUSENTE → PRESENTE (reset de notificación)

**Cómo probarlo:**
1. Tras el caso E6 (alumno `notificado=TRUE`), enciende/acerca el dispositivo Bluetooth del alumno.
2. Lanza un nuevo escaneo.

**Qué observar:**
- El estado cambia a `PRESENTE`.
- `notificado` se resetea a `FALSE` en `estado_alumno_dia`.
- Esto permite que si el alumno vuelve a ausentarse, se genere una nueva alerta.

```sql
SELECT nombre, estado, notificado FROM estado_alumno_dia
WHERE fecha = CURDATE();
-- estado: PRESENTE, notificado: FALSE
```

**Resultado:** ✅

---

### E8 — Lock concurrente: dos peticiones simultáneas a `/escanear`

**Cómo probarlo:**
```bash
# Lanzar dos escaneos casi simultáneos
curl -X POST http://localhost:5000/escanear \
  -H "X-Scheduler-Key: $SCHEDULER_KEY" &
curl -X POST http://localhost:5000/escanear \
  -H "X-Scheduler-Key: $SCHEDULER_KEY"
wait
```

**Output esperado:**
- Una petición devuelve HTTP 200 (la que adquiere el lock).
- La otra devuelve HTTP 409:
```json
{"status": "busy", "mensaje": "Ya hay un escaneo en curso."}
```

**Qué verificar:**
- Solo hay una nueva fila en `asistencia` por el periodo (no duplicados).

**Resultado:** ✅

---

## 4. Notificaciones

### N1 — Telegram con configuración válida

**Cómo probarlo:**
1. Asegúrate de que `TELEGRAM_TOKEN` y `TELEGRAM_CHAT_ID` están configurados en `.env`.
2. Provoca una ausencia (ver E6) o usa el endpoint de test si existe.

**Qué observar:**
- El mensaje llega al chat de Telegram.
- En los logs del contenedor `web`:
```bash
docker logs asistenciator_web | grep NOTIFICACION_TELEGRAM
```
Output esperado: línea con `NOTIFICACION_TELEGRAM` y el nombre del alumno.

**Resultado:** ✅

---

### N2 — Telegram con `TELEGRAM_TOKEN` vacío

**Cómo probarlo:**
1. Deja `TELEGRAM_TOKEN=` vacío en `.env` y reinicia el contenedor.
2. Lanza un escaneo con alumno ausente.

**Qué observar:**
- El escaneo termina correctamente (HTTP 200).
- En logs:
```bash
docker logs asistenciator_web | grep TELEGRAM_DESACTIVADO
```
No se produce excepción ni fallo en el escaneo.

**Resultado:** ✅

---

### N3 — Correo manual al tutor (botón) con SMTP correcto

**Cómo probarlo:**
1. Configura `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USER`, `EMAIL_PASSWORD` en `.env`.
2. Ve a *Alumnos*, busca un alumno con `email_tutor` configurado.
3. Haz clic en el botón *Notificar tutor por correo*.

**Qué observar:**
- La respuesta de la web muestra éxito.
- El correo llega a la bandeja del tutor.
- En logs:
```bash
docker logs asistenciator_web | grep NOTIFICACION_EMAIL_OK
```

**Resultado:** ✅

---

### N4 — Correo con SMTP mal configurado

**Cómo probarlo:**
1. Pon `EMAIL_HOST=smtp.invalido.ejemplo` en `.env` y reinicia el contenedor.
2. Intenta enviar un correo manual al tutor.

**Qué observar:**
- La web muestra un mensaje de error visible al usuario (no un 500 en blanco).
- En logs:
```bash
docker logs asistenciator_web | grep EMAIL_ERROR
```

**Resultado:** ✅

---

### N5 — Correo a alumno sin `email_tutor` configurado

**Cómo probarlo:**
1. Crea o edita un alumno dejando el campo `email_tutor` vacío.
2. Intenta enviar correo al tutor desde el panel de alumnos.

**Qué observar:**
- Mensaje claro: **"No hay email de tutor registrado para este alumno"**.
- No se produce error 500.

**Resultado:** ✅

---

## 5. Informes semanales

### I1 — Generación automática el lunes a las 00:01

**Cómo probarlo:**
```bash
# Verificar que el cron de informes está configurado en el contenedor scheduler
docker exec asistenciator_scheduler crontab -l
# Debe aparecer una línea tipo: 1 0 * * 1 /scripts/informe.sh

# Simular la ejecución manual del script:
docker exec asistenciator_scheduler /scripts/informe.sh
```

**Qué observar:**
- Nueva fila en la tabla `informes` con HTML generado.
- Verificar en BD:
```sql
SELECT id, fecha_generacion, LENGTH(contenido_html) FROM informes ORDER BY id DESC LIMIT 1;
```
`LENGTH(contenido_html)` debe ser > 0.

**Resultado:** ✅

---

### I2 — *Generar ahora* desde la web (admin)

**Cómo probarlo:**
1. Inicia sesión como admin.
2. Ve a `/informes` y haz clic en *Generar informe ahora*.

**Qué observar:**
- Aparece inmediatamente una nueva fila en la lista de informes.
- La fecha de generación es la actual.

**Resultado:** ✅

---

### I3 — Descarga de informe como HTML

**Cómo probarlo:**
```bash
# Reemplaza <ID> por el id de un informe existente
curl -i -b cookies_admin.txt \
  http://localhost:5000/informes/descargar/<ID>
```

**Output esperado:**
```
HTTP/1.1 200 OK
Content-Disposition: attachment; filename="informe_2026-05-08.html"
Content-Type: text/html; charset=utf-8
```

El fichero descargado debe ser HTML válido.

**Resultado:** ✅

---

### I4 — Informe con base de datos vacía (sin datos de asistencia)

**Cómo probarlo:**
1. Con la BD sin filas en `asistencia` para la semana actual, genera un informe manual (ver I2).

**Qué observar:**
- El informe se genera sin errores (no devuelve 500).
- El HTML contiene un mensaje tipo **"Sin datos de asistencia para este período"** en lugar de una tabla vacía o un traceback.

**Resultado:** ✅

---

## 6. Backups y restauración

### B1 — `backup.sh` ejecución manual

**Cómo probarlo:**
```bash
cd ~/iot-bluetooth-attendance
./scripts/backup.sh
```

**Output esperado:**
```
[2026-05-08 03:00:01] Iniciando backup de 'control_asistencia'...
[2026-05-08 03:00:03] Backup completado: backup_20260508_030001.sql.gz (45K)
[2026-05-08 03:00:03] Estado: 1 backup(s) almacenado(s) — 45K en disco
[2026-05-08 03:00:03] ✅ Backup finalizado correctamente.
```

**Verificar el fichero:**
```bash
ls -lh /backups/asistenciator/backup_*.sql.gz
# -rw------- (chmod 600)

# Comprobar que no está vacío y es gzip válido:
gzip -t /backups/asistenciator/backup_<TIMESTAMP>.sql.gz && echo "OK"
```

**Resultado:** ✅

---

### B2 — `backup.sh` con contenedor `db` parado

**Cómo probarlo:**
```bash
docker compose stop db
./scripts/backup.sh
echo "Código de salida: $?"
docker compose start db
```

**Output esperado:**
```
[...] ERROR: El contenedor asistenciator_db no está en ejecución.
[...]        Ejecuta: docker compose up -d db
Código de salida: 1
```

El script aborta con código ≠ 0 y no crea ningún fichero corrupto.

**Resultado:** ✅

---

### B3 — Retención: eliminar backups de más de 30 días

**Cómo probarlo:**
```bash
# Crear un fichero de backup con fecha antigua (simulado)
touch -d "31 days ago" /backups/asistenciator/backup_19990101_000000.sql.gz

# Ejecutar el backup (activará la limpieza de retención)
./scripts/backup.sh
```

**Output esperado:**
```
[...] Limpieza: 1 backup(s) antiguo(s) eliminado(s) (>30 días)
```

Verificar que el fichero antiguo ya no existe:
```bash
ls /backups/asistenciator/backup_19990101_000000.sql.gz
# ls: no existe
```

**Resultado:** ✅

---

### B4 — Restauración en entorno aislado

**Cómo probarlo:**
```bash
# 1. Hacer un backup de la BD actual
./scripts/backup.sh

# 2. Anotar el nombre del fichero generado
BACKUP=$(ls -t /backups/asistenciator/backup_*.sql.gz | head -1)

# 3. Restaurar (en el mismo entorno o en uno aislado)
./scripts/restaurar.sh "$BACKUP"
# Confirmar escribiendo 'SI' cuando se pida

# 4. Reiniciar el servicio web
docker compose restart web
```

**Qué verificar:**
```sql
-- Comprobar que las tablas principales existen y tienen datos
SELECT COUNT(*) FROM alumnos;
SELECT COUNT(*) FROM usuarios;
SELECT COUNT(*) FROM asistencia;
```

Los datos deben coincidir con los del estado antes del backup.

**Resultado:** ✅

---

### B5 — Restauración con backup corrupto

**Cómo probarlo:**
```bash
# Crear un fichero gzip corrupto
echo "esto no es gzip" > /tmp/backup_corrupto.sql.gz

./scripts/restaurar.sh /tmp/backup_corrupto.sql.gz
echo "Código de salida: $?"
```

**Output esperado:**
```
[ERROR] El fichero no es un gzip válido o está corrupto: /tmp/backup_corrupto.sql.gz
Código de salida: 1
```

El script aborta antes de tocar la BD (la validación `gzip -t` ocurre al inicio).

**Resultado:** ✅

---

## 7. Pruebas de integración

### INT-1 — Arranque coordinado `web` ↔ `db`

**Cómo probarlo:**
```bash
# Arranque desde cero
docker compose down -v
docker compose up -d

# Monitorizar los logs durante el arranque
docker compose logs -f web db
```

**Qué observar:**
- El contenedor `db` aparece como *healthy* antes de que `web` inicie Flask.
- Tiempo esperado de espera: ~30 s en el primer arranque.
- Sin el healthcheck se producía `Connection refused` en el primer log de `web`.

**Verificar el healthcheck activo:**
```bash
docker inspect asistenciator_db --format '{{.State.Health.Status}}'
# → healthy
```

---

### INT-2 — Cron `scheduler` con `web` caído

**Cómo probarlo:**
```bash
# Parar solo el contenedor web
docker compose stop web

# Esperar que el cron ejecute (o forzarlo manualmente)
docker exec asistenciator_scheduler /scripts/escaneo.sh

# Volver a levantar web
docker compose start web
```

**Qué observar:**
- El script registra el error pero el cron no muere.
- Revisar logs del scheduler:
```bash
docker logs asistenciator_scheduler | tail -5
# → ERROR: /estado-franja devolvió HTTP 000. ¿Está Flask activo?
```

---

### INT-3 — Reconexión del túnel Cloudflare tras pérdida de red

**Cómo probarlo:**
1. Con el túnel activo y la aplicación accesible desde internet, desactiva la red del equipo (WiFi/ethernet) durante ~10 s.
2. Vuelve a activar la red.

**Qué observar:**
- El túnel vuelve a establecerse sin intervención manual (5–10 s).
- La URL pública vuelve a responder.
- Log del contenedor `cloudflared`:
```bash
docker logs asistenciator_cloudflared | tail -10
# → Reconnecting tunnel connection
# → Connection established
```

---

### INT-4 — Contenedor `web` sin adaptador Bluetooth

**Cómo probarlo:**
```bash
# Bloquear el adaptador Bluetooth en el host
sudo rfkill block bluetooth

# Intentar un escaneo
curl -i -X POST http://localhost:5000/escanear \
  -H "X-Scheduler-Key: $SCHEDULER_KEY"
```

**Qué observar:**
- La aplicación devuelve un error coherente (no un 500 sin mensaje).
- La página `/bt/estado` muestra el estado del adaptador.

```bash
# Restaurar el adaptador
sudo rfkill unblock bluetooth
```

---

### INT-5 — Independencia Telegram ↔ correo

**Cómo probarlo:**
1. Con `TELEGRAM_TOKEN` apuntando a un servidor inaccesible (o token inválido) y SMTP correcto, intenta el envío manual de correo al tutor.

**Qué observar:**
- El correo se envía correctamente aunque Telegram falle.
- Y al revés: con SMTP mal configurado, Telegram sigue funcionando.

---

## 8. Pruebas de seguridad

### SEC-1 — Inyección SQL

**Cómo probarlo:**
```bash
# En el campo email del login:
curl -i -X POST http://localhost:5000/login \
  -d "email=' OR 1=1 --&password=cualquiera&csrf_token=<TOKEN>"
```

**Qué observar:**
- HTTP 200 con mensaje **"Credenciales inválidas"** (no entra, no se interpreta como SQL).
- La aplicación trata el valor como cadena literal.

**En el CRUD de alumnos (vía web):**
- Nombre: `' OR 1=1 --`
- El alumno se guarda con ese nombre exacto (escapado); no altera otras filas.

---

### SEC-2 — XSS

**Cómo probarlo:**
1. Ve a *Alumnos → Añadir alumno*.
2. En el campo *Nombre*, introduce: `<script>alert(1)</script>`
3. Guarda y visualiza la lista de alumnos.

**Qué observar:**
- El texto aparece literalmente como `<script>alert(1)</script>` en la tabla, no se ejecuta.
- Jinja2 escapa el HTML por defecto.
- La CSP del servidor impediría la ejecución incluso si se filtrara.

---

### SEC-3 — CSRF

**Cómo probarlo:**
```bash
# Enviar formulario sin token CSRF:
curl -i -b cookies_admin.txt \
  -X POST http://localhost:5000/alumnos/delete/1
  # Sin campo csrf_token en el body
# → HTTP 400, body contiene "CSRF_FAIL" o similar
```

**Qué observar:**
- HTTP 400. La eliminación no se ejecuta.
- También se puede probar montando una página HTML externa con un `<form>` apuntando al endpoint y enviándola desde un navegador autenticado → mismo resultado: 400.

---

### SEC-4 — Clickjacking

**Cómo probarlo:**
1. Crea un fichero `test_iframe.html` con este contenido:
```html
<iframe src="https://TU_DOMINIO/" width="800" height="600"></iframe>
```
2. Ábrelo en Chrome o Firefox.

**Qué observar:**
- El iframe no carga la aplicación.
- En DevTools → Console: `Refused to display ... in a frame because it set 'X-Frame-Options' to 'SAMEORIGIN'`.

**Verificar la cabecera:**
```bash
curl -I https://TU_DOMINIO/ | grep -i x-frame
# → X-Frame-Options: SAMEORIGIN
```

---

### SEC-5 — Enumeración de usuarios (timing)

**Cómo probarlo:**
```bash
# Email existente, contraseña incorrecta
time curl -s -X POST http://localhost:5000/login \
  -d "email=admin@centro.es&password=MALA&csrf_token=x"

# Email inexistente
time curl -s -X POST http://localhost:5000/login \
  -d "email=noexiste@falso.com&password=MALA&csrf_token=x"
```

**Qué observar:**
- En ambos casos el mensaje es genérico: **"Credenciales inválidas"**.
- Existe una asimetría temporal de ~15 ms (el email inexistente no ejecuta bcrypt). Esto está documentado como **mejora futura**: ejecutar bcrypt con un hash dummy para igualar tiempos.

---

### SEC-6 — Cabeceras HTTP de seguridad

**Cómo probarlo:**
```bash
curl -I https://TU_DOMINIO/login
```

**Cabeceras esperadas:**
```
X-Frame-Options: SAMEORIGIN
X-Content-Type-Options: nosniff
Referrer-Policy: strict-origin-when-cross-origin
Content-Security-Policy: default-src 'self'; ...
Strict-Transport-Security: max-age=...  (añadida por Cloudflare)
```

**Verificación externa:**
- Herramienta Mozilla Observatory: `https://observatory.mozilla.org/`
- Apuntar al dominio público. Nota esperada: **B+** (la única penalización es `'unsafe-inline'` en CSP, necesario para los estilos inline actuales — documentado como mejora futura).

---

## Registro de incidencias resueltas durante las pruebas

| ID     | Descripción                                                                 | Estado    |
|--------|-----------------------------------------------------------------------------|-----------|


---

*Última actualización: mayo 2026 — Laura Linares López*