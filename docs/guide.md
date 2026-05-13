# Guía de Despliegue y Mantenimiento — BATS IoT

**Versión:** 1.0  
**Autora:** Laura Linares — iamlaura.dev

---

## Índice

1. [Requisitos del sistema](#1-requisitos-del-sistema)
2. [Instalación de Docker en Raspberry Pi](#2-instalación-de-docker-en-raspberry-pi)
3. [Configuración del entorno](#3-configuración-del-entorno)
4. [Cloudflare Tunnel (acceso remoto HTTPS)](#4-cloudflare-tunnel)
5. [Primer arranque](#5-primer-arranque)
6. [Configuración inicial del sistema](#6-configuración-inicial-del-sistema)
7. [Notificaciones Telegram](#7-notificaciones-telegram)
8. [Notificaciones por correo](#8-notificaciones-por-correo)
9. [Operación diaria](#9-operación-diaria)
10. [Backups y restauración](#10-backups-y-restauración)
11. [Actualización del sistema](#11-actualización-del-sistema)
12. [Resolución de problemas](#12-resolución-de-problemas)

---

## 1. Requisitos del sistema

### Hardware

- Raspberry Pi 4 Model B (2 GB RAM mínimo, 4 GB recomendado)
- Tarjeta microSD de 16 GB o más (Class 10 / A1)
- Adaptador Bluetooth USB (o el integrado de la RPi 4)
- Conexión a internet (ethernet recomendado para estabilidad)

### Software

- Raspberry Pi OS Lite 64-bit (Bookworm o posterior)
- Docker Engine 24+ y Docker Compose v2
- Git

---

## 2. Instalación de Docker en Raspberry Pi

```bash
# Instalar Docker con el script oficial
curl -fsSL https://get.docker.com | sh

# Añadir el usuario pi al grupo docker (para no requerir sudo)
sudo usermod -aG docker $USER

# Cerrar sesión y volver a entrar, luego verificar
docker --version
docker compose version
```

---

## 3. Configuración del entorno

```bash
# Clonar el repositorio
git clone https://github.com/tu-usuario/bats.git
cd bats

# Crear el fichero .env a partir de la plantilla
cp .env.example .env
nano .env
```

Rellenar **todas** las variables obligatorias. Como mínimo:

```env
MYSQL_ROOT_PASSWORD=<contraseña_segura>
MYSQL_USER=bats_app
MYSQL_PASSWORD=<contraseña_segura>
FLASK_SECRET_KEY=<cadena_aleatoria_32_chars>
SCHEDULER_KEY=<cadena_aleatoria_32_chars>
TZ=Europe/Madrid
NOMBRE_CENTRO=Mi Centro Educativo
```

Para generar claves seguras:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

---

## 4. Cloudflare Tunnel

Cloudflare Tunnel permite acceder al panel web desde cualquier dispositivo con HTTPS real, sin abrir puertos en el router.

### Crear el túnel

1. Ir a [dash.cloudflare.com](https://dash.cloudflare.com) → **Networks → Tunnels → Create a tunnel**
2. Elegir **Cloudflared** como tipo de conector
3. Nombrar el túnel (ej: `bats`)
4. Copiar el token que aparece en el paso de instalación (empieza por `eyJ...`)
5. Añadirlo al `.env`:

```env
CLOUDFLARE_TUNNEL_TOKEN=eyJ...
```

6. En la configuración del túnel (pestaña **Public Hostname**), añadir:
   - Subdominio: el que quieras (ej: `asistencia.tu-dominio.com`)
   - Tipo: **HTTP**
   - URL: `localhost:5000`

---

## 5. Primer arranque

```bash
# Arrancar todos los servicios en segundo plano
docker compose up -d

# Ver el estado de los contenedores
docker compose ps

# Seguir los logs en tiempo real
docker compose logs -f web
```

El arranque completo tarda aproximadamente 30-60 segundos. La base de datos se inicializa automáticamente con el esquema de `db/scripts/init.sql`.

### Verificar que el sistema está operativo

```bash
# Comprobar que Flask responde
curl -I http://localhost:5000

# Comprobar que el adaptador Bluetooth está activo
docker compose exec web hciconfig hci0
```

---

## 6. Configuración inicial del sistema

### Primera vez: crear el administrador

Si la base de datos no tiene ningún usuario, el sistema redirige automáticamente a `/setup` para crear el primer administrador. Rellena el formulario y guarda.

### Configurar el sistema desde el panel

Accede a **Administración → Configuración** y ajusta:

- **Nombre del centro** — aparece en correos y cabeceras
- **Horario de escaneo** — horas entre las que el scheduler actúa
- **Frecuencia de escaneo** — cada cuántos minutos (recomendado: 10)
- **Recreo de mañana** — franja sin escaneo (ej: 11:00 – 11:20)
- **Recreo de tarde** — franja sin escaneo si hay turno de tarde

### Añadir alumnos

1. Ir a **Sistema → Alumnos → Añadir alumno**
2. Rellenar nombre, apellidos, grupo y turno
3. La MAC Bluetooth se puede asignar después usando el botón **Detectar dispositivos** (requiere que el móvil del alumno tenga el Bluetooth visible)

### Añadir asignaturas y horarios

1. **Sistema → Asignaturas** → crear las asignaturas con sus profesores
2. **Sistema → Horarios** → crear las franjas (día, hora inicio/fin, aula)

---

## 7. Notificaciones Telegram

### Crear el bot

1. Hablar con [@BotFather](https://t.me/BotFather) en Telegram
2. Usar `/newbot` y seguir las instrucciones
3. Copiar el **token** del bot

### Obtener el Chat ID

1. Añadir el bot al grupo/chat destino
2. Enviar cualquier mensaje al chat
3. Abrir en el navegador:
   ```
   https://api.telegram.org/bot<TOKEN>/getUpdates
   ```
4. Buscar el campo `"id"` dentro de `"chat"` — ese es el `CHAT_ID`

### Configurar en .env

```env
TELEGRAM_TOKEN=1234567890:ABCdef...
TELEGRAM_CHAT_ID=-100123456789
```

El sistema enviará:
- **Alerta individual** cuando detecte una ausencia (con nombre, grupo, asignatura y horario)
- **Resumen de turno** automáticamente al finalizar el turno de mañana y/o tarde

---

## 8. Notificaciones por correo

El sistema usa Gmail con **contraseña de aplicación** (no la contraseña de la cuenta normal).

### Obtener contraseña de aplicación Gmail

1. Activar la verificación en dos pasos en la cuenta Google
2. Ir a **Cuenta Google → Seguridad → Contraseñas de aplicación**
3. Crear una contraseña para "Correo" en "Otro dispositivo"
4. Copiar la contraseña de 16 caracteres

### Configurar en .env

```env
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USER=tu_cuenta@gmail.com
EMAIL_PASSWORD=abcd efgh ijkl mnop
EMAIL_FROM=BATS <tu_cuenta@gmail.com>
```

El correo al tutor legal se envía desde **Sistema → Alumnos** → botón de sobre, o automáticamente si está activado.

---

## 9. Operación diaria

El sistema funciona de manera completamente autónoma una vez configurado.

### Lo que ocurre automáticamente

| Cuándo | Qué pasa |
|---|---|
| L–V dentro del horario configurado | El scheduler escanea cada N minutos y registra presencia/ausencia |
| Al detectar una ausencia | Se envía alerta Telegram (y correo si está configurado) |
| Al final del turno de mañana | Telegram recibe resumen de ausentes del turno |
| Al final del turno de tarde | Telegram recibe resumen de ausentes del turno |
| Viernes 23:59 | Se genera el informe HTML de la semana, se limpian los registros individuales |
| Sábados y domingos | El sistema no escanea (modo reposo automático) |

### Pausar el escaneo manualmente

Desde el dashboard → botón **Pausar escaneo**. Los escaneos del scheduler seguirán llegando pero serán rechazados hasta que se reactive.

### Forzar un informe manual

**Archivos → Informes → Generar informe ahora** (solo admin).

---

## 10. Backups y restauración

### Hacer un backup

```bash
# Backup con timestamp automático
./scripts/backup.sh
```

El dump queda en `./backups/` comprimido con gzip.

### Programar backups automáticos (opcional)

Añadir al crontab del host:

```bash
# Backup diario a las 2:00 de la madrugada
0 2 * * * /ruta/al/proyecto/scripts/backup.sh >> /var/log/bats/backup.log 2>&1
```

### Restaurar desde un backup

```bash
./scripts/restaurar.sh ./backups/bats_2025-11-15_02-00.sql.gz
```

Ver el plan completo de recuperación ante desastres en [`DRP.md`](DRP.md).

---

## 11. Actualización del sistema

```bash
# Descargar cambios
git pull

# Reconstruir las imágenes e reiniciar
docker compose down
docker compose build --no-cache
docker compose up -d

# Si hay migraciones de base de datos
docker compose exec db mariadb -u root -p${MYSQL_ROOT_PASSWORD} control_asistencia < db/scripts/migrate_vX_Y.sql
```

> **Nota:** Los datos de la base de datos persisten en `db/mariadb_data/` y no se borran al hacer `docker compose down`.

---

## 12. Resolución de problemas

### El adaptador Bluetooth no responde

```bash
# Ver estado del adaptador en el contenedor web
docker compose exec web hciconfig

# Intentar activarlo manualmente
docker compose exec web hciconfig hci0 up

# Ver logs de detección
docker compose logs web | grep -i "BT\|Bluetooth\|hci"
```

Si el adaptador no aparece, asegurarse de que el contenedor tiene modo privilegiado y acceso a `/dev/`:

```yaml
# en docker-compose.yml, servicio web:
privileged: true
volumes:
  - /var/run/dbus:/var/run/dbus
```

### El scheduler no escanea

```bash
# Ver logs del scheduler
docker compose logs scheduler --tail 50

# Comprobar que el cron está corriendo dentro del contenedor
docker compose exec scheduler crontab -l
```

Verificar que `SCHEDULER_KEY` en `.env` coincide con el que usa Flask.

### No llegan alertas de Telegram

```bash
# Probar el bot manualmente
curl -s "https://api.telegram.org/bot${TELEGRAM_TOKEN}/sendMessage" \
  -d "chat_id=${TELEGRAM_CHAT_ID}&text=Test+desde+RPi"
```

Si devuelve error 401: el token es incorrecto.  
Si devuelve error 400 con "chat not found": el `CHAT_ID` es incorrecto.

### Los logs del sistema

```bash
# Logs de Flask
docker compose logs web

# Logs del scheduler
docker compose logs scheduler

# Ficheros de log en el host
tail -f ./logs/app.log
tail -f ./logs/cron.log
tail -f ./logs/informe.log
```

---

*Para más información sobre el modelo de datos, consultar [`db_diagram.mmd`](db_diagram.mmd).*  
*Para el plan de recuperación ante desastres, consultar [`DRP.md`](DRP.md).*
