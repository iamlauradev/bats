#!/bin/bash
# =============================================================================
# BATS IoT — Instalador del cron de backup
# =============================================================================
# Ejecutar UNA SOLA VEZ en el host (Raspberry Pi) para programar el backup
# automático diario a las 03:00.
#
# Uso:
#   chmod +x scripts/instalar_cron_backup.sh
#   sudo ./scripts/instalar_cron_backup.sh
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKUP_SCRIPT="${SCRIPT_DIR}/backup.sh"
LOG_FILE="/var/log/bats/backup.log"
LOG_DIR="/var/log/bats"
BACKUP_DIR="/backups/bats"

# Comprobar que se ejecuta como root
if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: Ejecuta este script con sudo."
    exit 1
fi

# Dar permisos de ejecución a los scripts
chmod +x "$BACKUP_SCRIPT"
chmod +x "${SCRIPT_DIR}/restaurar.sh"

# Crear directorios necesarios
mkdir -p "$LOG_DIR" "$BACKUP_DIR"

# Añadir la línea al crontab de root (evitar duplicados)
CRON_LINE="0 3 * * * ${BACKUP_SCRIPT} >> ${LOG_FILE} 2>&1"

if crontab -l 2>/dev/null | grep -qF "$BACKUP_SCRIPT"; then
    echo "ℹ️  El cron de backup ya estaba configurado. No se ha modificado."
else
    (crontab -l 2>/dev/null; echo "$CRON_LINE") | crontab -
    echo "✅ Cron de backup instalado: todos los días a las 03:00"
    echo "   Script : ${BACKUP_SCRIPT}"
    echo "   Log    : ${LOG_FILE}"
    echo "   Destino: ${BACKUP_DIR}"
fi

echo ""
echo "Para verificar que el cron está activo:"
echo "  sudo crontab -l"
echo ""
echo "Para ejecutar un backup manual ahora:"
echo "  ${BACKUP_SCRIPT}"
