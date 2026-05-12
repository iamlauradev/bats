#!/bin/sh
# =============================================================================
# BATS IoT — Script de escaneo automático v1.0
# =============================================================================
# Ejecutado por crond cada minuto (lunes a viernes, 07:00 a 22:59).
#
# Lógica de dos fases:
#   Fase 1 — Consulta /estado-franja: ¿hay clase ahora mismo?
#             Si no hay franja activa (o el sistema está pausado/en recreo),
#             termina sin escanear. No genera ruido en los logs.
#   Fase 2 — Si hay franja activa, lanza /escanear normalmente.
#
# De esta forma el cron puede cubrir todo el horario lectivo posible
# (mañana Y tarde) sin generar escaneos vacíos fuera de clase.
# Cualquier cambio de horario en la web se refleja automáticamente
# sin tocar este script ni el crontab.
# =============================================================================

FLASK_URL="http://127.0.0.1:5000"
LOG_TAG="[bats-cron]"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

# ── Fase 1: consultar si hay franja activa ────────────────────────────────────
ESTADO=$(curl -s -o /tmp/estado_franja.json \
    -w "%{http_code}" \
    -X GET \
    -H "X-Scheduler-Key: ${SCHEDULER_KEY}" \
    --max-time 10 \
    "${FLASK_URL}/estado-franja")

if [ "$ESTADO" != "200" ]; then
    echo "$TIMESTAMP $LOG_TAG ERROR: /estado-franja devolvió HTTP ${ESTADO}. ¿Está Flask activo?"
    exit 1
fi

# Leer el valor de franja_activa del JSON
# Usamos grep/sed en lugar de jq para no requerir dependencias extra en Alpine
FRANJA_ACTIVA=$(grep -o '"franja_activa": *[a-z]*' /tmp/estado_franja.json | grep -o '[a-z]*$')
RAZON=$(grep -o '"razon": *"[^"]*"' /tmp/estado_franja.json | grep -o '"[^"]*"$' | tr -d '"')
ASIGNATURA=$(grep -o '"asignatura": *"[^"]*"' /tmp/estado_franja.json | grep -o '"[^"]*"$' | tr -d '"')

if [ "$FRANJA_ACTIVA" != "true" ]; then
    # Sin franja activa — salir silenciosamente (sin escribir en el log)
    # Solo registrar si hay un motivo relevante (pausado o recreo).
    # Los motivos "fuera_horario", "sin_franja", "frecuencia" y
    # "turnos_desactivados" son normales en un cron que corre cada minuto:
    # se ignoran sin loguear para no llenar el cron.log.
    case "$RAZON" in
        escaneo_pausado)
            echo "$TIMESTAMP $LOG_TAG Escaneo pausado por el usuario, saltando."
            ;;
        recreo)
            RECREO_INI=$(grep -o '"recreo_inicio": *"[^"]*"' /tmp/estado_franja.json | grep -o '"[^"]*"$' | tr -d '"')
            RECREO_FIN=$(grep -o '"recreo_fin": *"[^"]*"' /tmp/estado_franja.json | grep -o '"[^"]*"$' | tr -d '"')
            echo "$TIMESTAMP $LOG_TAG Hora de recreo (${RECREO_INI}–${RECREO_FIN}), saltando."
            ;;
        # fuera_horario, turnos_desactivados, frecuencia, sin_franja:
        # salida silenciosa, no contaminar el log
    esac
    exit 0
fi

# ── Fase 2: hay franja activa → lanzar escaneo ────────────────────────────────
echo "$TIMESTAMP $LOG_TAG Franja activa: '${ASIGNATURA}'. Iniciando escaneo..."

HTTP_CODE=$(curl -s -o /tmp/escaneo_resp.json \
    -w "%{http_code}" \
    -X POST \
    -H "X-Scheduler-Key: ${SCHEDULER_KEY}" \
    --max-time 300 \
    "${FLASK_URL}/escanear")

case "$HTTP_CODE" in
    200)
        PRESENTES=$(grep -o '"presentes": *[0-9]*' /tmp/escaneo_resp.json | grep -o '[0-9]*$')
        AUSENTES=$(grep -o '"ausentes": *[0-9]*' /tmp/escaneo_resp.json | grep -o '[0-9]*$')
        TOTAL=$(grep -o '"total": *[0-9]*' /tmp/escaneo_resp.json | grep -o '[0-9]*$')
        echo "$TIMESTAMP $LOG_TAG Escaneo OK — Presentes: ${PRESENTES}/${TOTAL}, Ausentes: ${AUSENTES}/${TOTAL}"
        ;;
    409)
        echo "$TIMESTAMP $LOG_TAG Ya hay un escaneo en curso, saltando."
        ;;
    423)
        # 423 = pausado, recreo o fuera de horario configurado.
        # El motivo concreto está en la respuesta JSON.
        STATUS_BODY=$(grep -o '"status": *"[^"]*"' /tmp/escaneo_resp.json | grep -o '"[^"]*"$' | tr -d '"')
        echo "$TIMESTAMP $LOG_TAG Flask rechaza el escaneo (${STATUS_BODY:-423}), saltando."
        ;;
    000)
        echo "$TIMESTAMP $LOG_TAG ERROR: No se pudo conectar con Flask."
        ;;
    *)
        echo "$TIMESTAMP $LOG_TAG Respuesta inesperada HTTP ${HTTP_CODE}:"
        cat /tmp/escaneo_resp.json
        echo ""
        ;;
esac
