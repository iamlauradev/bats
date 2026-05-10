#!/bin/sh
# =============================================================================
# ASISTENCIATOR IoT — Script de generación de informe semanal v1.0
# =============================================================================
# Ejecutado por crond cada viernes a las 23:59.
# Llama al endpoint /informes/generar-cron del servidor Flask, autenticado
# con la misma SCHEDULER_KEY que usa el escaneo automático.
# Genera el informe de la semana EN CURSO (lunes–viernes actuales) y
# elimina los registros individuales de asistencia de esa semana.
# =============================================================================

FLASK_URL="http://127.0.0.1:5000"
LOG_TAG="[asistenciator-informe]"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

echo "$TIMESTAMP $LOG_TAG Iniciando generación de informe semanal (viernes 23:59)..."

HTTP_CODE=$(curl -s -o /tmp/informe_resp.json \
    -w "%{http_code}" \
    -X POST \
    -H "X-Scheduler-Key: ${SCHEDULER_KEY}" \
    --max-time 90 \
    "${FLASK_URL}/informes/generar-cron")

case "$HTTP_CODE" in
    200)
        echo "$TIMESTAMP $LOG_TAG ✅ Informe generado correctamente."
        cat /tmp/informe_resp.json
        echo ""
        ;;
    409)
        echo "$TIMESTAMP $LOG_TAG ⚠️  Ya existe un informe para esta semana."
        ;;
    000)
        echo "$TIMESTAMP $LOG_TAG ❌ ERROR: No se pudo conectar con Flask."
        ;;
    *)
        echo "$TIMESTAMP $LOG_TAG ❌ Respuesta inesperada HTTP $HTTP_CODE:"
        cat /tmp/informe_resp.json
        echo ""
        ;;
esac
