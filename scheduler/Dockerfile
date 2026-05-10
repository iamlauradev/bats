# =============================================================================
# ASISTENCIATOR IoT — Dockerfile del scheduler
# =============================================================================
FROM alpine:3.19

RUN apk add --no-cache curl tzdata

WORKDIR /scheduler

COPY escaneo.sh /scheduler/escaneo.sh
COPY informe.sh /scheduler/informe.sh
RUN chmod +x /scheduler/escaneo.sh /scheduler/informe.sh

COPY crontab /etc/crontabs/root

RUN mkdir -p /var/log/asistenciator

CMD ["crond", "-f", "-l", "2"]
