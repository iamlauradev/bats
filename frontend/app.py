# =============================================================================
# BATS IoT — Servidor Web Flask v1.0
# =============================================================================
# Autora  : Laura Linares — iamlaura.dev
# =============================================================================

import os
import re
import secrets
import subprocess
import threading
from markupsafe import escape as _esc
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime, date
from functools import wraps

from flask import (
    Flask, render_template, jsonify,
    request, redirect, url_for, flash, Response, abort, session, g
)
from flask_login import (
    LoginManager, UserMixin,
    login_user, logout_user, login_required, current_user
)
from flask_bcrypt import Bcrypt
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import pymysql

from detector import escanear_alumnos, encender_bt, nombre_metodo_activo

from notificaciones.telegram_bot import (
    enviar_ausencia as telegram_ausencia,
    enviar_resumen_turno as telegram_resumen_turno,
)
from notificaciones.correo import enviar_correo_tutor
from generar_informe import generar_informe


# =============================================================================
# INICIALIZACIÓN
# =============================================================================

app = Flask(__name__)

# ProxyFix: confiar en las cabeceras X-Forwarded-* que reenvía cloudflared.
# Sin esto, request.remote_addr siempre sería 127.0.0.1 y el rate limiter
# no distinguiría entre clientes distintos.
# x_for=1 indica que hay exactamente un proxy delante (cloudflared).
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

_secret = os.environ.get('FLASK_SECRET_KEY')
if not _secret:
    raise RuntimeError("ERROR: FLASK_SECRET_KEY no está definida en el entorno.")
app.secret_key = _secret

# ── Configuración segura de cookies de sesión ────────────────────────────────
# Secure: solo se envía la cookie por HTTPS (cloudflared garantiza HTTPS).
#         Desactivar con SESSION_COOKIE_SECURE=false en el .env para acceso
#         directo por IP local (HTTP) cuando el túnel Cloudflare esté caído.
# HttpOnly: JavaScript no puede leer la cookie (mitiga XSS)
# SameSite=Lax: protección adicional contra CSRF cross-site
# PERMANENT_SESSION_LIFETIME: la sesión expira tras 2 h de inactividad
from datetime import timedelta
_secure_cookie = os.environ.get('SESSION_COOKIE_SECURE', 'false').lower() not in ('false', '0', '')
app.config.update(
    SESSION_COOKIE_SECURE   = _secure_cookie,
    SESSION_COOKIE_HTTPONLY = True,
    SESSION_COOKIE_SAMESITE = 'Lax',
    PERMANENT_SESSION_LIFETIME = timedelta(hours=2),
)

bcrypt        = Bcrypt(app)
login_manager = LoginManager(app)

login_manager.login_view             = 'login'
login_manager.login_message          = None   # Sin flash automático al redirigir al login

_lock_escaneo      = threading.Lock()
_discover_sessions: dict = {}   # scan_id -> {'dispositivos': {}, 'running': bool}

# Clave opcional para que el scheduler pueda llamar a /escanear sin sesión
SCHEDULER_KEY = os.environ.get('SCHEDULER_KEY', '')

# Tiempo máximo de inactividad antes de cerrar sesión (2 horas)
SESSION_TIMEOUT = timedelta(hours=2)

# =============================================================================
# LOGGING CENTRALIZADO
# =============================================================================
def _configurar_logging():
    log_dir  = '/var/log/bats'
    log_file = os.path.join(log_dir, 'app.log')
    try:
        os.makedirs(log_dir, exist_ok=True)
        handler = RotatingFileHandler(
            log_file,
            maxBytes=5 * 1024 * 1024,  # 5 MB por fichero
            backupCount=5,
            encoding='utf-8',
        )
    except PermissionError:
        # Fallback a stderr si no se puede escribir en el directorio
        handler = logging.StreamHandler()
        print(f"[WARNING] No se puede escribir en {log_file}, usando stderr para logs.")

    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter(
        '[%(asctime)s] %(levelname)s %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    ))

    logger = logging.getLogger('bats')
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        logger.addHandler(handler)
    return logger

log = _configurar_logging()

# Log de arranque con la configuración crítica de cookies. Si
# secure_cookie=True y accedes por HTTP, el navegador NO mandará la
# cookie de sesión de vuelta — síntoma: login no entra, vuelve a /login
# o 403 con current_user vacío. Solución: SESSION_COOKIE_SECURE=false
# en .env (recomendado en este proyecto, porque Cloudflare ya fuerza
# HTTPS por arriba).
log.info(
    f"BOOT_CONFIG SESSION_COOKIE_SECURE={_secure_cookie} "
    f"(env var SESSION_COOKIE_SECURE="
    f"{os.environ.get('SESSION_COOKIE_SECURE', '<no definida>')!r})"
)
if _secure_cookie:
    log.warning(
        "BOOT_CONFIG SESSION_COOKIE_SECURE está activo. El login NO "
        "funcionará por HTTP plano (p. ej. http://192.168.x.x:5000). "
        "Pon SESSION_COOKIE_SECURE=false en .env si necesitas acceso "
        "local sin Cloudflare."
    )

# =============================================================================
# RATE LIMITER (Flask-Limiter)
# =============================================================================
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=[],          # Sin límite global; solo en rutas específicas
    storage_uri="memory://",    # En memoria — suficiente para una RPi en LAN
)

# =============================================================================
# CSRF — Token por sesión en meta tag
# =============================================================================
# Estrategia ligera: un token aleatorio por sesión Flask se inyecta en
# todas las páginas vía <meta name="csrf-token"> en base.html.
# Cada POST de formulario HTML lleva el token en un campo oculto.
# Las peticiones JSON del scheduler se eximen por SCHEDULER_KEY.
# =============================================================================

def _generar_csrf():
    """Devuelve el token CSRF de la sesión, creándolo si no existe."""
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_hex(32)
    return session['csrf_token']


def _verificar_csrf():
    """
    Comprueba el token CSRF en peticiones POST de formularios HTML.
    Exime:
      - Peticiones JSON (Content-Type: application/json)
      - Llamadas del scheduler autenticadas con X-Scheduler-Key

    Devuelve:
      - None si todo OK (el request continúa al endpoint normal).
      - Un Response (redirect) si el token falla en /login o /setup —
        recuperación elegante para que el usuario no quede atrapado en
        un 403 si la cookie de sesión se ha perdido (navegador que la
        bloquea, SESSION_COOKIE_SECURE=true sobre HTTP, primera visita
        sin sesión previa, etc.). En el resto de endpoints se aborta
        con 403 como siempre.
    """
    if request.method != 'POST':
        return None
    # Peticiones JSON propias del frontend (fetch API)
    if request.is_json:
        return None
    # Scheduler interno
    if _es_llamada_scheduler():
        return None
    token_form    = request.form.get('csrf_token', '')
    token_sesion  = session.get('csrf_token', '')
    if not token_form or not secrets.compare_digest(token_form, token_sesion):
        # Log detallado para diagnóstico: a menudo el síntoma es "no puedo
        # entrar y caigo en 403". Suele ser cookie de sesión perdida.
        log.warning(
            f"CSRF inválido desde {request.remote_addr} → {request.path} "
            f"endpoint={request.endpoint} "
            f"form_token={'sí' if token_form else 'NO'} "
            f"session_token={'sí' if token_sesion else 'NO'} "
            f"secure_cookie={_secure_cookie} scheme={request.scheme}"
        )
        # Recuperación elegante en pantallas de autenticación: regeneramos
        # la sesión y mandamos a la misma página con un aviso. Sin esto, el
        # usuario que pierde la cookie (típico en HTTP local con
        # SESSION_COOKIE_SECURE=true) ve un 403 y un bucle hacia /login.
        # El bruteforce sigue protegido por el rate limiter (5/10 min).
        if request.endpoint in ('login', 'setup'):
            session.clear()
            flash(
                'Tu sesión ha caducado o las cookies están bloqueadas. '
                'Vuelve a intentarlo. Si el problema persiste, revisa que '
                'tu navegador acepte cookies y que SESSION_COOKIE_SECURE='
                'false en el .env para acceso HTTP local.',
                'warning'
            )
            return redirect(url_for(request.endpoint))
        abort(403)
    return None


# Endpoints que NO cuentan como actividad del usuario para el timeout
# de sesión. Si entran aquí, el polling automático del dashboard mantendría
# la sesión viva indefinidamente, así que los excluimos.
ENDPOINTS_NO_RENUEVAN_SESION = {
    'api_dashboard_estado',  # polling cada 15s del dashboard
    'estado_franja',         # llamado por el scheduler interno
    'static',                # ficheros estáticos
}


@app.before_request
def antes_de_peticion():
    """
    Ejecutado antes de cada petición:
      1. Timeout de inactividad: cierra sesión si llevan más de 2 h sin actividad.
         IMPORTANTE: el polling automático del dashboard NO cuenta como
         actividad, sólo las peticiones disparadas por interacción real.
      2. Genera y verifica el token CSRF.
    """
    # ── Timeout de inactividad ───────────────────────────────────────────────
    if current_user.is_authenticated:
        ultima = session.get('_last_activity')
        ahora  = datetime.utcnow()
        if ultima:
            try:
                ultima_dt = datetime.fromisoformat(ultima)
            except ValueError:
                ultima_dt = ahora
            if (ahora - ultima_dt) > SESSION_TIMEOUT:
                log.info(
                    f"SESSION_TIMEOUT usuario={current_user.nombre} "
                    f"ip={request.remote_addr} inactividad={ahora - ultima_dt}"
                )
                logout_user()
                session.clear()
                flash('Tu sesión ha expirado por inactividad. Vuelve a iniciar sesión.', 'warning')
                return redirect(url_for('login'))

        # Sólo se renueva la marca de actividad si la petición no es polling
        # automático ni un asset estático. Si no, la sesión nunca expiraría.
        if request.endpoint not in ENDPOINTS_NO_RENUEVAN_SESION:
            session['_last_activity'] = ahora.isoformat()
            session.modified = True

    # ── CSRF ─────────────────────────────────────────────────────────────────
    g.csrf_token = _generar_csrf()
    csrf_response = _verificar_csrf()
    if csrf_response is not None:
        # _verificar_csrf devuelve un redirect cuando el fallo ocurre en
        # /login o /setup (recuperación elegante). En el resto de endpoints
        # ya habrá llamado a abort(403) y nunca llegamos aquí.
        return csrf_response


@app.context_processor
def inyectar_csrf():
    """Hace disponible csrf_token en todos los templates Jinja2."""
    return {'csrf_token': _generar_csrf()}


# =============================================================================
# CABECERAS DE SEGURIDAD HTTP
# =============================================================================
@app.after_request
def cabeceras_seguridad(response):
    """Añade cabeceras de seguridad a todas las respuestas HTTP."""
    # Evita que la app se cargue en un iframe (clickjacking)
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    # Evita MIME-sniffing
    response.headers['X-Content-Type-Options'] = 'nosniff'
    # Referrer mínimo
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    # CSP básica: recursos solo del mismo origen + CDN permitidas explícitamente
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' cdn.jsdelivr.net fonts.googleapis.com; "
        "font-src 'self' fonts.gstatic.com cdn.jsdelivr.net; "
        "img-src 'self' data:; "
        "connect-src 'self';"
    )
    return response

AVATAR_COLORS = [
    '#4f46e5', '#0891b2', '#059669',
    '#d97706', '#dc2626', '#7c3aed',
    '#db2777', '#0284c7'
]

DIAS_SEMANA = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes']

# Días y meses en español — usados para evitar depender del locale del SO,
# que en algunos contenedores devuelve los nombres en inglés (THURSDAY, MAY, ...).
DIAS_SEMANA_FULL = [
    'Lunes', 'Martes', 'Miércoles', 'Jueves',
    'Viernes', 'Sábado', 'Domingo'
]
MESES_ES = [
    '', 'enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio',
    'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre'
]


def fecha_larga_es(d) -> str:
    """
    Formatea una fecha al estilo 'Jueves, 07 de mayo de 2026' siempre en
    español, independientemente del locale del sistema operativo donde
    corra el contenedor.
    """
    return f"{DIAS_SEMANA_FULL[d.weekday()]}, {d.day:02d} de {MESES_ES[d.month]} de {d.year}"

