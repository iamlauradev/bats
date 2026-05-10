#!/bin/bash
# =============================================================================
# ASISTENCIATOR IoT — Script de restauración de base de datos
# =============================================================================
# Autora  : Laura Linares — iamlaura.dev
# Versión : 1.0
#
# Descripción:
#   Restaura un backup generado por backup.sh en la base de datos MariaDB.
#   ADVERTENCIA: SOBREESCRIBE la base de datos actual completamente.
#
# Uso:
#   ./scripts/restaurar.sh <fichero_backup>
#
# Ejemplos:
#   ./scripts/restaurar.sh /backups/asistenciator/backup_20250428_030001.sql.gz
#   ./scripts/restaurar.sh /backups/asistenciator/backup_20250428_030001.sql.gz --sin-confirmacion
#
# Opciones:
#   --sin-confirmacion   Salta la confirmación interactiva (útil en scripts)
# =============================================================================

set -euo pipefail

# ── Colores para la salida ─────────────────────────────────────────────────────
ROJO='\033[0;31m'
VERDE='\033[0;32m'
AMARILLO='\033[1;33m'
RESET='\033[0m'

info()    { echo -e "${VERDE}[INFO]${RESET}  $*"; }
aviso()   { echo -e "${AMARILLO}[AVISO]${RESET} $*"; }
error()   { echo -e "${ROJO}[ERROR]${RESET} $*" >&2; }
separador() { echo "──────────────────────────────────────────────────────────────"; }

# ── Validar argumentos ─────────────────────────────────────────────────────────
if [ $# -lt 1 ]; then
    error "Uso: $0 <fichero_backup> [--sin-confirmacion]"
    echo ""
    echo "  Ejemplo:"
    echo "    $0 /backups/asistenciator/backup_20250428_030001.sql.gz"
    echo ""
    echo "  Backups disponibles:"
    ls -lh /backups/asistenciator/backup_*.sql.gz 2>/dev/null \
        | awk '{print "    " $NF " (" $5 ")"}' \
        || echo "    (ninguno encontrado en /backups/asistenciator/)"
    exit 1
fi

FICHERO_BACKUP="$1"
SIN_CONFIRMACION="${2:-}"

# ── Validar que el fichero existe y no está vacío ─────────────────────────────
if [ ! -f "$FICHERO_BACKUP" ]; then
    error "El fichero no existe: ${FICHERO_BACKUP}"
    exit 1
fi

if [ ! -s "$FICHERO_BACKUP" ]; then
    error "El fichero está vacío: ${FICHERO_BACKUP}"
    exit 1
fi

# Verificar que es un gzip válido
if ! gzip -t "$FICHERO_BACKUP" 2>/dev/null; then
    error "El fichero no es un gzip válido o está corrupto: ${FICHERO_BACKUP}"
    exit 1
fi

# ── Cargar variables de entorno ────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROYECTO_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="${PROYECTO_DIR}/.env"

if [ ! -f "$ENV_FILE" ]; then
    error "No se encuentra el fichero .env en ${PROYECTO_DIR}"
    exit 1
fi

MYSQL_DATABASE=$(grep '^MYSQL_DATABASE=' "$ENV_FILE" | cut -d'=' -f2 | tr -d '"' | tr -d "'")
MYSQL_ROOT_PASSWORD=$(grep '^MYSQL_ROOT_PASSWORD=' "$ENV_FILE" | cut -d'=' -f2 | tr -d '"' | tr -d "'")
CONTENEDOR_DB="asistenciator_db"

# ── Comprobar que el contenedor está en ejecución ─────────────────────────────
if ! docker inspect "$CONTENEDOR_DB" --format '{{.State.Running}}' 2>/dev/null | grep -q "true"; then
    error "El contenedor ${CONTENEDOR_DB} no está en ejecución."
    echo "  Inicia solo la BD con: docker compose up -d db"
    exit 1
fi

# ── Mostrar resumen y pedir confirmación ──────────────────────────────────────
separador
aviso "  RESTAURACIÓN DE BASE DE DATOS"
separador
echo ""
echo "  Fichero : $(basename "$FICHERO_BACKUP")"
echo "  Tamaño  : $(du -h "$FICHERO_BACKUP" | cut -f1)"
echo "  BD      : ${MYSQL_DATABASE}"
echo "  Fecha   : $(date -r "$FICHERO_BACKUP" '+%Y-%m-%d %H:%M:%S' 2>/dev/null || stat -c '%y' "$FICHERO_BACKUP" 2>/dev/null | cut -d'.' -f1)"
echo ""
aviso "  ⚠️  ESTO SOBREESCRIBIRÁ COMPLETAMENTE LA BASE DE DATOS ACTUAL."
aviso "  Todos los datos existentes serán reemplazados por los del backup."
echo ""

if [ "$SIN_CONFIRMACION" != "--sin-confirmacion" ]; then
    read -rp "  ¿Continuar? Escribe 'SI' para confirmar: " CONFIRMACION
    if [ "$CONFIRMACION" != "SI" ]; then
        info "Restauración cancelada por el usuario."
        exit 0
    fi
fi

separador

# ── Hacer un backup previo de seguridad ───────────────────────────────────────
BACKUP_PREVIO_DIR="/backups/asistenciator"
BACKUP_PREVIO="${BACKUP_PREVIO_DIR}/pre_restauracion_$(date '+%Y%m%d_%H%M%S').sql.gz"
mkdir -p "$BACKUP_PREVIO_DIR"

info "Creando backup de seguridad antes de restaurar..."
docker exec "$CONTENEDOR_DB" \
    mysqldump \
        --user=root \
        --password="${MYSQL_ROOT_PASSWORD}" \
        --single-transaction \
        --add-drop-database \
        --databases "${MYSQL_DATABASE}" \
    | gzip -9 > "$BACKUP_PREVIO" 2>/dev/null || true

if [ -s "$BACKUP_PREVIO" ]; then
    info "Backup de seguridad guardado: $(basename "$BACKUP_PREVIO")"
else
    aviso "No se pudo crear backup previo (la BD podría estar vacía). Continuando..."
    rm -f "$BACKUP_PREVIO"
fi

# ── Restaurar el backup ────────────────────────────────────────────────────────
info "Restaurando backup: $(basename "$FICHERO_BACKUP")..."

zcat "$FICHERO_BACKUP" | docker exec -i "$CONTENEDOR_DB" \
    mysql \
        --user=root \
        --password="${MYSQL_ROOT_PASSWORD}" \
        --force

if [ $? -eq 0 ]; then
    info "✅ Restauración completada correctamente."
    echo ""
    info "Siguiente paso recomendado: reiniciar el servicio web."
    echo "  docker compose restart web"
    echo ""
    if [ -s "$BACKUP_PREVIO" ]; then
        info "Si algo ha ido mal, puedes revertir con:"
        echo "  $0 ${BACKUP_PREVIO} --sin-confirmacion"
    fi
else
    error "❌ Error durante la restauración."
    if [ -s "$BACKUP_PREVIO" ]; then
        error "Puedes revertir al estado anterior con:"
        echo "  $0 ${BACKUP_PREVIO} --sin-confirmacion"
    fi
    exit 1
fi

exit 0
