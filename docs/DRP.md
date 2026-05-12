# Plan de Recuperación ante Desastres (DRP)
## BATS IoT — Control de Asistencia Bluetooth
**Autora:** Laura Linares — iamlaura.dev  
**Versión:** 1.0

---

## 1. Introducción

Este documento describe el procedimiento completo para restaurar el sistema BATS IoT tras un fallo total de la Raspberry Pi. El objetivo es minimizar el tiempo de inactividad y garantizar que los datos de asistencia no se pierdan.

### Objetivos de recuperación

| Métrica | Valor objetivo |
|---|---|
| **RTO** (Recovery Time Objective) — tiempo máximo de restauración | **< 2 horas** |
| **RPO** (Recovery Point Objective) — máxima pérdida de datos aceptable | **< 24 horas** (último backup) |

El RTO de 2 horas asume que se dispone de otra Raspberry Pi 4 con Raspberry Pi OS ya instalado y conexión a internet. Si hay que instalar el SO desde cero, añadir 30-45 minutos adicionales.

---

## 2. Infraestructura del sistema

```
Raspberry Pi 4 (2 GB RAM, tarjeta SD 32 GB)
├── Raspberry Pi OS Lite (64-bit)
├── Docker Engine + Docker Compose
├── Repositorio Git: Bluetooth-Based Attendance Tracking System/
│   ├── docker-compose.yml
│   ├── .env                    ← SECRETO, NO está en Git
│   └── scripts/
│       ├── backup.sh
│       └── restaurar.sh
├── /backups/bats/     ← Dumps diarios de la BD
└── /var/log/bats/     ← Logs de la aplicación
```

> **Nota crítica:** El fichero `.env` contiene todas las contraseñas y tokens del sistema. **No está en Git** por seguridad. Debe guardarse en un lugar seguro externo a la RPi (gestor de contraseñas del centro, copia cifrada en la nube, etc.). Sin el `.env`, la restauración no es posible.

---

## 3. Tipos de fallo y respuesta

### 3.1 Fallo parcial — solo la aplicación

**Síntoma:** La RPi responde por SSH pero la web no carga.

```bash
# Ver estado de los contenedores
docker compose ps

# Ver logs del contenedor problemático
docker compose logs --tail=50 web
docker compose logs --tail=50 cloudflared

# Reiniciar todos los servicios
docker compose restart

# Si no se recupera, recrear los contenedores
docker compose down && docker compose up -d
```

**Tiempo estimado:** 5-10 minutos.

---

### 3.2 Fallo parcial — solo la base de datos

**Síntoma:** La web carga pero muestra errores de conexión a BD.

```bash
# Comprobar estado del contenedor de MariaDB
docker compose ps db
docker compose logs --tail=30 db

# Reiniciar solo la BD
docker compose restart db

# Si los datos de MariaDB están corruptos, restaurar desde backup
# (ver sección 4)
```

**Tiempo estimado:** 5-15 minutos.

---

### 3.3 Fallo total — RPi inaccesible o tarjeta SD dañada

Este es el escenario principal de este DRP. Seguir el procedimiento completo de la sección 4.

---

## 4. Procedimiento de recuperación completa

### Paso 1 — Preparar la nueva Raspberry Pi `⏱ ~30 min`