LABEL_ROL = {
    'admin':    'Administrador/a',
    'tutor':    'Tutor/a',
    'profesor': 'Profesor/a',
}


# =============================================================================
# MODELO DE USUARIO (Flask-Login)
# =============================================================================

class Usuario(UserMixin):
    def __init__(self, id_usuario, nombre, email, rol, activo):
        self.id     = id_usuario
        self.nombre = nombre
        self.email  = email
        self.rol    = rol
        self.activo = activo

    @property
    def es_admin(self):
        return self.rol == 'admin'

    @property
    def es_tutor(self):
        return self.rol in ('admin', 'tutor')

    @property
    def label_rol(self):
        return LABEL_ROL.get(self.rol, self.rol)

    @property
    def iniciales(self):
        partes = self.nombre.strip().split()
        if len(partes) >= 2:
            return (partes[0][0] + partes[-1][0]).upper()
        return self.nombre[:2].upper()


@login_manager.user_loader
def cargar_usuario(id_usuario):
    conexion = conectar_db()
    cursor   = conexion.cursor()
    cursor.execute(
        "SELECT id_usuario, nombre, email, rol, activo FROM usuarios WHERE id_usuario = %s",
        (int(id_usuario),)
    )
    fila = cursor.fetchone()
    conexion.close()

    if not fila or not fila['activo']:
        return None

    return Usuario(
        id_usuario = fila['id_usuario'],
        nombre     = fila['nombre'],
        email      = fila['email'],
        rol        = fila['rol'],
        activo     = bool(fila['activo']),
    )


# =============================================================================
# DECORADOR DE ROL
# =============================================================================

def rol_requerido(*roles):
    def decorador(f):
        @wraps(f)
        def envoltura(*args, **kwargs):
            if current_user.rol not in roles:
                abort(403)
            return f(*args, **kwargs)
        return envoltura
    return decorador


# =============================================================================
# HELPER: AUTENTICACIÓN DEL SCHEDULER
# =============================================================================

def _es_llamada_scheduler() -> bool:
    """
    Devuelve True si la petición viene del scheduler interno.
    La autenticación se realiza SIEMPRE mediante la cabecera X-Scheduler-Key.
    La IP de origen por sí sola no es suficiente: un usuario que haga curl
    desde localhost obtendría exención CSRF indebida si sólo comprobamos IP.
    Si SCHEDULER_KEY no está configurada, se acepta cualquier llamada local
    como fallback de compatibilidad, pero se recomienda configurar la clave.
    """
    if SCHEDULER_KEY:
        return request.headers.get('X-Scheduler-Key', '') == SCHEDULER_KEY
    # Fallback: sin SCHEDULER_KEY configurada, sólo llamadas locales del host
    return request.remote_addr in ('127.0.0.1', '::1')


# =============================================================================
# CAPA DE BASE DE DATOS
# =============================================================================

def conectar_db() -> pymysql.Connection:
    host     = os.environ.get('DB_HOST',  '127.0.0.1')
    database = os.environ.get('DB_NAME',  'control_asistencia')
    user     = os.environ.get('DB_USER')
    password = os.environ.get('DB_PASSWORD')

    if not user or not password:
        raise RuntimeError("ERROR: DB_USER y/o DB_PASSWORD no están definidas.")

    return pymysql.connect(
        host=host, user=user, password=password, database=database,
        autocommit=False,
        cursorclass=pymysql.cursors.DictCursor
    )


def leer_config(cursor, clave: str) -> str:
    cursor.execute("SELECT valor FROM configuracion WHERE clave = %s", (clave,))
    fila = cursor.fetchone()
    return fila['valor'] if fila else ''


def escribir_config(cursor, clave: str, valor: str) -> None:
    cursor.execute(
        "INSERT INTO configuracion (clave, valor) VALUES (%s, %s) ON DUPLICATE KEY UPDATE valor = VALUES(valor)",
        (clave, valor)
    )


def _parse_hora(s: str):
    """Convierte 'HH:MM' a datetime.time. Devuelve None si formato inválido."""
    from datetime import time as dtime
    try:
        h, m = s.strip().split(':')
        return dtime(int(h), int(m))
    except Exception:
        return None


def estado_horario_escaneo(cursor) -> dict:
    """
    Determina si la hora actual cae dentro de alguna ventana de escaneo
    configurada por el admin (mañana / tarde, cada una activable por separado).

    Devuelve un dict con:
      - dentro_horario: bool
      - turno_activo:  'mañana' | 'tarde' | None
      - motivo:        string descriptivo cuando dentro_horario es False
      - ventanas:      dict con la config leída (para mostrar en UI / logs)
    """
    cfg = {
        'manana_activo': leer_config(cursor, 'escaneo_manana_activo') == 'true',
        'manana_inicio': leer_config(cursor, 'escaneo_manana_inicio') or '08:15',
        'manana_fin':    leer_config(cursor, 'escaneo_manana_fin')    or '14:45',
        'tarde_activo':  leer_config(cursor, 'escaneo_tarde_activo')  == 'true',
        'tarde_inicio':  leer_config(cursor, 'escaneo_tarde_inicio')  or '16:00',
        'tarde_fin':     leer_config(cursor, 'escaneo_tarde_fin')     or '21:45',
    }

    ahora_t = datetime.now().time()

    if cfg['manana_activo']:
        hi = _parse_hora(cfg['manana_inicio'])
        hf = _parse_hora(cfg['manana_fin'])
        if hi and hf and hi <= ahora_t < hf:
            return {'dentro_horario': True, 'turno_activo': 'mañana',
                    'motivo': None, 'ventanas': cfg}

    if cfg['tarde_activo']:
        hi = _parse_hora(cfg['tarde_inicio'])
        hf = _parse_hora(cfg['tarde_fin'])
        if hi and hf and hi <= ahora_t < hf:
            return {'dentro_horario': True, 'turno_activo': 'tarde',
                    'motivo': None, 'ventanas': cfg}

    if not cfg['manana_activo'] and not cfg['tarde_activo']:
        motivo = 'turnos_desactivados'
    else:
        motivo = 'fuera_horario'

    return {'dentro_horario': False, 'turno_activo': None,
            'motivo': motivo, 'ventanas': cfg}


def obtener_horario_activo(cursor) -> dict | None:
    override = leer_config(cursor, 'horario_override')
    if override:
        cursor.execute(
            """
            SELECT h.id_horario, a.nombre AS asignatura,
                   h.hora_inicio, h.hora_fin, h.aula, h.dia_semana
            FROM horarios h
            JOIN asignaturas a ON h.id_asignatura = a.id_asignatura
            WHERE h.id_horario = %s
            """,
            (int(override),)
        )
        return cursor.fetchone()

    ahora      = datetime.now()
    dia_hoy    = ahora.weekday()
    hora_ahora = ahora.time()

    if dia_hoy > 4:
        return None

    cursor.execute(
        """
        SELECT h.id_horario, a.nombre AS asignatura,
               h.hora_inicio, h.hora_fin, h.aula, h.dia_semana
        FROM horarios h
        JOIN asignaturas a ON h.id_asignatura = a.id_asignatura
        WHERE h.dia_semana = %s
          AND h.hora_inicio <= %s
          AND h.hora_fin    >  %s
        LIMIT 1
        """,
        (dia_hoy, hora_ahora, hora_ahora)
    )
    return cursor.fetchone()


def upsert_estado_dia(cursor, id_alumno: int, fecha: date, nuevo_estado: str) -> dict:
    cursor.execute(
        "SELECT estado_actual, notificado FROM estado_alumno_dia WHERE id_alumno = %s AND fecha = %s",
        (id_alumno, fecha)
    )
    fila = cursor.fetchone()

    if fila is None:
        cursor.execute(
            "INSERT INTO estado_alumno_dia (id_alumno, fecha, estado_actual, notificado) VALUES (%s, %s, %s, FALSE)",
            (id_alumno, fecha, nuevo_estado)
        )
        return {'estado_anterior': None, 'estado_actual': nuevo_estado, 'notificado': False}

    estado_anterior  = fila['estado_actual']
    notificado_prev  = bool(fila['notificado'])
    nuevo_notificado = notificado_prev if estado_anterior == nuevo_estado else False

    cursor.execute(
        "UPDATE estado_alumno_dia SET estado_actual = %s, notificado = %s, ultima_actualizacion = NOW() WHERE id_alumno = %s AND fecha = %s",
        (nuevo_estado, nuevo_notificado, id_alumno, fecha)
    )
    return {'estado_anterior': estado_anterior, 'estado_actual': nuevo_estado, 'notificado': nuevo_notificado}


# Helpers para asignaturas con múltiples profesores
def _obtener_profesores_asignatura(cursor, id_asignatura: int) -> list[dict]:
    cursor.execute(
        """
        SELECT u.id_usuario, u.nombre, u.rol
        FROM asignatura_profesores ap
        JOIN usuarios u ON ap.id_usuario = u.id_usuario
        WHERE ap.id_asignatura = %s
        ORDER BY u.nombre ASC
        """,
        (id_asignatura,)
    )
    return cursor.fetchall()


def _actualizar_profesores_asignatura(cursor, id_asignatura: int, ids_profesores: list[int]) -> None:
    """Reemplaza completamente los profesores de una asignatura."""
    cursor.execute("DELETE FROM asignatura_profesores WHERE id_asignatura = %s", (id_asignatura,))
    for id_prof in ids_profesores:
        cursor.execute(
            "INSERT IGNORE INTO asignatura_profesores (id_asignatura, id_usuario) VALUES (%s, %s)",
            (id_asignatura, id_prof)
        )


# =============================================================================
# FILTROS JINJA2
# =============================================================================

def obtener_iniciales(nombre_completo: str) -> str:
    partes = nombre_completo.strip().split()
    if len(partes) >= 2:
        return (partes[0][0] + partes[-1][0]).upper()
    return nombre_completo[:2].upper() if nombre_completo else '?'


def obtener_color_avatar(nombre: str) -> str:
    indice = sum(ord(c) for c in nombre) % len(AVATAR_COLORS)
    return AVATAR_COLORS[indice]


def formatear_dia(numero: int) -> str:
    return DIAS_SEMANA[numero] if 0 <= numero <= 4 else '—'


def validar_contrasena(contrasena: str, nombre_usuario: str = '') -> str | None:
    """
    Valida que la contraseña cumpla los requisitos mínimos de seguridad.
    Devuelve None si es válida, o un mensaje de error si no lo es.
    """
    if len(contrasena) < 8:
        return 'La contraseña debe tener al menos 8 caracteres.'
    if not re.search(r'[A-Z]', contrasena):
        return 'La contraseña debe contener al menos una letra mayúscula.'
    if not re.search(r'[a-z]', contrasena):
        return 'La contraseña debe contener al menos una letra minúscula.'
    if not re.search(r'\d', contrasena):
        return 'La contraseña debe contener al menos un número.'
    # Evitar que la contraseña sea demasiado similar al nombre de usuario
    if nombre_usuario:
        nombre_norm = nombre_usuario.strip().lower().replace(' ', '')
        pass_norm   = contrasena.lower()
        if nombre_norm and len(nombre_norm) >= 4 and nombre_norm in pass_norm:
            return 'La contraseña no puede contener tu nombre de usuario.'
    return None


def calcular_siglas(nombre: str) -> str:
    """
    Genera las siglas de una asignatura a partir de su nombre.
    Toma la primera letra de cada palabra significativa (≥3 chars),
    hasta un máximo de 4 caracteres. Si el nombre es una sola palabra,
    devuelve los 4 primeros caracteres en mayúsculas.
    Ejemplos:
      'Servicios de Red e Internet'             → 'SRI'
      'Seguridad y Alta Disponibilidad'         → 'SAD'
      'Administración de Sistemas Operativos'   → 'ASO'
    """
    PALABRAS_IGNORADAS = {'de', 'del', 'la', 'el', 'los', 'las', 'y', 'en', 'a', 'o'}
    palabras = nombre.strip().split()
    significativas = [p for p in palabras if p.lower() not in PALABRAS_IGNORADAS and len(p) >= 2]
    if not significativas:
        significativas = palabras
    if len(significativas) == 1:
        return significativas[0][:4].upper()
    return ''.join(p[0] for p in significativas)[:4].upper()


