# =============================================================================
# ASISTENCIATOR IoT — Dockerfile del scheduler
# =============================================================================
FROM alpine:3.19

RUN apk add --no-cache curl tzdata

WORKDIR /scheduler

COPY escaneo.sh /scheduler/escaneo.sh
COPY informe.sh /scheduler/informe.sh
COPY crontab /etc/crontabs/root

# Defensa contra line endings CRLF (Windows): si los archivos entran al
# build con \r\n, el shebang queda como "#!/bin/sh\r" y crond no puede
# ejecutarlos, fallando en silencio. Limpiamos aquí dentro del build para
# que la imagen sea siempre LF, da igual desde qué SO se haga el COPY.
RUN sed -i 's/\r$//' /scheduler/escaneo.sh /scheduler/informe.sh /etc/crontabs/root \
 && chmod +x /scheduler/escaneo.sh /scheduler/informe.sh

RUN mkdir -p /var/log/asistenciator

CMD ["crond", "-f", "-l", "2"]