**1.1** Descargar Raspberry Pi OS Lite (64-bit) desde [raspberrypi.com/software](https://www.raspberrypi.com/software/).

**1.2** Grabar la imagen en la nueva tarjeta SD con Raspberry Pi Imager. En la configuración avanzada del Imager:
- Establecer nombre de host: `bats`
- Habilitar SSH con clave pública o contraseña
- Configurar Wi-Fi si es necesario

**1.3** Arrancar la RPi, conectar por SSH y actualizar el sistema:

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y git curl
```

**1.4** Instalar Docker Engine:

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# Cerrar sesión y volver a entrar para que el grupo surta efecto
```

---

### Paso 2 — Recuperar el código `⏱ ~5 min`

```bash
# Clonar el repositorio
git clone https://github.com/tu-usuario/bats.git
cd bats
```

Si el repositorio es privado:
```bash
# Configurar credenciales Git o usar token de acceso personal
git clone https://token@github.com/tu-usuario/bats.git
```

---

### Paso 3 — Restaurar la configuración secreta `⏱ ~5 min`

Recuperar el fichero `.env` desde el lugar seguro donde se guardó la copia (gestor de contraseñas, copia cifrada, etc.) y copiarlo a la raíz del proyecto:

```bash
# Opción A: copiar desde otro dispositivo por SCP
scp usuario@otro-equipo:/ruta/.env ~/bats/.env

# Opción B: crearlo manualmente desde .env.example
cp .env.example .env
nano .env   # Rellenar todos los valores
```

Verificar que el fichero tiene los valores correctos:
```bash
grep -E "^(MYSQL_|FLASK_|SCHEDULER_|CLOUDFLARE_)" .env
```

---

### Paso 4 — Recuperar los backups `⏱ ~5-10 min`

Los backups en `/backups/bats/` no están en Git y se pierden con la tarjeta SD. Deben haberse copiado previamente a un almacenamiento externo.

**Si se tenía sincronización externa** (ver sección 5):
```bash
# Recuperar desde almacenamiento externo
sudo mkdir -p /backups/bats
# Copiar el backup más reciente
sudo scp usuario@servidor-externo:/backups/bats/backup_*.sql.gz \
     /backups/bats/
```

**Si no hay backup externo:**  
Los datos de asistencia se pierden. Solo se recupera la aplicación vacía. Continuar desde el paso 5 sin restaurar datos.

---

### Paso 5 — Arrancar la aplicación `⏱ ~10 min`

**5.1** Generar el certificado SSL (solo si no se usa Cloudflare Tunnel):
```bash
chmod +x genera_cert.sh && ./genera_cert.sh
```

**5.2** Construir y arrancar los contenedores:
```bash
docker compose up -d --build
```

**5.3** Verificar que todos los servicios están sanos:
```bash
docker compose ps
# Todos deben estar "Up"

# Verificar que Flask responde
curl -s http://localhost:5000 | grep -o "<title>.*</title>"
# Debe devolver: <title>Iniciar Sesión — BATS</title>
```

---

### Paso 6 — Restaurar la base de datos `⏱ ~5 min`

Si se recuperó un backup en el paso 4:

```bash
# Dar permisos a los scripts
chmod +x scripts/backup.sh scripts/restaurar.sh

# Listar backups disponibles
ls -lh /backups/bats/

# Restaurar el más reciente (ajustar el nombre del fichero)
./scripts/restaurar.sh /backups/bats/backup_YYYYMMDD_HHMMSS.sql.gz
```

El script pedirá confirmación, hará un backup de seguridad previo y restaurará los datos.

---

### Paso 7 — Reinstalar el cron de backup `⏱ ~2 min`

```bash
sudo ./scripts/instalar_cron_backup.sh
sudo crontab -l   # Verificar que aparece la línea del backup
```

---

### Paso 8 — Verificación final `⏱ ~5 min`

```bash
# 1. Acceder a la web (con Cloudflare Tunnel activo)
#    https://bats.tudominio.com

# 2. Iniciar sesión con las credenciales del administrador

# 3. Verificar que los datos están presentes
#    → Ir a Alumnos/as y comprobar que aparecen los alumnos

# 4. Lanzar un escaneo manual para verificar que el Bluetooth funciona

# 5. Verificar los logs
tail -f /var/log/bats/app.log
```

---

## 5. Recomendaciones para reducir el RPO

El sistema actual hace backups diarios a las 03:00, lo que significa que en el peor caso se pueden perder hasta 24 horas de registros de asistencia. Para reducir esta ventana:

### 5.1 Sincronización automática de backups al exterior

Añadir al crontab del host una tarea que copie los backups a un servidor externo o almacenamiento en la nube tras cada backup:

```bash
# Ejemplo con rsync a un servidor SSH externo (añadir al crontab tras la línea del backup)
30 3 * * * rsync -az /backups/bats/ usuario@servidor-externo:/backups/bats/

# Ejemplo con rclone a Google Drive / Nextcloud (requiere configurar rclone previamente)
30 3 * * * rclone sync /backups/bats/ drive:bats-backups/ --log-file=/var/log/bats/rclone.log
```

### 5.2 Backup de la tarjeta SD completa

Para un RTO aún menor, clonar la tarjeta SD mensualmente con otra RPi o con un PC:

```bash
# Desde un PC con la tarjeta SD insertada (reemplazar /dev/sdX por el dispositivo correcto)
sudo dd if=/dev/sdX bs=4M status=progress | gzip > bats_sd_$(date +%Y%m%d).img.gz
```

---

## 6. Medidas de seguridad física recomendadas

> Estas medidas aplican si el sistema se despliega de forma permanente en el centro. No se han aplicado a la RPi prestada durante el desarrollo del proyecto.

| Medida | Descripción | Prioridad |
|---|---|---|
| **Ubicación física** | Instalar la RPi en un armario técnico cerrado con llave, no accesible al alumnado | Alta |
| **Contraseña de bootloader** | Configurar contraseña en `/boot/firmware/config.txt` para evitar arranque desde USB | Alta |
| **Deshabilitar boot USB** | En `raspi-config` → Advanced → Boot Order, establecer solo SD card | Alta |
| **Contraseña de consola** | Asegurarse de que el usuario `pi` (o el usuario por defecto) tiene contraseña fuerte | Alta |
| **Deshabilitar login automático** | En `raspi-config` → System → Boot, seleccionar "Console (requires login)" | Media |
| **SSH con clave pública** | Deshabilitar autenticación por contraseña en SSH, usar solo claves | Media |
| **Actualizaciones automáticas** | Instalar `unattended-upgrades` para parches de seguridad automáticos | Media |
| **Cifrado de tarjeta SD** | LUKS requiere introducir contraseña en cada arranque — no recomendado sin automatización | Baja |

---

## 7. Contactos de emergencia

> Completar con los datos reales del centro antes del despliegue en producción.

| Rol | Nombre | Contacto |
|---|---|---|
| Responsable técnico del sistema | *(completar)* | — |
| Administrador de red del centro | — | — |
| Proveedor de internet del centro | — | — |

---

## 8. Registro de pruebas del DRP

Es recomendable ejecutar el procedimiento de recuperación en un entorno de prueba al menos una vez antes del despliegue en producción, y documentar los resultados aquí.

| Fecha | Tipo de prueba | Resultado | RTO real | Observaciones |
|---|---|---|---|---|
| — | Restauración completa en entorno de prueba | — | — | Pendiente |

---

*Adaptar con los datos reales del centro antes de poner el sistema en producción.*