app.jinja_env.filters['iniciales']    = obtener_iniciales
app.jinja_env.filters['color_avatar'] = obtener_color_avatar
app.jinja_env.filters['dia_semana']   = formatear_dia
app.jinja_env.filters['siglas']       = calcular_siglas


# =============================================================================
# MANEJO DE ERRORES
# =============================================================================

@app.errorhandler(400)
def error_400(e):
    return render_template('400.html'), 400


@app.errorhandler(403)
def error_403(e):
    return render_template('403.html'), 403


@app.errorhandler(404)
def error_404(e):
    return render_template('404.html'), 404


@app.errorhandler(429)
def error_429(e):
    """Rate limit excedido en /login (u otros endpoints con limit)."""
    return render_template('429.html'), 429


@app.errorhandler(500)
def error_500(e):
    log.error(f"ERROR_500 path={request.path} error={e}")
    return render_template('500.html'), 500


@app.errorhandler(Exception)
def error_no_capturado(e):
    """
    Captura cualquier excepción no manejada explícitamente.
    La registra en el log y devuelve la página 500 sin exponer
    stack traces ni detalles internos al usuario.
    """
    import traceback
    log.error(
        f"EXCEPCION_NO_CAPTURADA path={request.path} "
        f"tipo={type(e).__name__} error={e}\n"
        + traceback.format_exc()
    )
    return render_template('500.html'), 500


# =============================================================================
# AUTENTICACIÓN
# =============================================================================

def hay_usuarios() -> bool:
    conexion = conectar_db()
    cursor   = conexion.cursor()
    cursor.execute("SELECT COUNT(*) AS total FROM usuarios")
    total = cursor.fetchone()['total']
    conexion.close()
    return total > 0


@app.route('/setup', methods=['GET', 'POST'])
def setup():
    if hay_usuarios():
        return redirect(url_for('login'))

    if request.method == 'POST':
        nombre       = request.form.get('nombre', '').strip()
        email        = request.form.get('email', '').strip().lower()
        contrasena   = request.form.get('contrasena', '').strip()
        confirmacion = request.form.get('confirmacion', '').strip()

        errores = []
        if not nombre:
            errores.append('El nombre es obligatorio.')
        if not email:
            errores.append('El email es obligatorio.')
        error_pass = validar_contrasena(contrasena, nombre)
        if error_pass:
            errores.append(error_pass)
        if contrasena != confirmacion:
            errores.append('Las contraseñas no coinciden.')

        if errores:
            for e in errores:
                flash(e, 'danger')
            return render_template('setup.html')

        password_hash = bcrypt.generate_password_hash(contrasena).decode('utf-8')

        conexion = conectar_db()
        cursor   = conexion.cursor()
        try:
            cursor.execute(
                "INSERT INTO usuarios (nombre, email, password_hash, rol) VALUES (%s, %s, %s, 'admin')",
                (nombre, email, password_hash)
            )
            conexion.commit()
            flash('✅ Sistema configurado correctamente. Ya puedes iniciar sesión.', 'success')
            return redirect(url_for('login'))
        except Exception as e:
            flash(f'❌ Error al crear el administrador: {e}', 'danger')
            return render_template('setup.html')
        finally:
            conexion.close()

    return render_template('setup.html')


@app.route('/login', methods=['GET', 'POST'])
@limiter.limit("5 per 10 minutes", methods=["POST"],
               error_message="Demasiados intentos de login. Espera 10 minutos.")
def login():
    if not hay_usuarios():
        return redirect(url_for('setup'))

    if current_user.is_authenticated:
        return redirect(url_for('index'))

    if request.method == 'POST':
        email      = request.form.get('email', '').strip().lower()
        contrasena = request.form.get('contrasena', '')

        if not email or not contrasena:
            flash('Introduce tu email y contraseña.', 'danger')
            return render_template('login.html')

        conexion = conectar_db()
        cursor   = conexion.cursor()
        cursor.execute(
            "SELECT id_usuario, nombre, email, password_hash, rol, activo FROM usuarios WHERE email = %s",
            (email,)
        )
        fila = cursor.fetchone()
        conexion.close()

        # ── Timing uniforme (anti-enumeración de usuarios) ──────────────────
        # bcrypt.check_password_hash tarda ~100ms. Si el usuario no existe,
        # se compara contra un hash ficticio REAL (no una cadena vacía) para
        # que el tiempo de respuesta sea idéntico y no sea posible distinguir
        # "usuario no existe" de "contraseña incorrecta" por diferencia de tiempo.
        HASH_FICTICIO = '$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMeJf36/FeZyB3e4eTudBVZqa.'
        hash_bd     = fila['password_hash'] if fila else HASH_FICTICIO
        hash_valido = bcrypt.check_password_hash(hash_bd, contrasena)

        # Mensaje de error SIEMPRE idéntico, independientemente de si el
        # usuario existe o no (evita enumeración por mensaje diferente).
        MSG_ERROR = 'Email o contraseña incorrectos.'

        if not fila or not hash_valido:
            log.warning(f"LOGIN_FALLIDO email={email} ip={request.remote_addr}")
            flash(MSG_ERROR, 'danger')
            return render_template('login.html')

        if not fila['activo']:
            # Mismo mensaje genérico — no revelar que el usuario existe
            log.warning(f"LOGIN_INACTIVO email={email} ip={request.remote_addr}")
            flash(MSG_ERROR, 'danger')
            return render_template('login.html')

        usuario = Usuario(
            id_usuario = fila['id_usuario'],
            nombre     = fila['nombre'],
            email      = fila['email'],
            rol        = fila['rol'],
            activo     = bool(fila['activo']),
        )
        login_user(usuario, remember=False)
        session.permanent = True           # Activa PERMANENT_SESSION_LIFETIME
        session['_last_activity'] = datetime.utcnow().isoformat()
        log.info(f"LOGIN_OK usuario={usuario.nombre} rol={usuario.rol} ip={request.remote_addr}")

        siguiente = request.args.get('next')
        return redirect(siguiente or url_for('index'))

    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    log.info(f"LOGOUT usuario={current_user.nombre} ip={request.remote_addr}")
    logout_user()
    flash('Sesión cerrada correctamente.', 'info')
    return redirect(url_for('login'))


# =============================================================================
# DASHBOARD
# =============================================================================

@app.route('/')
@login_required
def index():
    conexion = conectar_db()
    cursor   = conexion.cursor()
    hoy = date.today()

    escaneo_pausado = leer_config(cursor, 'escaneo_pausado') == 'true'
    horario_activo  = obtener_horario_activo(cursor)
    override_activo = leer_config(cursor, 'horario_override') != ''
    estado_esc      = estado_horario_escaneo(cursor)

    cursor.execute(
        "SELECT MAX(hora_registro) AS ultima_hora FROM asistencia WHERE fecha = %s", (hoy,)
    )
    fila_hora      = cursor.fetchone()
    ultimo_escaneo = fila_hora['ultima_hora'] if fila_hora else None

    cursor.execute(
        """
        SELECT
            al.id_alumno,
            al.apellidos,
            CONCAT(al.nombre, ' ', al.apellidos) AS nombre_completo,
            al.grupo,
            al.turno,
            al.mac_bluetooth,
            ead.estado_actual,
            ead.notificado,
            ead.ultima_actualizacion
        FROM alumnos al
        LEFT JOIN estado_alumno_dia ead
            ON al.id_alumno = ead.id_alumno AND ead.fecha = %s
        WHERE al.activo = TRUE
        ORDER BY al.apellidos ASC, al.nombre ASC
        """,
        (hoy,)
    )
    alumnos_hoy = cursor.fetchall()
    conexion.close()

    total     = len(alumnos_hoy)
    presentes = sum(1 for a in alumnos_hoy if a['estado_actual'] == 'PRESENTE')
    ausentes  = sum(1 for a in alumnos_hoy if a['estado_actual'] == 'AUSENTE')
    sin_datos = total - presentes - ausentes

    # Separar alumnos por turno para los dos paneles del dashboard
    alumnos_manana = [a for a in alumnos_hoy if a['turno'] in ('mañana', 'ambos')]
    alumnos_tarde  = [a for a in alumnos_hoy if a['turno'] in ('tarde',  'ambos')]

    return render_template(
        'index.html',
        alumnos_hoy            = alumnos_hoy,
        ultimo_escaneo         = ultimo_escaneo,
        horario_activo         = horario_activo,
        override_activo        = override_activo,
        escaneo_pausado        = escaneo_pausado,
        fuera_horario_escaneo  = not estado_esc['dentro_horario'],
        motivo_fuera_horario   = estado_esc['motivo'],
        ventanas_escaneo       = estado_esc['ventanas'],
        presentes              = presentes,
        ausentes               = ausentes,
        sin_datos              = sin_datos,
        total                  = total,
        metodo_bt              = nombre_metodo_activo(),
        hoy                    = fecha_larga_es(hoy),
        es_fin_de_semana       = hoy.weekday() >= 5,
        alumnos_manana         = alumnos_manana,
        alumnos_tarde          = alumnos_tarde,
    )


@app.route('/api/dashboard-estado')
@login_required
def api_dashboard_estado():
    """
    Endpoint ligero usado por el dashboard para auto-refresh. Devuelve un
    "fingerprint" del estado actual; si el cliente detecta que ha cambiado
    desde la última vez, recarga la página. Así evitamos recargar cada N
    segundos a ciegas (perdiendo filtros) — solo recargamos cuando hay
    datos nuevos.
    """
    conexion = conectar_db()
    cursor   = conexion.cursor()
    hoy = date.today()
    cursor.execute(
        "SELECT MAX(hora_registro) AS ultima_hora FROM asistencia WHERE fecha = %s", (hoy,)
    )
    fila = cursor.fetchone()
    pausado = leer_config(cursor, 'escaneo_pausado') == 'true'
    estado  = estado_horario_escaneo(cursor)
    conexion.close()

    return jsonify({
        "ultimo_escaneo":  str(fila['ultima_hora']) if fila and fila['ultima_hora'] else '',
        "escaneo_pausado": pausado,
        "fuera_horario":   not estado['dentro_horario'],
    })


# =============================================================================
# ESCANEO BLUETOOTH
# =============================================================================

