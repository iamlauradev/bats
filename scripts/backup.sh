#!/bin/bash
# =============================================================================
# BATS IoT — Script de backup de base de datos
# =============================================================================
# Autora  : Laura Linares — iamlaura.dev
# Versión : 1.0
#
# Descripción:
#   Genera un dump comprimido de la base de datos MariaDB usando el contenedor
#   Docker de la aplicación. No requiere mysqldump en el host.
#
# Uso manual:
#   ./scripts/backup.sh
#
# Programación automática (cron del host):
#   Añadir con: sudo crontab -e
#   0 3 * * * /ruta/completa/al/proyecto/scripts/backup.sh >> /var/log/bats/backup.log 2>&1
#
# Retención: conserva los últimos 30 días, borra los más antiguos.
# =============================================================================

set -euo pipefail

# ── Configuración ─────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROYECTO_DIR="$(dirname "$SCRIPT_DIR")"
BACKUP_DIR="/backups/bats"
RETENCION_DIAS=30
CONTENEDOR_DB="bats_db"
TIMESTAMP=$(date '+%Y%m%d_%H%M%S')
FECHA_LEGIBLE=$(date '+%Y-%m-%d %H:%M:%S')
FICHERO_BACKUP="backup_${TIMESTAMP}.sql.gz"
RUTA_BACKUP="${BACKUP_DIR}/${FICHERO_BACKUP}"

# ── Cargar variables de entorno del proyecto ──────────────────────────────────
ENV_FILE="${PROYECTO_DIR}/.env"
if [ ! -f "$ENV_FILE" ]; then
    echo "[${FECHA_LEGIBLE}] ERROR: No se encuentra el fichero .env en ${PROYECTO_DIR}"
    exit 1
fi

# Exportar solo las variables necesarias (sin ejecutar el fichero completo)
MYSQL_DATABASE=$(grep '^MYSQL_DATABASE=' "$ENV_FILE" | cut -d'=' -f2 | tr -d '"' | tr -d "'")
MYSQL_USER=$(grep '^MYSQL_USER=' "$ENV_FILE" | cut -d'=' -f2 | tr -d '"' | tr -d "'")
MYSQL_PASSWORD=$(grep '^MYSQL_PASSWORD=' "$ENV_FILE" | cut -d'=' -f2 | tr -d '"' | tr -d "'")
MYSQL_ROOT_PASSWORD=$(grep '^MYSQL_ROOT_PASSWORD=' "$ENV_FILE" | cut -d'=' -f2 | tr -d '"' | tr -d "'")

if [ -z "$MYSQL_DATABASE" ] || [ -z "$MYSQL_ROOT_PASSWORD" ]; then
    echo "[${FECHA_LEGIBLE}] ERROR: Variables de BD no encontradas en .env"
    exit 1
fi

# ── Crear directorio de backups ────────────────────────────────────────────────
mkdir -p "$BACKUP_DIR"

# ── Comprobar que el contenedor está en ejecución ─────────────────────────────
if ! docker inspect "$CONTENEDOR_DB" --format '{{.State.Running}}' 2>/dev/null | grep -q "true"; then
    echo "[${FECHA_LEGIBLE}] ERROR: El contenedor ${CONTENEDOR_DB} no está en ejecución."
    echo "[${FECHA_LEGIBLE}]        Ejecuta: docker compose up -d db"
    exit 1
fi

# ── Ejecutar mysqldump dentro del contenedor ──────────────────────────────────
echo "[${FECHA_LEGIBLE}] Iniciando backup de '${MYSQL_DATABASE}'..."

docker exec "$CONTENEDOR_DB" \
    mysqldump \
        --user=root \
        --password="${MYSQL_ROOT_PASSWORD}" \
        --single-transaction \
        --routines \
        --triggers \
        --add-drop-database \
        --databases "${MYSQL_DATABASE}" \
    | gzip -9 > "$RUTA_BACKUP"

# Verificar que el fichero se ha creado y no está vacío
if [ ! -s "$RUTA_BACKUP" ]; then
    echo "[${FECHA_LEGIBLE}] ERROR: El backup está vacío o no se ha creado."
    rm -f "$RUTA_BACKUP"
    exit 1
fi

TAMANYO=$(du -h "$RUTA_BACKUP" | cut -f1)
echo "[${FECHA_LEGIBLE}] Backup completado: ${FICHERO_BACKUP} (${TAMANYO})"

# ── Permisos restrictivos sobre el fichero ────────────────────────────────────
chmod 600 "$RUTA_BACKUP"

# ── Retención: borrar backups con más de N días ───────────────────────────────
BORRADOS=$(find "$BACKUP_DIR" -name "backup_*.sql.gz" -mtime +${RETENCION_DIAS} -print -delete | wc -l)
if [ "$BORRADOS" -gt 0 ]; then
    echo "[${FECHA_LEGIBLE}] Limpieza: ${BORRADOS} backup(s) antiguo(s) eliminado(s) (>30 días)"
fi

# ── Resumen final ─────────────────────────────────────────────────────────────
TOTAL_BACKUPS=$(find "$BACKUP_DIR" -name "backup_*.sql.gz" | wc -l)
ESPACIO_TOTAL=$(du -sh "$BACKUP_DIR" 2>/dev/null | cut -f1)
echo "[${FECHA_LEGIBLE}] Estado: ${TOTAL_BACKUPS} backup(s) almacenado(s) — ${ESPACIO_TOTAL} en disco"
echo "[${FECHA_LEGIBLE}] ✅ Backup finalizado correctamente."
exit 0
