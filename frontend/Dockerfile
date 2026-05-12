# =============================================================================
# BATS IoT — Dockerfile del servidor Flask
# =============================================================================
FROM python:3.11-slim

WORKDIR /app

# Herramientas Bluetooth del sistema necesarias para hcitool, rfkill y bleak.
# build-essential y libbluetooth-dev son necesarios para compilar dbus-fast
# (dependencia de bleak) desde fuente en ARM.
RUN apt-get update && apt-get install -y --no-install-recommends \
    bluez \
    bluetooth \
    rfkill \
    libbluetooth-dev \
    python3-dev \
    build-essential \
    git \
    dbus

COPY requirements.txt .

# piwheels provee wheels ARM precompiladas para Raspberry Pi.
# Se usa como índice adicional (no exclusivo) para obtener bleak
# compilado correctamente para la arquitectura ARM de la RPi.
RUN pip install --no-cache-dir \
        --extra-index-url https://www.piwheels.org/simple \
        -r requirements.txt

COPY . .

EXPOSE 5000

CMD ["python", "app.py"]