@app.route('/escanear', methods=['POST'])
def escanear():
    # Permitir llamadas del scheduler sin sesión activa
    if not current_user.is_authenticated and not _es_llamada_scheduler():
        return redirect(url_for('login', next=request.path))

    # ── Escaneo forzado por el admin (bypasa horario y recreo) ────────────────
    # Solo un admin autenticado puede forzar el escaneo fuera del horario
    # configurado. El scheduler nunca usa este flag.
    force = False
    if current_user.is_authenticated and current_user.es_admin:
        try:
            # force=True en get_json ignora el Content-Type y siempre intenta
            # parsear el body como JSON. Necesario porque proxies (Cloudflare,
            # etc.) pueden modificar o no reenviar la cabecera correctamente.
            body  = request.get_json(silent=True, force=True) or {}
            force = bool(body.get('force', False))
        except Exception:
            pass

    # ── Comprobar override antes del bloqueo de fin de semana ─────────────────
    # Si hay un override manual activo, se considera que hay clase ahora mismo
    # independientemente del día. Esto permite al admin lanzar escaneos en
    # fin de semana sin necesidad de pulsar el botón forzado.
    conexion_pre = conectar_db()
    cursor_pre   = conexion_pre.cursor()
    override_id_pre = leer_config(cursor_pre, 'horario_override')
    override_activo_pre = bool(override_id_pre)
    conexion_pre.close()

    # ── Bloqueo de fin de semana ───────────────────────────────────────────────
    if not force and not override_activo_pre and datetime.now().weekday() >= 5:
        return jsonify({
            "status":  "fin_de_semana",
            "mensaje": "Sistema en reposo. No se realizan escaneos durante el fin de semana."
        }), 423

    conexion = conectar_db()
    cursor   = conexion.cursor()

    if leer_config(cursor, 'escaneo_pausado') == 'true':
        conexion.close()
        return jsonify({"status": "pausado", "mensaje": "El escaneo está pausado."}), 423

    # ── Ventana de escaneo configurable (mañana / tarde) ───────────────────
    # Aplica también al botón manual: si estamos fuera del horario que el
    # admin ha configurado, no se escanea (evita registrar ausencias antes
    # de empezar las clases o después de terminar).
    # Con force=True el admin puede saltarse esta restricción.
    estado = estado_horario_escaneo(cursor)
    if not force and not estado['dentro_horario']:
        conexion.close()
        if estado['motivo'] == 'turnos_desactivados':
            msg = "Los dos turnos de escaneo están desactivados en la configuración."
        else:
            msg = "Fuera del horario de escaneo configurado."
        return jsonify({"status": "fuera_horario", "mensaje": msg}), 423

    # En escaneo forzado fuera de horario no hay turno definido → escanear todos.
    turno_activo = estado['turno_activo'] if estado['dentro_horario'] else None

    from datetime import time as dtime
    ahora_t = datetime.now().time()

    def _en_recreo(ini_str, fin_str):
        """Comprueba si la hora actual cae dentro de la franja de recreo."""
        if not ini_str or not fin_str:
            return False
        try:
            hi = dtime(*[int(x) for x in ini_str.split(':')])
            hf = dtime(*[int(x) for x in fin_str.split(':')])
            return hi <= ahora_t < hf
        except Exception:
            return False

    recreo_inicio       = leer_config(cursor, 'recreo_inicio')
    recreo_fin          = leer_config(cursor, 'recreo_fin')
    recreo_tarde_inicio = leer_config(cursor, 'recreo_tarde_inicio')
    recreo_tarde_fin    = leer_config(cursor, 'recreo_tarde_fin')

    if not force and _en_recreo(recreo_inicio, recreo_fin):
        conexion.close()
        return jsonify({
            "status":  "recreo",
            "mensaje": f"Hora de recreo de mañana ({recreo_inicio}–{recreo_fin}). El escaneo se reanudará automáticamente."
        }), 423
    if not force and _en_recreo(recreo_tarde_inicio, recreo_tarde_fin):
        conexion.close()
        return jsonify({
            "status":  "recreo",
            "mensaje": f"Hora de recreo de tarde ({recreo_tarde_inicio}–{recreo_tarde_fin}). El escaneo se reanudará automáticamente."
        }), 423

    # ── Comprobar que hay una franja horaria (clase real) activa ──────────────
    # Sin esta comprobación se podía registrar "ausencias" fuera de las
    # clases configuradas (asignatura=None, aula=None, hora_inicio=None…)
    # con sólo estar dentro de la ventana general mañana/tarde. Ahora exigimos
    # que exista un registro en `horarios` cuyo rango incluya la hora actual
    # (o un override manual desde la web). Aplica también al botón manual.
    horario_activo = obtener_horario_activo(cursor)
    if not horario_activo:
        conexion.close()
        return jsonify({
            "status":  "sin_franja",
            "mensaje": "No hay ninguna franja horaria activa en este momento. "
                       "El escaneo sólo registra ausencias durante las clases configuradas en Horarios."
        }), 423

    conexion.close()

    if not _lock_escaneo.acquire(blocking=False):
        return jsonify({"status": "busy", "mensaje": "Ya hay un escaneo en curso."}), 409

    try:
        encender_bt()

        conexion = conectar_db()
        cursor   = conexion.cursor()

        # Se vuelve a leer el horario activo: la conexión anterior ya está
        # cerrada y entre tanto el usuario podría haber cambiado el override.
        horario_activo = obtener_horario_activo(cursor)
        id_horario     = horario_activo['id_horario'] if horario_activo else None

        # Filtro de turno basado en la ventana de escaneo activa según la
        # configuración del admin (mañana / tarde). Quien tiene turno=ambos
        # entra siempre. Esto reemplaza el rango hardcodeado anterior.
        if turno_activo == 'mañana':
            _turnos_sql = "AND turno IN ('mañana', 'ambos')"
        elif turno_activo == 'tarde':
            _turnos_sql = "AND turno IN ('tarde', 'ambos')"
        else:
            _turnos_sql = ""

        cursor.execute(
            f"SELECT id_alumno, nombre, apellidos, mac_bluetooth FROM alumnos WHERE activo = TRUE AND mac_bluetooth IS NOT NULL {_turnos_sql}"
        )
        alumnos_bd = cursor.fetchall()

        if not alumnos_bd:
            # Aún sin alumnos a escanear, marcamos timestamp para que el cron
            # respete la frecuencia y no reintente cada minuto.
            escribir_config(cursor, 'escaneo_ultima_ejecucion', datetime.now().isoformat(timespec='seconds'))
            conexion.commit()
            conexion.close()
            return jsonify({"status": "ok", "presentes": 0, "ausentes": 0, "total": 0, "horario": None})

        alumnos_detector = [
            (a['id_alumno'], f"{a['nombre']} {a['apellidos']}", a['mac_bluetooth'])
            for a in alumnos_bd
        ]

        resultados    = escanear_alumnos(alumnos_detector)
        fecha_hoy     = date.today()
        hora_registro = datetime.now().time()

        pendientes_notificar = []

        for id_alumno, nombre_completo, esta_presente in resultados:
            nuevo_estado = 'PRESENTE' if esta_presente else 'AUSENTE'

            # Una sola fila por (alumno, horario, fecha).
            cursor.execute(
                """
                INSERT INTO asistencia (id_alumno, id_horario, fecha, hora_registro, estado)
                VALUES (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    estado        = IF(VALUES(estado) = 'PRESENTE', 'PRESENTE', estado),
                    hora_registro = IF(VALUES(estado) = 'PRESENTE' AND estado = 'AUSENTE',
                                       VALUES(hora_registro), hora_registro)
                """,
                (id_alumno, id_horario, fecha_hoy, hora_registro, nuevo_estado)
            )

            resultado_estado = upsert_estado_dia(cursor, id_alumno, fecha_hoy, nuevo_estado)

            estado_anterior   = resultado_estado['estado_anterior']
            ya_estaba_ausente = (estado_anterior == 'AUSENTE')

            if (nuevo_estado == 'AUSENTE'
                    and ya_estaba_ausente
                    and not resultado_estado['notificado']):
                pendientes_notificar.append({
                    'id_alumno':      id_alumno,
                    'nombre':         nombre_completo,
                    'horario_activo': horario_activo
                })
                cursor.execute(
                    "UPDATE estado_alumno_dia SET notificado = TRUE WHERE id_alumno = %s AND fecha = %s",
                    (id_alumno, fecha_hoy)
                )

        # Marca la hora del escaneo para que el gating por frecuencia (ver
        # /estado-franja) sepa cuándo fue el último.
        escribir_config(cursor, 'escaneo_ultima_ejecucion', datetime.now().isoformat(timespec='seconds'))

        conexion.commit()
        conexion.close()

        if pendientes_notificar:
            print(f"\n[Notificaciones] {len(pendientes_notificar)} alumno(s) para notificar:")
            for p in pendientes_notificar:
                print(f"  ⚠️  {p['nombre']}")

            conexion_n = conectar_db()
            cursor_n   = conexion_n.cursor()
            ids        = [p['id_alumno'] for p in pendientes_notificar]
            fmt        = ','.join(['%s'] * len(ids))
            cursor_n.execute(
                f"SELECT id_alumno, CONCAT(nombre, ' ', apellidos) AS nombre_completo, grupo, email_tutor FROM alumnos WHERE id_alumno IN ({fmt})",
                ids
            )
            datos_alumnos = {row['id_alumno']: row for row in cursor_n.fetchall()}
            conexion_n.close()

            for p in pendientes_notificar:
                alumno = datos_alumnos.get(p['id_alumno'])
                if not alumno:
                    continue
                telegram_ausencia(alumno['nombre_completo'], alumno['grupo'], p['horario_activo'])
                log.info(f"NOTIFICACION_TELEGRAM alumno={alumno['nombre_completo']} grupo={alumno['grupo']}")

        total_presentes = sum(1 for _, _, p in resultados if p)
        total_ausentes  = len(resultados) - total_presentes

        log.info(
            f"ESCANEO presentes={total_presentes} ausentes={total_ausentes} "
            f"total={len(resultados)} horario={horario_activo['asignatura'] if horario_activo else 'ninguno'}"
        )

        # ── Resumen de fin de turno ───────────────────────────────────────────
        # Si estamos en los últimos minutos del turno activo (dentro de la
        # ventana de frecuencia*1.5), enviamos un resumen por Telegram con todos
        # los alumnos que han tenido ausencias durante el turno.
        _resumen_enviado = False
        if turno_activo in ('mañana', 'tarde'):
            # Usar las ventanas capturadas antes del primer close() de la conexión
            _vent    = estado.get('ventanas', {})
            _fin_key = 'manana_fin' if turno_activo == 'mañana' else 'tarde_fin'
            _fin_str = _vent.get(_fin_key, '')
            if _fin_str:
                from datetime import time as _dtime
                try:
                    # Abrir una conexión fresca: la original ya está cerrada
                    _cx_r  = conectar_db()
                    _cur_r = _cx_r.cursor()
                    _frec = int(leer_config(_cur_r, 'escaneo_frecuencia_min') or '10')
                    _hf   = _dtime(*[int(x) for x in _fin_str.split(':')])
                    # Ventana: [fin_turno - frec*1.5 min ... fin_turno]
                    from datetime import timedelta
                    _fin_dt = datetime.combine(date.today(), _hf)
                    _ini_dt = _fin_dt - timedelta(minutes=int(_frec * 1.5))
                    _now_dt = datetime.now()

                    _resumen_key = f'resumen_turno_{turno_activo}_{date.today().isoformat()}'
                    _ya_enviado  = leer_config(_cur_r, _resumen_key) == 'enviado'

                    if not _ya_enviado and _ini_dt <= _now_dt <= _fin_dt:
                        # Consultar ausentes del día para este turno
                        _turno_sql = f"AND al.turno IN ('{turno_activo}', 'ambos')"
                        _cur_r.execute(
                            f"""
                            SELECT CONCAT(al.apellidos, ', ', al.nombre) AS nombre,
                                   al.grupo,
                                   COUNT(*) AS clases_ausente
                            FROM asistencia a
                            JOIN alumnos al ON a.id_alumno = al.id_alumno
                            WHERE a.fecha = %s AND a.estado = 'AUSENTE' {_turno_sql}
                            GROUP BY al.id_alumno
                            ORDER BY al.apellidos ASC
                            """,
                            (date.today(),)
                        )
                        _ausentes_turno = _cur_r.fetchall()
                        _lista_ausentes = [
                            {'nombre': r['nombre'], 'grupo': r['grupo'], 'clases_ausente': r['clases_ausente']}
                            for r in _ausentes_turno
                        ]
                        threading.Thread(
                            target=telegram_resumen_turno,
                            args=(turno_activo, _lista_ausentes),
                            daemon=True
                        ).start()
                        escribir_config(_cur_r, _resumen_key, 'enviado')
                        _cx_r.commit()
                        log.info(f"RESUMEN_TURNO turno={turno_activo} ausentes={len(_lista_ausentes)}")
                        _resumen_enviado = True
                    _cx_r.close()
                except Exception as _e:
                    log.warning(f"RESUMEN_TURNO Error calculando resumen: {_e}")

        return jsonify({
            "status":    "ok",
            "presentes": total_presentes,
            "ausentes":  total_ausentes,
            "total":     len(resultados),
            "horario":   horario_activo['asignatura'] if horario_activo else None
        })

    finally:
        _lock_escaneo.release()


# =============================================================================
# GESTIÓN DE ALUMNOS
# =============================================================================

@app.route('/alumnos')
@login_required
def gestion_alumnos():
    conexion = conectar_db()
    cursor   = conexion.cursor()
    cursor.execute(
        "SELECT id_alumno, nombre, apellidos, mac_bluetooth, grupo, turno, email_tutor, telefono_tutor, activo FROM alumnos ORDER BY apellidos ASC, nombre ASC"
    )
    alumnos = cursor.fetchall()
    conexion.close()
    return render_template('alumnos.html', alumnos=alumnos)


