# =============================================================================
# BATS IoT — Módulo de correo a tutores legales
# =============================================================================
# Envía correos automáticos a los tutores legales de alumnos ausentes.
# Usa smtplib nativo de Python con Gmail + contraseña de aplicación.
#
# Para obtener una contraseña de aplicación Gmail:
#   1. Activa la verificación en dos pasos en tu cuenta Google.
#   2. Ve a: Cuenta Google → Seguridad → Contraseñas de aplicación.
#   3. Crea una contraseña para "Correo" en "Otro dispositivo".
#   4. Añade esa contraseña de 16 caracteres al .env como EMAIL_PASSWORD.
#
# Autora: Laura Linares — iamlaura.dev
# =============================================================================

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime

DIAS_ES = ['lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo']


def _get_config() -> dict | None:
    """
    Lee la configuración SMTP desde variables de entorno.
    Devuelve None si no está configurada, sin lanzar excepción.
    """
    host     = os.environ.get('EMAIL_HOST',     'smtp.gmail.com').strip()
    port     = int(os.environ.get('EMAIL_PORT', '587'))
    user     = os.environ.get('EMAIL_USER',     '').strip()
    password = os.environ.get('EMAIL_PASSWORD', '').strip()
    remite   = os.environ.get('EMAIL_FROM',     user).strip()

    if not user or not password:
        return None

    return {
        'host':     host,
        'port':     port,
        'user':     user,
        'password': password,
        'remite':   remite,
    }


def _construir_html(nombre_alumno: str, grupo: str,
                    horario_activo: dict | None,
                    nombre_centro: str) -> str:
    """
    Genera el cuerpo HTML del correo de notificación.
    """
    ahora = datetime.now()
    hora  = ahora.strftime('%H:%M')
    dia   = DIAS_ES[ahora.weekday()].capitalize()
    fecha = ahora.strftime('%d/%m/%Y')

    if horario_activo:
        asignatura = horario_activo.get('asignatura', '—')
        aula       = horario_activo.get('aula', '—')
        hora_clase = f"{horario_activo.get('hora_inicio', '')} – {horario_activo.get('hora_fin', '')}"
    else:
        asignatura = '—'
        aula       = '—'
        hora_clase = '—'

    return f"""
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin:0;padding:0;background:#f1f5f9;font-family:'Segoe UI',system-ui,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f1f5f9;padding:32px 16px;">
  <tr>
    <td align="center">
      <table width="100%" style="max-width:520px;background:#ffffff;border-radius:16px;
                                  overflow:hidden;box-shadow:0 4px 20px rgba(0,0,0,0.08);">

        <!-- Cabecera -->
        <tr>
          <td style="background:#12243a;padding:28px 32px;">
            <p style="margin:0;font-size:1rem;font-weight:700;color:#60a5fa;">
              📡 BATS
            </p>
            <p style="margin:4px 0 0;font-size:0.75rem;color:#64748b;">
              {nombre_centro}
            </p>
          </td>
        </tr>

        <!-- Alerta -->
        <tr>
          <td style="background:#fee2e2;padding:16px 32px;">
            <p style="margin:0;font-size:0.95rem;font-weight:700;color:#b91c1c;">
              🚨 Ausencia detectada
            </p>
          </td>
        </tr>

        <!-- Cuerpo -->
        <tr>
          <td style="padding:28px 32px;">
            <p style="margin:0 0 16px;font-size:0.9rem;color:#374151;line-height:1.6;">
              Le informamos de que <strong>{nombre_alumno}</strong>,
              perteneciente al grupo <strong>{grupo}</strong>,
              ha sido registrado/a como <strong>ausente</strong>
              el <strong>{dia} {fecha}</strong> a las <strong>{hora}</strong>.
            </p>

            <!-- Tabla de detalles -->
            <table width="100%" cellpadding="0" cellspacing="0"
                   style="background:#f8fafc;border-radius:10px;overflow:hidden;
                          border:1px solid #e2e8f0;margin-bottom:20px;">
              <tr style="border-bottom:1px solid #e2e8f0;">
                <td style="padding:10px 16px;font-size:0.8rem;font-weight:600;
                           color:#64748b;width:40%;">Asignatura</td>
                <td style="padding:10px 16px;font-size:0.85rem;color:#1e293b;">
                  {asignatura}
                </td>
              </tr>
              <tr style="border-bottom:1px solid #e2e8f0;">
                <td style="padding:10px 16px;font-size:0.8rem;font-weight:600;color:#64748b;">
                  Aula
                </td>
                <td style="padding:10px 16px;font-size:0.85rem;color:#1e293b;">
                  {aula}
                </td>
              </tr>
              <tr style="border-bottom:1px solid #e2e8f0;">
                <td style="padding:10px 16px;font-size:0.8rem;font-weight:600;color:#64748b;">
                  Franja horaria
                </td>
                <td style="padding:10px 16px;font-size:0.85rem;color:#1e293b;">
                  {hora_clase}
                </td>
              </tr>
              <tr>
                <td style="padding:10px 16px;font-size:0.8rem;font-weight:600;color:#64748b;">
                  Hora de detección
                </td>
                <td style="padding:10px 16px;font-size:0.85rem;color:#1e293b;">
                  {hora} · {dia} {fecha}
                </td>
              </tr>
            </table>

            <p style="margin:0;font-size:0.82rem;color:#94a3b8;line-height:1.5;">
              Este mensaje ha sido generado automáticamente por el sistema
              de control de asistencia BATS. Si tiene alguna duda,
              contacte con el tutor/a del grupo.
            </p>
          </td>
        </tr>

        <!-- Pie -->
        <tr>
          <td style="background:#f8fafc;padding:16px 32px;border-top:1px solid #e2e8f0;">
            <p style="margin:0;font-size:0.72rem;color:#94a3b8;">
              {nombre_centro} · Sistema IoT Bluetooth · BATS
            </p>
          </td>
        </tr>

      </table>
    </td>
  </tr>
</table>
</body>
</html>
"""


