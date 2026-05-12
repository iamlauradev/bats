# =============================================================================
# BATS IoT -- Modulo de notificaciones Telegram v1.0
# =============================================================================
# Envia alertas al chat de Telegram configurado.
# Usa la API HTTP de Telegram directamente (sin librerias extra).
#
# Funciones:
#   enviar_ausencia()       -> alerta individual cuando se detecta una ausencia
#   enviar_resumen_turno()  -> resumen al finalizar cada turno
#   enviar_mensaje_libre()  -> mensaje de texto libre
# =============================================================================

import os
import requests
from datetime import datetime

DIAS_ES = ['lunes', 'martes', 'miercoles', 'jueves', 'viernes', 'sabado', 'domingo']

# Separadores y emojis como variables para compatibilidad con Python 3.10
SEP  = '━' * 20          # ━━━━━━━━━━━━━━━━━━━━
BELL = '\U0001F514'           # 🔔
OK   = '✅'               # ✅
WARN = '⚠️'         # ⚠️
LIST = '\U0001F4CB'           # 📋


def _get_config():
    token   = os.environ.get('TELEGRAM_TOKEN', '').strip()
    chat_id = os.environ.get('TELEGRAM_CHAT_ID', '').strip()
    if not token or not chat_id:
        return None, None
    return token, chat_id


def _enviar(token, chat_id, texto):
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": texto, "parse_mode": "Markdown"},
            timeout=10
        )
        if resp.status_code == 200:
            return True
        print(f"[Telegram] Error {resp.status_code}: {resp.text}")
        return False
    except requests.exceptions.Timeout:
        print("[Telegram] Timeout al enviar mensaje.")
        return False
    except Exception as e:
        print(f"[Telegram] Error inesperado: {e}")
        return False


def enviar_ausencia(nombre_alumno, grupo, horario_activo):
    """Envia una alerta individual cuando se detecta una ausencia."""
    token, chat_id = _get_config()
    if not token:
        print("[Telegram] No configurado (TOKEN/CHAT_ID vacios). Saltando.")
        return False

    ahora = datetime.now()
    hora  = ahora.strftime('%H:%M')
    dia   = DIAS_ES[ahora.weekday()]

    if horario_activo:
        asignatura = horario_activo.get('asignatura', '-')
        aula       = horario_activo.get('aula', '-')
        h_ini      = horario_activo.get('hora_inicio', '')
        h_fin      = horario_activo.get('hora_fin', '')
        clase_str  = f"{h_ini}–{h_fin}" if h_ini else '-'
    else:
        asignatura = '-'
        aula       = '-'
        clase_str  = '-'

    lineas = [
        f"{BELL} *AUSENCIA DETECTADA*",
        SEP,
        f"*Alumno:*    {nombre_alumno}",
        f"*Grupo:*     {grupo}",
        f"*Clase:*     {asignatura}",
        f"*Aula:*      {aula}",
        f"*Franja:*    {clase_str}",
        SEP,
        f"`{hora} · {dia}`",
    ]
    mensaje = "\n".join(lineas)

    ok = _enviar(token, chat_id, mensaje)
    if ok:
        print(f"[Telegram] Alerta enviada: {nombre_alumno}")
    return ok


def enviar_resumen_turno(turno, ausentes):
    """Envia un resumen al finalizar cada turno (manana o tarde)."""
    token, chat_id = _get_config()
    if not token:
        return False

    ahora     = datetime.now()
    turno_str = turno.capitalize()
    fecha_str = ahora.strftime('%d/%m/%Y · %H:%M')

    if not ausentes:
        lineas = [
            f"{LIST} *Resumen — Turno {turno_str}*",
            SEP,
            f"{OK} Sin ausencias registradas este turno.",
            SEP,
            f"`{fecha_str}`",
        ]
    else:
        total  = len(ausentes)
        plural = 'alumno' if total == 1 else 'alumnos'
        items  = []
        for a in ausentes:
            n      = a.get('clases_ausente', 0)
            plural_c = 'falta' if n == 1 else 'faltas'
            items.append(f"  {WARN} {a['nombre']} ({a['grupo']}) — {n} {plural_c}")

        lineas = (
            [
                f"{LIST} *Resumen — Turno {turno_str}*",
                SEP,
                f"*{total} {plural} con ausencias:*",
            ]
            + items
            + [SEP, f"`{fecha_str}`"]
        )

    mensaje = "\n".join(lineas)

    ok = _enviar(token, chat_id, mensaje)
    if ok:
        print(f"[Telegram] Resumen de turno {turno} enviado ({len(ausentes)} ausencias).")
    return ok


def enviar_mensaje_libre(texto):
    """Envia un mensaje de texto libre al chat configurado."""
    token, chat_id = _get_config()
    if not token:
        return False
    ok = _enviar(token, chat_id, texto)
    if not ok:
        print("[Telegram] Error al enviar mensaje libre.")
    return ok