@app.route('/alumnos/add', methods=['POST'])
@login_required
@rol_requerido('admin', 'tutor')
def add_alumno():
    nombre         = request.form['nombre'].strip()
    apellidos      = request.form['apellidos'].strip()
    mac            = request.form.get('mac', '').strip().upper() or None
    grupo          = request.form['grupo'].strip()
    turno          = request.form.get('turno', 'ambos').strip()
    email_tutor    = request.form.get('email_tutor', '').strip() or None
    telefono_tutor = request.form.get('telefono_tutor', '').strip() or None

    if turno not in ('mañana', 'tarde', 'ambos'):
        turno = 'ambos'

    if not nombre or not apellidos or not grupo:
        flash('❌ Nombre, apellidos y grupo son obligatorios.', 'danger')
        return redirect(url_for('gestion_alumnos'))

    conexion = conectar_db()
    cursor   = conexion.cursor()
    try:
        cursor.execute(
            "INSERT INTO alumnos (nombre, apellidos, mac_bluetooth, grupo, turno, email_tutor, telefono_tutor) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (nombre, apellidos, mac, grupo, turno, email_tutor, telefono_tutor)
        )
        conexion.commit()
        flash(f'✅ Alumno/a <strong>{_esc(nombre)} {_esc(apellidos)}</strong> añadido/a correctamente.', 'success')
    except pymysql.err.IntegrityError:
        flash(f'❌ La MAC <code>{_esc(mac)}</code> ya está registrada en otro alumno.', 'danger')
    except Exception as e:
        flash(f'❌ Error inesperado: {e}', 'danger')
    finally:
        conexion.close()
    return redirect(url_for('gestion_alumnos'))


@app.route('/alumnos/edit/<int:id_alumno>', methods=['POST'])
@login_required
@rol_requerido('admin', 'tutor')
def edit_alumno(id_alumno):
    nombre         = request.form['nombre'].strip()
    apellidos      = request.form['apellidos'].strip()
    mac            = request.form.get('mac', '').strip().upper() or None
    grupo          = request.form['grupo'].strip()
    turno          = request.form.get('turno', 'ambos').strip()
    email_tutor    = request.form.get('email_tutor', '').strip() or None
    telefono_tutor = request.form.get('telefono_tutor', '').strip() or None

    if turno not in ('mañana', 'tarde', 'ambos'):
        turno = 'ambos'

    conexion = conectar_db()
    cursor   = conexion.cursor()
    try:
        cursor.execute(
            "UPDATE alumnos SET nombre=%s, apellidos=%s, mac_bluetooth=%s, grupo=%s, turno=%s, email_tutor=%s, telefono_tutor=%s WHERE id_alumno=%s",
            (nombre, apellidos, mac, grupo, turno, email_tutor, telefono_tutor, id_alumno)
        )
        conexion.commit()
        flash(f'✅ Datos de <strong>{_esc(nombre)} {_esc(apellidos)}</strong> actualizados.', 'success')
    except pymysql.err.IntegrityError:
        flash(f'❌ La MAC <code>{_esc(mac)}</code> ya la usa otro alumno/a.', 'danger')
    except Exception as e:
        flash(f'❌ Error inesperado: {e}', 'danger')
    finally:
        conexion.close()
    return redirect(url_for('gestion_alumnos'))


@app.route('/alumnos/delete/<int:id_alumno>', methods=['POST'])
@login_required
@rol_requerido('admin')
def delete_alumno(id_alumno):
    conexion = conectar_db()
    cursor   = conexion.cursor()
    cursor.execute("DELETE FROM alumnos WHERE id_alumno=%s", (id_alumno,))
    conexion.commit()
    conexion.close()
    flash('🗑️ Alumno/a eliminado/a correctamente.', 'warning')
    return redirect(url_for('gestion_alumnos'))


@app.route('/alumnos/toggle/<int:id_alumno>', methods=['POST'])
@login_required
@rol_requerido('admin', 'tutor')
def toggle_alumno(id_alumno):
    conexion = conectar_db()
    cursor   = conexion.cursor()
    cursor.execute("UPDATE alumnos SET activo = NOT activo WHERE id_alumno = %s", (id_alumno,))
    conexion.commit()
    conexion.close()
    flash('↩️ Estado del alumno/a actualizado.', 'info')
    return redirect(url_for('gestion_alumnos'))


# =============================================================================
# NOTIFICAR TUTOR LEGAL
# =============================================================================

@app.route('/asistencia/editar/<int:id_alumno>', methods=['POST'])
@login_required
def editar_asistencia(id_alumno):
    """
    Edición manual del estado de asistencia de un alumno para hoy.
    Accesible por todos los roles (admin, tutor, profesor).

    Body JSON: { "estado": "PRESENTE" | "AUSENTE" | "SIN_DATOS" }

    SIN_DATOS elimina el registro del día en lugar de escribir un valor
    especial, porque en la BD la ausencia de fila == sin datos.
    """
    body        = request.get_json(silent=True, force=True) or {}
    nuevo_estado = body.get('estado', '').upper()

    if nuevo_estado not in ('PRESENTE', 'AUSENTE', 'SIN_DATOS'):
        return jsonify({"status": "error", "mensaje": "Estado inválido."}), 400

    conexion = conectar_db()
    cursor   = conexion.cursor()

    cursor.execute(
        "SELECT id_alumno, nombre FROM alumnos WHERE id_alumno = %s AND activo = TRUE",
        (id_alumno,)
    )
    alumno = cursor.fetchone()
    if not alumno:
        conexion.close()
        return jsonify({"status": "error", "mensaje": "Alumno no encontrado."}), 404

    hoy = date.today()

    if nuevo_estado == 'SIN_DATOS':
        cursor.execute(
            "DELETE FROM estado_alumno_dia WHERE id_alumno = %s AND fecha = %s",
            (id_alumno, hoy)
        )
    else:
        upsert_estado_dia(cursor, id_alumno, hoy, nuevo_estado)

    conexion.commit()
    conexion.close()

    log.info(
        f"ASISTENCIA_MANUAL usuario={current_user.nombre} "
        f"alumno={alumno['nombre']} estado={nuevo_estado} fecha={hoy}"
    )
    return jsonify({"status": "ok", "estado": nuevo_estado})


@app.route('/alumnos/notificar-tutor/<int:id_alumno>', methods=['POST'])
@login_required
@rol_requerido('admin', 'tutor')
def notificar_tutor(id_alumno):
    conexion = conectar_db()
    cursor   = conexion.cursor()
    cursor.execute(
        "SELECT CONCAT(nombre, ' ', apellidos) AS nombre_completo, grupo, email_tutor FROM alumnos WHERE id_alumno = %s",
        (id_alumno,)
    )
    alumno = cursor.fetchone()
    conexion.close()

    if not alumno:
        flash('❌ Alumno/a no encontrado/a.', 'danger')
        return redirect(url_for('index'))

    if not alumno['email_tutor']:
        flash('⚠️ Este alumno/a no tiene email del padre/madre/tutor registrado.', 'warning')
        return redirect(url_for('index'))

    conexion2 = conectar_db()
    cursor2   = conexion2.cursor()
    cursor2.execute(
        """
        SELECT h.aula, a.nombre AS asignatura, h.hora_inicio, h.hora_fin
        FROM asistencia ast
        LEFT JOIN horarios h    ON ast.id_horario  = h.id_horario
        LEFT JOIN asignaturas a ON h.id_asignatura = a.id_asignatura
        WHERE ast.id_alumno = %s AND ast.fecha = CURDATE()
        ORDER BY ast.hora_registro DESC LIMIT 1
        """,
        (id_alumno,)
    )
    horario = cursor2.fetchone()
    conexion2.close()

    exito = enviar_correo_tutor(
        alumno['nombre_completo'], alumno['grupo'],
        alumno['email_tutor'], horario
    )

    if exito:
        log.info(f"CORREO_TUTOR alumno={alumno['nombre_completo']} destino={alumno['email_tutor']}")
        flash(f'✅ Correo enviado a {alumno["email_tutor"]}.', 'success')
    else:
        log.error(f"CORREO_TUTOR_ERROR alumno={alumno['nombre_completo']} destino={alumno['email_tutor']}")
        flash('❌ Error al enviar el correo. Comprueba la configuración SMTP.', 'danger')

    return redirect(url_for('index'))


# =============================================================================
# GESTIÓN DE ASIGNATURAS (con múltiples profesores)
# =============================================================================

@app.route('/asignaturas')
@login_required
@rol_requerido('admin', 'tutor')
def gestion_asignaturas():
    conexion = conectar_db()
    cursor   = conexion.cursor()

    cursor.execute("SELECT id_asignatura, nombre FROM asignaturas ORDER BY nombre ASC")
    asignaturas_raw = cursor.fetchall()

    # Para cada asignatura, obtener sus profesores
    asignaturas = []
    for asig in asignaturas_raw:
        profs = _obtener_profesores_asignatura(cursor, asig['id_asignatura'])
        asignaturas.append({**asig, 'profesores': profs})

    cursor.execute(
        "SELECT id_usuario, nombre, rol FROM usuarios WHERE rol IN ('profesor','tutor','admin') AND activo = TRUE ORDER BY nombre ASC"
    )
    profesores = cursor.fetchall()
    conexion.close()
    return render_template('asignaturas.html', asignaturas=asignaturas, profesores=profesores)


@app.route('/asignaturas/add', methods=['POST'])
@login_required
@rol_requerido('admin', 'tutor')
def add_asignatura():
    nombre       = request.form['nombre'].strip()
    ids_profs    = request.form.getlist('id_profesores')  # lista de IDs

    if not nombre:
        flash('❌ El nombre de la asignatura es obligatorio.', 'danger')
        return redirect(url_for('gestion_asignaturas'))

    conexion = conectar_db()
    cursor   = conexion.cursor()
    try:
        cursor.execute("INSERT INTO asignaturas (nombre) VALUES (%s)", (nombre,))
        id_nueva = cursor.lastrowid
        _actualizar_profesores_asignatura(cursor, id_nueva, [int(i) for i in ids_profs if i])
        conexion.commit()
        flash(f'✅ Asignatura <strong>{_esc(nombre)}</strong> creada correctamente.', 'success')
    except Exception as e:
        flash(f'❌ Error: {e}', 'danger')
    finally:
        conexion.close()
    return redirect(url_for('gestion_asignaturas'))


@app.route('/asignaturas/edit/<int:id_asignatura>', methods=['POST'])
@login_required
@rol_requerido('admin', 'tutor')
def edit_asignatura(id_asignatura):
    nombre    = request.form['nombre'].strip()
    ids_profs = request.form.getlist('id_profesores')

    conexion = conectar_db()
    cursor   = conexion.cursor()
    try:
        cursor.execute("UPDATE asignaturas SET nombre=%s WHERE id_asignatura=%s", (nombre, id_asignatura))
        _actualizar_profesores_asignatura(cursor, id_asignatura, [int(i) for i in ids_profs if i])
        conexion.commit()
        flash('✅ Asignatura actualizada correctamente.', 'success')
    except Exception as e:
        flash(f'❌ Error: {e}', 'danger')
    finally:
        conexion.close()
    return redirect(url_for('gestion_asignaturas'))


@app.route('/asignaturas/delete/<int:id_asignatura>', methods=['POST'])
@login_required
@rol_requerido('admin')
def delete_asignatura(id_asignatura):
    conexion = conectar_db()
    cursor   = conexion.cursor()
    try:
        cursor.execute("DELETE FROM asignaturas WHERE id_asignatura=%s", (id_asignatura,))
        conexion.commit()
        flash('🗑️ Asignatura eliminada.', 'warning')
    except pymysql.err.IntegrityError:
        flash('❌ No se puede eliminar: tiene horarios asignados.', 'danger')
    finally:
        conexion.close()
    return redirect(url_for('gestion_asignaturas'))


# =============================================================================
# GESTIÓN DE HORARIOS (creación agrupada)
# =============================================================================