def enviar_correo_tutor(nombre_alumno: str, grupo: str,
                        email_tutor: str,
                        horario_activo: dict | None) -> bool:
    """
    Envía un correo de notificación de ausencia al tutor legal del alumno.

    Args:
        nombre_alumno:  Nombre completo del alumno ausente.
        grupo:          Grupo del alumno.
        email_tutor:    Dirección de correo del tutor legal.
        horario_activo: Dict con datos del horario o None.

    Returns:
        True si el correo se envió correctamente, False en caso contrario.
    """
    config = _get_config()
    if not config:
        print("[Correo] No configurado (EMAIL_USER o EMAIL_PASSWORD vacíos). Saltando.")
        return False

    # Preferir el valor guardado en la BD; caer en env var si la BD no lo tiene
    try:
        import pymysql, pymysql.cursors
        _nc = pymysql.connect(
            host=os.environ.get('DB_HOST', '127.0.0.1'),
            user=os.environ.get('DB_USER'), password=os.environ.get('DB_PASSWORD'),
            database=os.environ.get('DB_NAME', 'control_asistencia'),
            cursorclass=pymysql.cursors.DictCursor, connect_timeout=3
        )
        _nc_cur = _nc.cursor()
        _nc_cur.execute("SELECT valor FROM configuracion WHERE clave='nombre_centro' LIMIT 1")
        _nc_row = _nc_cur.fetchone()
        _nc.close()
        nombre_centro = (_nc_row['valor'] if _nc_row else None) \
                        or os.environ.get('NOMBRE_CENTRO', 'Centro educativo')
    except Exception:
        nombre_centro = os.environ.get('NOMBRE_CENTRO', 'Centro educativo')

    ahora         = datetime.now()
    dia           = DIAS_ES[ahora.weekday()].capitalize()
    fecha         = ahora.strftime('%d/%m/%Y')

    asignatura = horario_activo.get('asignatura', '') if horario_activo else ''
    asunto_asig = f" en {asignatura}" if asignatura and asignatura != '—' else ''

    asunto = f"Ausencia de {nombre_alumno}{asunto_asig} — {dia} {fecha}"

    msg = MIMEMultipart('alternative')
    msg['Subject'] = asunto
    msg['From']    = f"BATS <{config['remite']}>"
    msg['To']      = email_tutor

    html = _construir_html(nombre_alumno, grupo, horario_activo, nombre_centro)
    msg.attach(MIMEText(html, 'html', 'utf-8'))

    try:
        with smtplib.SMTP(config['host'], config['port'], timeout=15) as servidor:
            servidor.ehlo()
            servidor.starttls()
            servidor.login(config['user'], config['password'])
            servidor.sendmail(config['remite'], [email_tutor], msg.as_string())

        print(f"[Correo] ✅ Enviado a {email_tutor} ({nombre_alumno})")
        return True

    except smtplib.SMTPAuthenticationError:
        print("[Correo] ❌ Error de autenticación. Comprueba EMAIL_USER y EMAIL_PASSWORD.")
        return False
    except smtplib.SMTPException as e:
        print(f"[Correo] ❌ Error SMTP: {e}")
        return False
    except Exception as e:
        print(f"[Correo] ❌ Error inesperado: {e}")
        return False
