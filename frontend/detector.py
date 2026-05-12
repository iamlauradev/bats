# =============================================================================
# BATS IoT -- Modulo de Deteccion Bluetooth (intercambiable)
# =============================================================================

import subprocess
import time
import sys
import os

# El modulo de deteccion Bluetooth utilizado es metodo_06_hibrido (hibrido
# L2CAP + PyBluez fallback), ubicado en el mismo directorio que este fichero.
_frontend_dir = os.path.dirname(os.path.abspath(__file__))
if _frontend_dir not in sys.path:
    sys.path.insert(0, _frontend_dir)

import metodo_06_hibrido as _METODO

# =============================================================================
# INTERFAZ PUBLICA -- lo que app.py llama
# =============================================================================

def encender_bt(reintentos=3):
    """
    Activa el adaptador Bluetooth de la Raspberry Pi.
    Desbloquea el hardware de radio y levanta la interfaz hci0.

    Args:
        reintentos: Numero maximo de intentos (por defecto 3).

    Returns:
        True si el adaptador quedo activo, False si fallo tras todos los intentos.
    """
    subprocess.run(['rfkill', 'unblock', 'bluetooth'], capture_output=True)
    time.sleep(0.5)

    for intento in range(1, reintentos + 1):
        subprocess.run(['hciconfig', 'hci0', 'up'], capture_output=True)
        time.sleep(1)

        resultado = subprocess.run(
            ['hciconfig', 'hci0'],
            capture_output=True, text=True
        )
        if 'UP' in resultado.stdout or 'RUNNING' in resultado.stdout:
            if intento > 1:
                print(f"[BT] Adaptador hci0 activo (intento {intento})", flush=True)
            return True

        print(f"[BT] hci0 no responde (intento {intento}/{reintentos}), reintentando...", flush=True)
        time.sleep(2)

    print("[BT] ERROR: No se pudo activar hci0 tras todos los intentos.", flush=True)
    return False


def escanear_alumnos(alumnos):
    """
    Escanea la lista de alumnos usando el metodo de deteccion configurado.

    Adapta automaticamente el flujo segun las capacidades del metodo:
      - Si el metodo tiene 'preparar_escaneo' (metodos BLE), lo llama primero.
      - Si tiene 'mantenimiento_entre_lotes' (metodo 1), lo llama cada 10 alumnos.

    Args:
        alumnos (list): Lista de tuplas (id, nombre, mac).

    Returns:
        list[tuple]: Lista de (id_alumno, nombre, esta_presente: bool).
    """
    tiene_prep  = hasattr(_METODO, 'preparar_escaneo')
    tiene_mant  = hasattr(_METODO, 'mantenimiento_entre_lotes')
    pausa_entre = getattr(_METODO, 'PAUSA_ENTRE_ALUMNOS_SEGUNDOS', 0)

    print(f"\n[Detector] Metodo activo: {nombre_metodo_activo()}", flush=True)
    print(f"[Detector] Escaneando {len(alumnos)} alumnos...", flush=True)

    if tiene_prep:
        _METODO.preparar_escaneo()

    resultados = []

    for i, alumno in enumerate(alumnos):
        id_alumno, nombre, mac = alumno

        if tiene_mant:
            _METODO.mantenimiento_entre_lotes(i)

        detectado, razon = _METODO.detectar(mac)
        estado = 'PRESENTE' if detectado else 'AUSENTE'
        icono  = 'OK' if detectado else '--'
        print(f"  [{icono}] {nombre}: {estado} -- {razon}", flush=True)

        resultados.append((id_alumno, nombre, detectado))

        if pausa_entre > 0:
            time.sleep(pausa_entre)

    presentes = sum(1 for _, _, p in resultados if p)
    print(f"[Detector] Completado: {presentes}/{len(alumnos)} presentes\n", flush=True)

    return resultados


def nombre_metodo_activo():
    """Devuelve el nombre del metodo de deteccion actualmente activo."""
    return getattr(_METODO, 'NOMBRE_METODO', 'Metodo desconocido')