@app.route('/horarios')
@login_required
def gestion_horarios():
    conexion = conectar_db()
    cursor   = conexion.cursor()

    # Horarios con sus asignaturas
    cursor.execute(
        """
        SELECT h.id_horario, h.dia_semana, h.hora_inicio, h.hora_fin, h.aula,
               a.id_asignatura, a.nombre AS asignatura
        FROM horarios h
        JOIN asignaturas a ON h.id_asignatura = a.id_asignatura
        ORDER BY h.hora_inicio ASC, h.dia_semana ASC
        """
    )
    horarios = cursor.fetchall()

    # Profesores por asignatura (para la leyenda) + siglas + color_map
    cursor.execute("SELECT id_asignatura, nombre FROM asignaturas ORDER BY nombre ASC")
    asignaturas_raw = cursor.fetchall()
    asignaturas = []
    color_map   = {}   # id_asignatura -> indice de color (0-9)
    for idx, asig in enumerate(asignaturas_raw):
        profs  = _obtener_profesores_asignatura(cursor, asig['id_asignatura'])
        siglas = calcular_siglas(asig['nombre'])
        color_map[asig['id_asignatura']] = idx % 10
        asignaturas.append({**asig, 'profesores': profs, 'siglas': siglas})

    # Anotar siglas en cada fila de horario
    siglas_map = {a['id_asignatura']: a['siglas'] for a in asignaturas}
    for h in horarios:
        h['siglas'] = siglas_map.get(h['id_asignatura'], calcular_siglas(h['asignatura']))

    override_id = leer_config(cursor, 'horario_override')
    conexion.close()

    return render_template(
        'horarios.html',
        horarios        = horarios,
        asignaturas     = asignaturas,
        color_map       = color_map,
        dias            = DIAS_SEMANA,
        override_id     = int(override_id) if override_id else None,
    )


@app.route('/horarios/override/<int:id_horario>', methods=['POST'])
@login_required
@rol_requerido('admin')
def toggle_override(id_horario):
    """Activa o desactiva el override manual para una franja horaria concreta.
    Si ya es la franja activa, la desactiva. Si es otra, la activa."""
    conexion = conectar_db()
    cursor   = conexion.cursor()
    actual   = leer_config(cursor, 'horario_override')
    if actual == str(id_horario):
        escribir_config(cursor, 'horario_override', '')   # desactivar
    else:
        escribir_config(cursor, 'horario_override', str(id_horario))  # activar
    conexion.commit()
    conexion.close()
    return redirect(url_for('gestion_horarios'))


@app.route('/horarios/add', methods=['POST'])
@login_required
@rol_requerido('admin', 'tutor')
def add_horario():
    """
    Recibe un JSON con:
      id_asignatura: int
      franjas: [{dia_semana, hora_inicio, hora_fin, aula}, ...]
    """
    datos = request.get_json(force=True)
    id_asignatura = int(datos.get('id_asignatura', 0))
    franjas       = datos.get('franjas', [])

    if not id_asignatura or not franjas:
        return jsonify({'status': 'error', 'mensaje': 'Datos incompletos.'}), 400

    conexion = conectar_db()
    cursor   = conexion.cursor()
    insertados = 0
    errores    = []
    try:
        for f in franjas:
            dia_semana  = int(f.get('dia_semana', -1))
            hora_inicio = f.get('hora_inicio', '').strip()
            hora_fin    = f.get('hora_fin', '').strip()
            aula        = f.get('aula', '').strip()

            if dia_semana < 0 or not hora_inicio or not hora_fin or not aula:
                errores.append(f'Franja incompleta: {f}')
                continue

            try:
                cursor.execute(
                    "INSERT INTO horarios (id_asignatura, dia_semana, hora_inicio, hora_fin, aula) VALUES (%s, %s, %s, %s, %s)",
                    (id_asignatura, dia_semana, hora_inicio, hora_fin, aula)
                )
                insertados += 1
            except pymysql.err.IntegrityError:
                errores.append(f'Ya existe esa franja ({DIAS_SEMANA[dia_semana]} {hora_inicio})')

        conexion.commit()
    except Exception as e:
        conexion.rollback()
        return jsonify({'status': 'error', 'mensaje': str(e)}), 500
    finally:
        conexion.close()

    msg = f'{insertados} franja(s) añadida(s).'
    if errores:
        msg += ' Ignoradas: ' + '; '.join(errores)
    return jsonify({'status': 'ok', 'mensaje': msg, 'insertados': insertados})


@app.route('/horarios/delete/<int:id_horario>', methods=['POST'])
@login_required
@rol_requerido('admin', 'tutor')
def delete_horario(id_horario):
    conexion = conectar_db()
    cursor   = conexion.cursor()
    cursor.execute("DELETE FROM horarios WHERE id_horario=%s", (id_horario,))
    override = leer_config(cursor, 'horario_override')
    if override == str(id_horario):
        escribir_config(cursor, 'horario_override', '')
    conexion.commit()
    conexion.close()
    flash('🗑️ Franja horaria eliminada.', 'warning')
    return redirect(url_for('gestion_horarios'))


# =============================================================================
# GESTIÓN DE USUARIOS (solo admin)
# =============================================================================

@app.route('/usuarios')
@login_required
@rol_requerido('admin')
def gestion_usuarios():
    conexion = conectar_db()
    cursor   = conexion.cursor()
    cursor.execute(
        "SELECT id_usuario, nombre, email, rol, activo, creado_en FROM usuarios ORDER BY creado_en ASC"
    )
    usuarios = cursor.fetchall()
    conexion.close()
    return render_template('usuarios.html', usuarios=usuarios, roles=LABEL_ROL)


@app.route('/usuarios/add', methods=['POST'])
@login_required
@rol_requerido('admin')
def add_usuario():
    nombre     = request.form['nombre'].strip()
    email      = request.form['email'].strip().lower()
    contrasena = request.form['contrasena'].strip()
    rol        = request.form['rol'].strip()

    if rol not in ('admin', 'tutor', 'profesor'):
        flash('❌ Rol no válido.', 'danger')
        return redirect(url_for('gestion_usuarios'))

    error_pass = validar_contrasena(contrasena, nombre)
    if error_pass:
        flash(f'❌ {error_pass}', 'danger')
        return redirect(url_for('gestion_usuarios'))

    password_hash = bcrypt.generate_password_hash(contrasena).decode('utf-8')

    conexion = conectar_db()
    cursor   = conexion.cursor()
    try:
        cursor.execute(
            "INSERT INTO usuarios (nombre, email, password_hash, rol) VALUES (%s, %s, %s, %s)",
            (nombre, email, password_hash, rol)
        )
        conexion.commit()
        flash(f'✅ Usuario <strong>{_esc(nombre)}</strong> creado correctamente.', 'success')
    except pymysql.err.IntegrityError:
        flash(f'❌ El email <code>{_esc(email)}</code> ya está registrado.', 'danger')
    except Exception as e:
        flash(f'❌ Error inesperado: {e}', 'danger')
    finally:
        conexion.close()
    return redirect(url_for('gestion_usuarios'))


@app.route('/usuarios/edit/<int:id_usuario>', methods=['POST'])
@login_required
@rol_requerido('admin')
def edit_usuario(id_usuario):
    """Editar nombre, email y rol de un usuario."""
    nombre = request.form['nombre'].strip()
    email  = request.form['email'].strip().lower()
    rol    = request.form['rol'].strip()

    if rol not in ('admin', 'tutor', 'profesor'):
        flash('❌ Rol no válido.', 'danger')
        return redirect(url_for('gestion_usuarios'))

    # No se puede quitar el rol admin al último admin
    if id_usuario == current_user.id and rol != 'admin':
        flash('❌ No puedes cambiar tu propio rol.', 'danger')
        return redirect(url_for('gestion_usuarios'))

    conexion = conectar_db()
    cursor   = conexion.cursor()
    try:
        cursor.execute(
            "UPDATE usuarios SET nombre=%s, email=%s, rol=%s WHERE id_usuario=%s",
            (nombre, email, rol, id_usuario)
        )
        conexion.commit()
        flash(f'✅ Usuario <strong>{_esc(nombre)}</strong> actualizado.', 'success')
    except pymysql.err.IntegrityError:
        flash(f'❌ El email <code>{_esc(email)}</code> ya está registrado.', 'danger')
    except Exception as e:
        flash(f'❌ Error inesperado: {e}', 'danger')
    finally:
        conexion.close()
    return redirect(url_for('gestion_usuarios'))


@app.route('/usuarios/toggle/<int:id_usuario>', methods=['POST'])
@login_required
@rol_requerido('admin')
def toggle_usuario(id_usuario):
    if id_usuario == current_user.id:
        flash('❌ No puedes desactivar tu propia cuenta.', 'danger')
        return redirect(url_for('gestion_usuarios'))

    conexion = conectar_db()
    cursor   = conexion.cursor()
    cursor.execute("UPDATE usuarios SET activo = NOT activo WHERE id_usuario = %s", (id_usuario,))
    conexion.commit()
    conexion.close()
    flash('↩️ Estado del usuario actualizado.', 'info')
    return redirect(url_for('gestion_usuarios'))


@app.route('/usuarios/delete/<int:id_usuario>', methods=['POST'])
@login_required
@rol_requerido('admin')
def delete_usuario(id_usuario):
    """
    Borrar permanentemente un usuario. Solo admin. Salvaguardas:
      - No te puedes borrar a ti mismo (usa logout/desactivar para eso).
      - No puedes borrar al último admin del sistema (te quedarías sin acceso).
    Las asignaciones a asignaturas se borran en cascada por la FK.
    """
    if id_usuario == current_user.id:
        flash('❌ No puedes borrar tu propia cuenta.', 'danger')
        return redirect(url_for('gestion_usuarios'))

    conexion = conectar_db()
    cursor   = conexion.cursor()

    # Verificar que el usuario existe y obtener su rol
    cursor.execute(
        "SELECT nombre, rol FROM usuarios WHERE id_usuario = %s",
        (id_usuario,)
    )
    objetivo = cursor.fetchone()
    if not objetivo:
        conexion.close()
        flash('❌ El usuario no existe.', 'danger')
        return redirect(url_for('gestion_usuarios'))

    # Si es admin, comprobar que no es el último
    if objetivo['rol'] == 'admin':
        cursor.execute(
            "SELECT COUNT(*) AS n FROM usuarios WHERE rol = 'admin' AND activo = TRUE"
        )
        n_admins = cursor.fetchone()['n']
        if n_admins <= 1:
            conexion.close()
            flash('❌ No se puede borrar al último administrador del sistema.', 'danger')
            return redirect(url_for('gestion_usuarios'))

    try:
        cursor.execute("DELETE FROM usuarios WHERE id_usuario = %s", (id_usuario,))
        conexion.commit()
        flash(f'🗑️ Usuario <strong>{_esc(objetivo["nombre"])}</strong> eliminado permanentemente.', 'success')
    except Exception as e:
        flash(f'❌ Error al borrar el usuario: {e}', 'danger')
    finally:
        conexion.close()
    return redirect(url_for('gestion_usuarios'))


@app.route('/usuarios/cambiar-password', methods=['POST'])
@login_required
def cambiar_password():
    id_objetivo  = int(request.form.get('id_usuario', current_user.id))
    nueva        = request.form.get('nueva_contrasena', '').strip()
    confirmacion = request.form.get('confirmacion', '').strip()

    if id_objetivo != current_user.id and not current_user.es_admin:
        abort(403)

    # Obtener el nombre del usuario para la validación
    _conn_pw = conectar_db()
    _cur_pw  = _conn_pw.cursor()
    _cur_pw.execute("SELECT nombre FROM usuarios WHERE id_usuario = %s", (id_objetivo,))
    _row_pw  = _cur_pw.fetchone()
    _conn_pw.close()
    nombre_destino = _row_pw['nombre'] if _row_pw else ''

    error_pass = validar_contrasena(nueva, nombre_destino)
    if error_pass:
        flash(f'❌ {error_pass}', 'danger')
        return redirect(url_for('gestion_usuarios'))

    if nueva != confirmacion:
        flash('❌ Las contraseñas no coinciden.', 'danger')
        return redirect(url_for('gestion_usuarios'))

    nuevo_hash = bcrypt.generate_password_hash(nueva).decode('utf-8')
    conexion   = conectar_db()
    cursor     = conexion.cursor()
    cursor.execute("UPDATE usuarios SET password_hash = %s WHERE id_usuario = %s", (nuevo_hash, id_objetivo))
    conexion.commit()
    conexion.close()
    flash('✅ Contraseña actualizada correctamente.', 'success')
    return redirect(url_for('gestion_usuarios'))


