# =============================================================================
# POC — MÉTODO 6: Híbrido L2CAP + PyBluez fallback
# =============================================================================
# Orden:
#   1) Intento rápido con L2CAP (método 3).
#   2) Si falla, intento profundo con PyBluez lookup_name (método 2).
#
# Objetivo:
#   Maximizar la detección de dispositivos presentes sin disparar el tiempo total.
# =============================================================================

import os
import socket
import time
import subprocess

import bluetooth

NOMBRE_METODO = "Método 6 — Híbrido L2CAP + PyBluez fallback"
DESCRIPCION = (
    "Primero L2CAP socket ping (rápido). Si falla, fallback con PyBluez "
    "lookup_name para recuperar dispositivos que no respondan al primer método."
)

# Ajustes razonables para que no se dispare el tiempo total.
TIMEOUT_L2CAP_SEGUNDOS = float(os.getenv("BT06_TIMEOUT_L2CAP", "2.5"))
TIMEOUT_PYBLUEZ_SEGUNDOS = float(os.getenv("BT06_TIMEOUT_PYBLUEZ", "4.0"))

# Pequeña pausa entre alumnos. El propio segundo intento ya consume tiempo.
PAUSA_ENTRE_ALUMNOS_SEGUNDOS = 0.15

# Mantenimiento preventivo suave para evitar que la pila BT se quede tonta
# en corridas largas.
_FALLOS_CONSECUTIVOS = 0
_REINICIO_CADA_FALLOS = 10


def _reset_bt() -> None:
    """Reinicia suavemente la interfaz Bluetooth."""
    comandos = [
        ["rfkill", "unblock", "bluetooth"],
        ["hciconfig", "hci0", "reset"],
        ["hciconfig", "hci0", "up"],
    ]
    for cmd in comandos:
        try:
            subprocess.run(cmd, capture_output=True, check=False)
        except FileNotFoundError:
            pass
    time.sleep(0.8)


def _ping_l2cap(mac: str) -> tuple[bool, str]:
    """Paso 1: método rápido con socket L2CAP."""
    sock = None
    try:
        sock = socket.socket(
            socket.AF_BLUETOOTH,
            socket.SOCK_SEQPACKET,
            socket.BTPROTO_L2CAP,
        )
        sock.settimeout(TIMEOUT_L2CAP_SEGUNDOS)
        sock.connect((mac, 0x0001))
        return True, "L2CAP conectado (puerto SDP 0x0001)"

    except ConnectionRefusedError:
        return False, "Puerto SDP bloqueado"

    except TimeoutError:
        return False, f"Timeout L2CAP ({TIMEOUT_L2CAP_SEGUNDOS}s)"

    except OSError as error:
        return False, f"Error socket L2CAP: {error}"

    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass


def _lookup_name_pybluez(mac: str) -> tuple[bool, str]:
    """Paso 2: fallback profundo con PyBluez."""
    try:
        nombre = bluetooth.lookup_name(mac, timeout=TIMEOUT_PYBLUEZ_SEGUNDOS)
        if nombre:
            return True, f"PyBluez lookup_name: '{nombre}'"
        return False, f"Sin respuesta PyBluez ({TIMEOUT_PYBLUEZ_SEGUNDOS}s)"
    except Exception as error:
        return False, f"Error PyBluez: {error}"


def detectar(mac: str) -> tuple[bool, str]:
    """
    Híbrido 2-3:
      - Primero L2CAP.
      - Si falla, lookup_name.
      - Si ambos fallan, se considera ausente.
    """
    global _FALLOS_CONSECUTIVOS

    mac = mac.upper()

    # Fase 1: L2CAP
    detectado, razon = _ping_l2cap(mac)
    if detectado:
        _FALLOS_CONSECUTIVOS = 0
        return True, f"Fase 1 OK — {razon}"

    _FALLOS_CONSECUTIVOS += 1

    # Mantenimiento preventivo ocasional si la pila BT se vuelve inestable.
    if _FALLOS_CONSECUTIVOS % _REINICIO_CADA_FALLOS == 0:
        _reset_bt()

    # Pequeña pausa antes del fallback para dejar respirar a la pila BT.
    time.sleep(0.12)

    # Fase 2: PyBluez
    detectado, razon2 = _lookup_name_pybluez(mac)
    if detectado:
        _FALLOS_CONSECUTIVOS = 0
        return True, f"Fase 2 OK — {razon2}"

    return False, f"Sin L2CAP + sin PyBluez ({razon}; {razon2})"