# =============================================================================
# ESTADO DE FRANJA ACTIVA (para el scheduler inteligente)
# =============================================================================

@app.route('/estado-franja')
def estado_franja():
    """
    Endpoint público consultado por el scheduler antes de cada escaneo.
    Devuelve si hay una franja horaria activa ahora mismo y si el escaneo
    está pausado o en recreo. El scheduler solo lanza /escanear si hay
    franja activa y el sistema no está pausado ni en recreo.

    Autenticación: SCHEDULER_KEY (misma que /escanear) o 127.0.0.1.
    No requiere sesión web.
    """
    if not _es_llamada_scheduler():
        return jsonify({"error": "No autorizado"}), 403

    conexion = conectar_db()
    cursor   = conexion.cursor()

    # ── Comprobar estado del sistema ─────────────────────────────────────────
    pausado = leer_config(cursor, 'escaneo_pausado') == 'true'
    if pausado:
        conexion.close()
        return jsonify({"franja_activa": False, "razon": "escaneo_pausado"})

    # ── Ventana de escaneo configurada por el admin ──────────────────────────
    estado = estado_horario_escaneo(cursor)
    if not estado['dentro_horario']:
        conexion.close()
        return jsonify({"franja_activa": False, "razon": estado['motivo']})

    # ── Frecuencia: respeta el intervalo mínimo entre escaneos ───────────────
    # El cron corre cada minuto pero no debe ejecutar /escanear más a
    # menudo de lo que el admin haya pedido (escaneo_frecuencia_min).
    try:
        frec_min = int(leer_config(cursor, 'escaneo_frecuencia_min') or '10')
    except ValueError:
        frec_min = 10
    ultima_iso = leer_config(cursor, 'escaneo_ultima_ejecucion')
    if ultima_iso:
        try:
            ultima_dt = datetime.fromisoformat(ultima_iso)
            transcurridos = (datetime.now() - ultima_dt).total_seconds() / 60.0
            if transcurridos < frec_min:
                conexion.close()
                return jsonify({
                    "franja_activa":      False,
                    "razon":              "frecuencia",
                    "minutos_transcurridos": round(transcurridos, 1),
                    "frecuencia_min":     frec_min,
                })
        except Exception:
            pass

    # ── Comprobar recreo (mañana y tarde) ────────────────────────────────────
    from datetime import time as dtime
    ahora_t = datetime.now().time()

    def _en_recreo_ef(ini_str, fin_str):
        if not ini_str or not fin_str:
            return False
        try:
            hi = dtime(*[int(x) for x in ini_str.split(':')])
            hf = dtime(*[int(x) for x in fin_str.split(':')])
            return hi <= ahora_t < hf
        except Exception:
            return False

    for rec_ini_key, rec_fin_key in [
        ('recreo_inicio', 'recreo_fin'),
        ('recreo_tarde_inicio', 'recreo_tarde_fin'),
    ]:
        r_ini = leer_config(cursor, rec_ini_key)
        r_fin = leer_config(cursor, rec_fin_key)
        if _en_recreo_ef(r_ini, r_fin):
            conexion.close()
            return jsonify({
                "franja_activa": False,
                "razon":         "recreo",
                "recreo_inicio": r_ini,
                "recreo_fin":    r_fin,
            })

    # ── Comprobar si hay franja horaria activa ahora ─────────────────────────
    horario = obtener_horario_activo(cursor)
    conexion.close()

    if horario:
        return jsonify({
            "franja_activa": True,
            "asignatura":    horario['asignatura'],
            "aula":          horario['aula'],
            "hora_inicio":   str(horario['hora_inicio']),
            "hora_fin":      str(horario['hora_fin']),
        })

    return jsonify({"franja_activa": False, "razon": "sin_franja"})


# =============================================================================
# CONFIGURACIÓN DEL SISTEMA
# =============================================================================

@app.route('/configuracion')
@login_required
@rol_requerido('admin', 'tutor')
def configuracion():
    """Página de configuración general del sistema (solo admin)."""
    conexion = conectar_db()
    cursor   = conexion.cursor()
    cfg = {
        'recreo_inicio':       leer_config(cursor, 'recreo_inicio'),
        'recreo_fin':          leer_config(cursor, 'recreo_fin'),
        'recreo_tarde_inicio': leer_config(cursor, 'recreo_tarde_inicio'),
        'recreo_tarde_fin':    leer_config(cursor, 'recreo_tarde_fin'),
        'nombre_centro': leer_config(cursor, 'nombre_centro') or os.environ.get('NOMBRE_CENTRO', ''),
        # Ventanas de escaneo configurables
        'escaneo_manana_activo':   leer_config(cursor, 'escaneo_manana_activo')   or 'true',
        'escaneo_manana_inicio':   leer_config(cursor, 'escaneo_manana_inicio')   or '08:15',
        'escaneo_manana_fin':      leer_config(cursor, 'escaneo_manana_fin')      or '14:45',
        'escaneo_tarde_activo':    leer_config(cursor, 'escaneo_tarde_activo')    or 'true',
        'escaneo_tarde_inicio':    leer_config(cursor, 'escaneo_tarde_inicio')    or '16:00',
        'escaneo_tarde_fin':       leer_config(cursor, 'escaneo_tarde_fin')       or '21:45',
        'escaneo_frecuencia_min':  leer_config(cursor, 'escaneo_frecuencia_min')  or '10',
    }
    conexion.close()
    return render_template('configuracion.html', cfg=cfg)


@app.route('/configuracion/guardar', methods=['POST'])
@login_required
@rol_requerido('admin', 'tutor')
def guardar_configuracion():
    """Guarda los parámetros editables desde la web."""
    import re
    recreo_inicio       = request.form.get('recreo_inicio', '').strip()
    recreo_fin          = request.form.get('recreo_fin', '').strip()
    recreo_tarde_inicio = request.form.get('recreo_tarde_inicio', '').strip()
    recreo_tarde_fin    = request.form.get('recreo_tarde_fin', '').strip()

    # Ventanas de escaneo
    manana_activo  = '1' if request.form.get('escaneo_manana_activo') else '0'
    manana_inicio  = request.form.get('escaneo_manana_inicio', '').strip()
    manana_fin     = request.form.get('escaneo_manana_fin', '').strip()
    tarde_activo   = '1' if request.form.get('escaneo_tarde_activo') else '0'
    tarde_inicio   = request.form.get('escaneo_tarde_inicio', '').strip()
    tarde_fin      = request.form.get('escaneo_tarde_fin', '').strip()
    frecuencia_raw = request.form.get('escaneo_frecuencia_min', '').strip()

    patron_hora = re.compile(r'^\d{2}:\d{2}$')
    errores = []
    if recreo_inicio and not patron_hora.match(recreo_inicio):
        errores.append('Formato de hora de inicio del recreo de mañana incorrecto (usa HH:MM).')
    if recreo_fin and not patron_hora.match(recreo_fin):
        errores.append('Formato de hora de fin del recreo de mañana incorrecto (usa HH:MM).')
    if recreo_inicio and recreo_fin and recreo_inicio >= recreo_fin:
        errores.append('En el recreo de mañana, la hora de inicio debe ser anterior a la de fin.')
    if recreo_tarde_inicio and not patron_hora.match(recreo_tarde_inicio):
        errores.append('Formato de hora de inicio del recreo de tarde incorrecto (usa HH:MM).')
    if recreo_tarde_fin and not patron_hora.match(recreo_tarde_fin):
        errores.append('Formato de hora de fin del recreo de tarde incorrecto (usa HH:MM).')
    if recreo_tarde_inicio and recreo_tarde_fin and recreo_tarde_inicio >= recreo_tarde_fin:
        errores.append('En el recreo de tarde, la hora de inicio debe ser anterior a la de fin.')

    # Validación de las ventanas de escaneo
    if manana_activo == '1':
        if not patron_hora.match(manana_inicio) or not patron_hora.match(manana_fin):
            errores.append('Las horas del turno de mañana tienen formato incorrecto (usa HH:MM).')
        elif manana_inicio >= manana_fin:
            errores.append('En el turno de mañana, la hora de inicio debe ser anterior a la de fin.')
    if tarde_activo == '1':
        if not patron_hora.match(tarde_inicio) or not patron_hora.match(tarde_fin):
            errores.append('Las horas del turno de tarde tienen formato incorrecto (usa HH:MM).')
        elif tarde_inicio >= tarde_fin:
            errores.append('En el turno de tarde, la hora de inicio debe ser anterior a la de fin.')

    # Frecuencia: entero positivo razonable
    try:
        frecuencia = int(frecuencia_raw)
        if frecuencia < 1 or frecuencia > 240:
            errores.append('La frecuencia de escaneo debe estar entre 1 y 240 minutos.')
    except ValueError:
        errores.append('La frecuencia de escaneo debe ser un número entero de minutos.')
        frecuencia = 10

    if errores:
        for e in errores:
            flash(f'❌ {e}', 'danger')
        return redirect(url_for('configuracion'))

    nombre_centro = request.form.get('nombre_centro', '').strip()

    conexion = conectar_db()
    cursor   = conexion.cursor()
    if recreo_inicio:
        escribir_config(cursor, 'recreo_inicio', recreo_inicio)
    if recreo_fin:
        escribir_config(cursor, 'recreo_fin', recreo_fin)
    # Recreo de tarde (vacío = desactivado)
    escribir_config(cursor, 'recreo_tarde_inicio', recreo_tarde_inicio)
    escribir_config(cursor, 'recreo_tarde_fin',    recreo_tarde_fin)
    escribir_config(cursor, 'nombre_centro', nombre_centro)

    # Guardar ventanas de escaneo. Los valores 'true'/'false' son los que
    # lee estado_horario_escaneo() y leer_config('...') == 'true'.
    escribir_config(cursor, 'escaneo_manana_activo',  'true' if manana_activo == '1' else 'false')
    escribir_config(cursor, 'escaneo_tarde_activo',   'true' if tarde_activo  == '1' else 'false')
    if manana_inicio: escribir_config(cursor, 'escaneo_manana_inicio', manana_inicio)
    if manana_fin:    escribir_config(cursor, 'escaneo_manana_fin',    manana_fin)
    if tarde_inicio:  escribir_config(cursor, 'escaneo_tarde_inicio',  tarde_inicio)
    if tarde_fin:     escribir_config(cursor, 'escaneo_tarde_fin',     tarde_fin)
    escribir_config(cursor, 'escaneo_frecuencia_min', str(frecuencia))

    conexion.commit()
    conexion.close()

    flash('✅ Configuración guardada correctamente.', 'success')
    return redirect(url_for('configuracion'))


@app.route('/configuracion/escaneo', methods=['POST'])
@login_required
@rol_requerido('admin', 'tutor', 'profesor')
def toggle_escaneo():
    datos   = request.get_json(force=True)
    pausado = bool(datos.get('pausado', False))

    conexion = conectar_db()
    cursor   = conexion.cursor()
    escribir_config(cursor, 'escaneo_pausado', 'true' if pausado else 'false')
    conexion.commit()
    conexion.close()
    return jsonify({"status": "ok", "escaneo": 'pausado' if pausado else 'activo'})


# =============================================================================
# BLUETOOTH — DIAGNÓSTICO Y CONTROL MANUAL
# =============================================================================

@app.route('/bt/estado')
@login_required
def bt_estado():
    """
    Devuelve el estado actual del adaptador Bluetooth (hci0).
    Útil para diagnosticar si el BT está activo antes de escanear.
    """
    resultado = subprocess.run(
        ['hciconfig', 'hci0'],
        capture_output=True, text=True
    )
    activo = 'UP' in resultado.stdout or 'RUNNING' in resultado.stdout
    return jsonify({
        'activo':  activo,
        'detalle': resultado.stdout.strip() or resultado.stderr.strip() or 'Sin respuesta de hciconfig',
    })


@app.route('/bt/encender', methods=['POST'])
@login_required
@rol_requerido('admin', 'tutor', 'profesor')
def bt_encender():
    """
    Enciende manualmente el adaptador Bluetooth.
    Permite recuperarlo sin necesidad de lanzar un escaneo completo.
    """
    log.info(f"BT_ENCENDER_MANUAL usuario={current_user.nombre}")
    ok = encender_bt()
    if ok:
        return jsonify({'status': 'ok',    'mensaje': 'Adaptador hci0 encendido correctamente.'})
    else:
        return jsonify({'status': 'error', 'mensaje': 'No se pudo activar hci0. Comprueba que el adaptador BT está conectado.'}), 500


# =============================================================================
# BLUETOOTH DISCOVERY
# =============================================================================

@app.route('/alumnos/discover/start', methods=['POST'])
@login_required
@rol_requerido('admin', 'tutor')
def discover_start():
    """Inicia un escaneo BT en background y devuelve un scan_id para polling."""
    import uuid, asyncio
    import bluetooth
    from bleak import BleakScanner

    conexion = conectar_db()
    cursor   = conexion.cursor()
    cursor.execute("SELECT mac_bluetooth FROM alumnos WHERE mac_bluetooth IS NOT NULL")
    macs_registradas = {row['mac_bluetooth'].upper() for row in cursor.fetchall()}
    conexion.close()

    scan_id = uuid.uuid4().hex
    session = {'dispositivos': {}, 'running': True}
    _discover_sessions[scan_id] = session

    def _background():
        encender_bt()

        # Fase 1: Bluetooth clásico (blocking ~10 s)
        try:
            clasicos = bluetooth.discover_devices(
                duration=10, lookup_names=True, flush_cache=True, lookup_class=False
            )
            for mac, nombre in clasicos:
                if not session['running']:
                    return
                mac_up = mac.upper()
                if mac_up not in macs_registradas:
                    session['dispositivos'][mac_up] = nombre or 'Desconocido'
        except Exception as e:
            print(f"[Discovery] Error clásico: {e}")

        if not session['running']:
            return

        # Fase 2: BLE (contínuo hasta que se detenga o pasen 30 s)
        async def _ble():
            def cb(device, adv):
                if not session['running']:
                    return
                mac_up = device.address.upper()
                if mac_up not in macs_registradas:
                    nombre = adv.local_name or device.name or 'Desconocido'
                    session['dispositivos'][mac_up] = nombre

            async with BleakScanner(detection_callback=cb):
                for _ in range(30):
                    if not session['running']:
                        break
                    await asyncio.sleep(1)

        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(_ble())
            loop.close()
        except Exception as e:
            print(f"[Discovery] Error BLE: {e}")

        session['running'] = False
        print(f"[Discovery] Escaneo {scan_id[:8]} finalizado — {len(session['dispositivos'])} dispositivos")

    threading.Thread(target=_background, daemon=True).start()
    return jsonify({'status': 'ok', 'scan_id': scan_id})


@app.route('/alumnos/discover/poll/<scan_id>', methods=['GET'])
@login_required
@rol_requerido('admin', 'tutor')
def discover_poll(scan_id):
    """Devuelve el estado actual del escaneo: dispositivos encontrados hasta ahora."""
    session = _discover_sessions.get(scan_id)
    if not session:
        return jsonify({'status': 'error', 'mensaje': 'Sesión no encontrada.'}), 404
    dispositivos = [{'mac': mac, 'nombre': nombre}
                    for mac, nombre in session['dispositivos'].items()]
    return jsonify({'status': 'ok', 'running': session['running'], 'dispositivos': dispositivos})


@app.route('/alumnos/discover/stop/<scan_id>', methods=['POST'])
@login_required
@rol_requerido('admin', 'tutor')
def discover_stop(scan_id):
    """Señala al hilo de escaneo que debe detenerse."""
    session = _discover_sessions.get(scan_id)
    if session:
        session['running'] = False
    return jsonify({'status': 'ok'})


@app.route('/alumnos/asignar-mac/<int:id_alumno>', methods=['POST'])
@login_required
@rol_requerido('admin', 'tutor')
def asignar_mac(id_alumno):
    datos = request.get_json(force=True)
    mac   = datos.get('mac', '').strip().upper()

    if not mac:
        return jsonify({'status': 'error', 'mensaje': 'MAC no proporcionada.'}), 400

    conexion = conectar_db()
    cursor   = conexion.cursor()
    try:
        cursor.execute("UPDATE alumnos SET mac_bluetooth = %s WHERE id_alumno = %s", (mac, id_alumno))
        conexion.commit()
        return jsonify({'status': 'ok', 'mensaje': f'MAC {mac} asignada correctamente.'})
    except pymysql.err.IntegrityError:
        return jsonify({'status': 'error', 'mensaje': f'La MAC {mac} ya está registrada.'}), 409
    except Exception as e:
        return jsonify({'status': 'error', 'mensaje': str(e)}), 500
    finally:
        conexion.close()


# =============================================================================
# INFORMES SEMANALES
# =============================================================================

@app.route('/informes')
@login_required
@rol_requerido('admin', 'tutor')
def ver_informes():
    conexion = conectar_db()
    cursor   = conexion.cursor()
    cursor.execute(
        "SELECT id_informe, semana_inicio, semana_fin, generado_en FROM informes ORDER BY semana_inicio DESC"
    )
    informes = cursor.fetchall()
    conexion.close()
    return render_template('informes.html', informes=informes)


@app.route('/informes/<int:id_informe>')
@login_required
@rol_requerido('admin', 'tutor')
def ver_informe(id_informe):
    conexion = conectar_db()
    cursor   = conexion.cursor()
    cursor.execute(
        "SELECT contenido_html, semana_inicio, semana_fin FROM informes WHERE id_informe = %s",
        (id_informe,)
    )
    informe = cursor.fetchone()
    conexion.close()
    if not informe:
        abort(404)
    return informe['contenido_html'], 200, {'Content-Type': 'text/html; charset=utf-8'}


@app.route('/informes/<int:id_informe>/descargar')
@login_required
@rol_requerido('admin', 'tutor')
def descargar_informe(id_informe):
    conexion = conectar_db()
    cursor   = conexion.cursor()
    cursor.execute(
        "SELECT contenido_html, semana_inicio FROM informes WHERE id_informe = %s",
        (id_informe,)
    )
    informe = cursor.fetchone()
    conexion.close()
    if not informe:
        abort(404)
    nombre = f"Informe_Asistencia_{informe['semana_inicio']}.html"
    return Response(
        informe['contenido_html'],
        mimetype='text/html',
        headers={'Content-Disposition': f'attachment; filename={nombre}'}
    )


@app.route('/informes/generar', methods=['POST'])
@login_required
@rol_requerido('admin')
def generar_informe_manual():
    """Generación manual del informe desde la interfaz web (solo admin)."""
    resultado = generar_informe(semana_actual=True)
    if resultado['exito']:
        flash(f"✅ {resultado['mensaje']}", 'success')
        return jsonify({'status': 'ok', 'mensaje': resultado['mensaje'], 'id_informe': resultado['id_informe']}), 200
    else:
        flash(f"⚠️ {resultado['mensaje']}", 'warning')
        return jsonify({'status': 'error', 'mensaje': resultado['mensaje']}), 409


@app.route('/asistencia-total')
@login_required
@rol_requerido('admin', 'tutor')
def asistencia_total():
    """
    Vista de asistencia acumulada para toda la temporada.
    Muestra estadísticas por clase y por día completo (falta total = ausente
    en TODAS las clases de ese día) para cada alumno activo.
    Filtrable por turno (mañana / tarde / todos) vía JS en cliente.
    Se actualiza automáticamente cada viernes al generar el informe semanal.
    """
    conexion = conectar_db()
    cursor   = conexion.cursor()

    # ── Estadísticas por clase ────────────────────────────────────────────────
    cursor.execute(
        """
        SELECT
            al.id_alumno,
            CONCAT(al.apellidos, ', ', al.nombre) AS nombre_completo,
            al.apellidos,
            al.nombre,
            al.grupo,
            al.turno,
            COUNT(a.id_registro)                                      AS total_clases,
            SUM(CASE WHEN a.estado = 'PRESENTE' THEN 1 ELSE 0 END)   AS clases_presentes,
            SUM(CASE WHEN a.estado = 'AUSENTE'  THEN 1 ELSE 0 END)   AS clases_ausentes
        FROM alumnos al
        LEFT JOIN asistencia a ON al.id_alumno = a.id_alumno
        WHERE al.activo = TRUE
        GROUP BY al.id_alumno, al.apellidos, al.nombre, al.grupo, al.turno
        ORDER BY al.apellidos ASC, al.nombre ASC
        """
    )
    stats_clase = {row['id_alumno']: row for row in cursor.fetchall()}

    # ── Estadísticas por día ──────────────────────────────────────────────────
    # Un día cuenta como "falta total" únicamente si el alumno estuvo AUSENTE
    # en TODAS sus clases de ese día (independientemente del turno).
    cursor.execute(
        """
        SELECT
            id_alumno,
            COUNT(DISTINCT fecha)                                               AS dias_con_clases,
            SUM(CASE WHEN clases_presentes = 0 THEN 1 ELSE 0 END)              AS dias_falta_total,
            SUM(CASE WHEN clases_presentes > 0 THEN 1 ELSE 0 END)              AS dias_asistidos
        FROM (
            SELECT
                id_alumno,
                fecha,
                SUM(CASE WHEN estado = 'PRESENTE' THEN 1 ELSE 0 END) AS clases_presentes
            FROM asistencia
            GROUP BY id_alumno, fecha
        ) sub
        GROUP BY id_alumno
        """
    )
    stats_dia = {row['id_alumno']: row for row in cursor.fetchall()}
    conexion.close()

    # ── Combinar ambas fuentes ────────────────────────────────────────────────
    alumnos = []
    for id_alumno, sc in stats_clase.items():
        sd            = stats_dia.get(id_alumno, {})
        total_clases  = sc['total_clases']  or 0
        presentes     = sc['clases_presentes'] or 0
        ausentes      = sc['clases_ausentes']  or 0
        pct           = round(presentes / total_clases * 100) if total_clases > 0 else 0
        alumnos.append({
            'id_alumno':        id_alumno,
            'nombre_completo':  sc['nombre_completo'],
            'grupo':            sc['grupo'],
            'turno':            sc['turno'],
            'total_clases':     total_clases,
            'clases_presentes': presentes,
            'clases_ausentes':  ausentes,
            'pct_asistencia':   pct,
            'dias_con_clases':  sd.get('dias_con_clases',  0) or 0,
            'dias_asistidos':   sd.get('dias_asistidos',   0) or 0,
            'dias_falta_total': sd.get('dias_falta_total', 0) or 0,
        })

    return render_template('asistencia_total.html', alumnos=alumnos)


@app.route('/informes/generar-cron', methods=['POST'])
def generar_informe_cron():
    """
    Endpoint para el scheduler (cron del viernes 23:59).
    Autenticado con X-Scheduler-Key, sin sesión web.
    Genera el informe de la semana actual y purga los registros.
    """
    if not _es_llamada_scheduler():
        return jsonify({"status": "forbidden", "mensaje": "No autorizado."}), 403

    resultado = generar_informe(semana_actual=True)
    if resultado['exito']:
        return jsonify({'status': 'ok', 'mensaje': resultado['mensaje'], 'id_informe': resultado['id_informe']}), 200
    else:
        return jsonify({'status': 'error', 'mensaje': resultado['mensaje']}), 409


# =============================================================================
# ARRANQUE — Encender Bluetooth al iniciar Flask
# =============================================================================

def _init_bluetooth():
    """
    Enciende el adaptador BT al arrancar Flask, no solo cuando llega el primer escaneo.
    Así el sistema queda listo aunque el contenedor se haya reiniciado.
    """
    log.info("INIT_BT Activando adaptador Bluetooth al arrancar...")
    ok = encender_bt()
    if ok:
        log.info("INIT_BT Adaptador hci0 activo y listo.")
    else:
        log.warning("INIT_BT No se pudo activar hci0 al arrancar. Se reintentará en el primer escaneo.")


# Usar with_appcontext para que el log de Flask esté disponible
with app.app_context():
    _init_bluetooth()


# =============================================================================
# PUNTO DE ENTRADA
# =============================================================================

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
