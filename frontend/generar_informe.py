#!/usr/bin/env python3
# =============================================================================
# ASISTENCIATOR IoT — Generador de informes semanales v1.0
# =============================================================================
# Ejecutado cada viernes a las 23:59 por el scheduler (cron).
# También puede lanzarse manualmente desde la web (solo admin).
#
# Flujo:
#   1. Calcula el rango de la semana actual (lunes–viernes en curso).
#   2. Consulta todos los registros de asistencia de esa semana.
#   3. Genera un informe HTML compacto y filtrable por alumno.
#   4. Guarda el informe en la tabla 'informes'.
#   5. Borra los registros individuales de asistencia y estado_alumno_dia
#      para mantener la BD ligera.
# =============================================================================

import os
import sys
from datetime import date, datetime, timedelta

import pymysql
import pymysql.cursors

DIAS_ES = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo']


# =============================================================================
# CONEXIÓN A LA BASE DE DATOS
# =============================================================================

def conectar_db() -> pymysql.Connection:
    host     = os.environ.get('DB_HOST',     '127.0.0.1')
    database = os.environ.get('DB_NAME',     'control_asistencia')
    user     = os.environ.get('DB_USER')
    password = os.environ.get('DB_PASSWORD')

    if not user or not password:
        raise RuntimeError("ERROR: DB_USER y/o DB_PASSWORD no están definidas.")

    return pymysql.connect(
        host=host, user=user, password=password, database=database,
        autocommit=False,
        cursorclass=pymysql.cursors.DictCursor
    )


# =============================================================================
# CÁLCULO DEL RANGO SEMANAL
# =============================================================================

def calcular_semana_anterior() -> tuple[date, date]:
    """Lunes y domingo de la semana anterior."""
    hoy        = date.today()
    lunes_hoy  = hoy - timedelta(days=hoy.weekday())
    lunes_ant  = lunes_hoy - timedelta(weeks=1)
    domingo_ant = lunes_ant + timedelta(days=6)
    return lunes_ant, domingo_ant


def calcular_semana_actual() -> tuple[date, date]:
    """Lunes y domingo de la semana actual."""
    hoy    = date.today()
    lunes  = hoy - timedelta(days=hoy.weekday())
    domingo = lunes + timedelta(days=6)
    return lunes, domingo


# =============================================================================
# CONSULTA DE DATOS
# =============================================================================

def obtener_datos_semana(cursor, fecha_inicio: date, fecha_fin: date) -> list[dict]:
    """Registros de asistencia de la semana indicada, ordenados por alumno y fecha."""
    cursor.execute(
        """
        SELECT
            al.id_alumno,
            CONCAT(al.apellidos, ', ', al.nombre) AS alumno,
            al.grupo,
            al.turno,
            a.fecha,
            a.hora_registro,
            a.estado,
            COALESCE(asig.nombre, '—') AS asignatura,
            COALESCE(h.aula,      '—') AS aula
        FROM asistencia a
        JOIN alumnos al            ON a.id_alumno    = al.id_alumno
        LEFT JOIN horarios h       ON a.id_horario   = h.id_horario
        LEFT JOIN asignaturas asig ON h.id_asignatura = asig.id_asignatura
        WHERE a.fecha BETWEEN %s AND %s
        ORDER BY al.apellidos ASC, al.nombre ASC, a.fecha ASC, a.hora_registro ASC
        """,
        (fecha_inicio, fecha_fin)
    )
    return cursor.fetchall()


def agrupar_por_alumno(registros: list[dict]) -> dict:
    """
    Agrupa los registros por alumno.
    Devuelve: {id_alumno: {alumno, grupo, turno, ausencias: [], presencias: int, total: int}}
    """
    agrupado = {}
    for r in registros:
        iid = r['id_alumno']
        if iid not in agrupado:
            agrupado[iid] = {
                'alumno':     r['alumno'],
                'grupo':      r['grupo'],
                'turno':      r.get('turno', 'ambos'),
                'ausencias':  [],
                'presencias': 0,
                'total':      0,
            }
        agrupado[iid]['total'] += 1
        if r['estado'] == 'AUSENTE':
            agrupado[iid]['ausencias'].append(r)
        else:
            agrupado[iid]['presencias'] += 1

    return agrupado


# =============================================================================
# GENERACIÓN DEL HTML (informe compacto y filtrable)
# =============================================================================

def generar_html(fecha_inicio: date, fecha_fin: date,
                 agrupado: dict, nombre_centro: str) -> str:
    """
    Genera el HTML del informe semanal: tabla compacta filtrable por alumno.
    """
    fmt_fecha   = lambda d: d.strftime('%d/%m/%Y')
    generado_en = datetime.now().strftime('%d/%m/%Y a las %H:%M')

    total_alumnos      = len(agrupado)
    total_ausencias    = sum(len(v['ausencias']) for v in agrupado.values())
    alumnos_sin_faltas = sum(1 for v in agrupado.values() if not v['ausencias'])

    # Calcular el total global de clases registradas (para % global)
    total_clases_global = sum(v['total'] for v in agrupado.values())

    # Construir filas de la tabla
    filas_tabla = ''
    for datos in sorted(agrupado.values(), key=lambda x: x['alumno']):
        n_aus   = len(datos['ausencias'])
        n_pres  = datos['presencias']
        n_total = datos['total']
        pct_pres = round((n_pres / n_total * 100), 1) if n_total else 0
        pct_aus  = round((n_aus  / n_total * 100), 1) if n_total else 0

        turno_badge = {
            'mañana': '<span style="background:#fef3c7;color:#b45309;padding:2px 8px;border-radius:4px;font-size:0.7rem;font-weight:600;">☀ Mañana</span>',
            'tarde':  '<span style="background:#ede9fe;color:#6d28d9;padding:2px 8px;border-radius:4px;font-size:0.7rem;font-weight:600;">◑ Tarde</span>',
            'ambos':  '<span style="background:#e0f2fe;color:#0369a1;padding:2px 8px;border-radius:4px;font-size:0.7rem;font-weight:600;">◈ Ambos</span>',
        }.get(datos.get('turno', 'ambos'), '')

        color_pct = '#15803d' if pct_pres >= 80 else ('#b45309' if pct_pres >= 60 else '#b91c1c')

        # Detalle de ausencias expandible
        detalle = ''
        for aus in datos['ausencias']:
            dia_str = f"{DIAS_ES[aus['fecha'].weekday()]} {fmt_fecha(aus['fecha'])}"
            detalle += f"""
                <tr style="background:#fff8f8;">
                    <td style="padding:4px 10px;font-size:0.75rem;color:#64748b;">{dia_str}</td>
                    <td style="padding:4px 10px;font-size:0.75rem;color:#64748b;">{aus['asignatura']}</td>
                    <td style="padding:4px 10px;font-size:0.75rem;color:#64748b;">{aus['aula']}</td>
                    <td style="padding:4px 10px;font-size:0.75rem;color:#64748b;">{aus['hora_registro']}</td>
                </tr>"""

        detalle_html = ''
        if detalle:
            detalle_html = f"""
            <tr class="detalle-row" style="display:none;background:#fff8f8;">
                <td colspan="7" style="padding:0 16px 10px;">
                    <table width="100%" style="border-collapse:collapse;margin-top:4px;border-radius:6px;overflow:hidden;">
                        <tr style="background:#fee2e2;">
                            <th style="padding:5px 10px;font-size:0.7rem;text-align:left;color:#64748b;font-weight:600;">Día</th>
                            <th style="padding:5px 10px;font-size:0.7rem;text-align:left;color:#64748b;font-weight:600;">Clase</th>
                            <th style="padding:5px 10px;font-size:0.7rem;text-align:left;color:#64748b;font-weight:600;">Aula</th>
                            <th style="padding:5px 10px;font-size:0.7rem;text-align:left;color:#64748b;font-weight:600;">Hora</th>
                        </tr>
                        {detalle}
                    </table>
                </td>
            </tr>"""

        toggle = f'onclick="toggleDetalle(this)" style="cursor:pointer;" title="Ver ausencias"' if detalle else ''

        filas_tabla += f"""
        <tr class="alumno-row" data-nombre="{datos['alumno'].lower()}" data-turno="{datos.get('turno','ambos')}" {toggle}>
            <td style="padding:10px 14px;font-size:0.85rem;font-weight:600;color:#1e293b;">
                {datos['alumno']}
                {'<i class="expand-icon"> ▸</i>' if detalle else ''}
            </td>
            <td style="padding:10px 14px;font-size:0.8rem;color:#64748b;">{datos['grupo']}</td>
            <td style="padding:10px 14px;">{turno_badge}</td>
            <td style="padding:10px 14px;text-align:center;font-size:0.9rem;font-weight:700;color:#15803d;">{n_pres}</td>
            <td style="padding:10px 14px;text-align:center;font-size:0.9rem;font-weight:700;color:{'#b91c1c' if n_aus > 0 else '#94a3b8'};">{n_aus}</td>
            <td style="padding:10px 14px;text-align:center;font-size:0.78rem;color:#64748b;">{n_total}</td>
            <td style="padding:10px 14px;text-align:center;">
                <span style="font-size:0.82rem;font-weight:700;color:{color_pct};">{pct_pres}%</span>
                <div style="height:4px;background:#e2e8f0;border-radius:2px;margin-top:3px;width:60px;margin-left:auto;margin-right:auto;">
                    <div style="height:100%;width:{pct_pres}%;background:{color_pct};border-radius:2px;"></div>
                </div>
            </td>
        </tr>
        {detalle_html}"""

    if not filas_tabla:
        filas_tabla = """
        <tr>
            <td colspan="7" style="padding:32px;text-align:center;color:#94a3b8;font-size:0.85rem;">
                No hay registros de asistencia para esta semana.
            </td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Informe de Asistencia — {fmt_fecha(fecha_inicio)} al {fmt_fecha(fecha_fin)}</title>
    <style>
        * {{ box-sizing: border-box; }}
        body {{ margin:0;padding:0;font-family:'Segoe UI',system-ui,sans-serif;background:#f1f5f9;color:#1e293b; }}
        .container {{ max-width:920px;margin:28px auto;padding:0 16px; }}
        .card {{ background:#fff;border-radius:10px;border:1px solid #e2e8f0;box-shadow:0 1px 4px rgba(0,0,0,0.05);overflow:hidden;margin-bottom:18px; }}
        table {{ border-collapse:collapse;width:100%; }}
        th {{ font-size:0.72rem;text-transform:uppercase;letter-spacing:0.06em;color:#94a3b8;
              font-weight:600;padding:10px 14px;background:#f8fafc;border-bottom:2px solid #e2e8f0;text-align:left; }}
        tr:not(:last-child) td {{ border-bottom:1px solid #f1f5f9; }}
        .alumno-row:hover {{ background:#f8fafc; }}
        .stat {{ text-align:center;padding:16px 20px; }}
        .stat-val {{ font-size:1.8rem;font-weight:700;margin-bottom:2px; }}
        .stat-lbl {{ font-size:0.73rem;color:#64748b; }}
        input[type=search], select {{
            padding:7px 12px;border:1px solid #e2e8f0;border-radius:6px;
            font-size:0.85rem;background:#fff;outline:none;
        }}
        input[type=search]:focus, select:focus {{ border-color:#60a5fa; }}
        .filter-bar {{ padding:14px 18px;border-bottom:1px solid #e2e8f0;display:flex;gap:10px;flex-wrap:wrap;align-items:center; }}
    </style>
</head>
<body>
<div class="container">

    <!-- Cabecera -->
    <div class="card">
        <div style="background:#0f172a;padding:20px 24px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;">
            <div>
                <p style="margin:0;font-size:1rem;font-weight:700;color:#60a5fa;">
                    📡 Asistenciator — Informe Semanal
                </p>
                <p style="margin:2px 0 0;font-size:0.78rem;color:#64748b;">{nombre_centro}</p>
            </div>
            <div style="text-align:right;">
                <p style="margin:0;font-size:0.85rem;color:#e2e8f0;">
                    Semana del <strong>{fmt_fecha(fecha_inicio)}</strong> al <strong>{fmt_fecha(fecha_fin)}</strong>
                </p>
                <p style="margin:2px 0 0;font-size:0.72rem;color:#64748b;">Generado el {generado_en}</p>
            </div>
        </div>
    </div>

    <!-- Estadísticas -->
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:12px;margin-bottom:18px;">
        <div class="card stat">
            <div class="stat-val" style="color:#0f172a;">{total_alumnos}</div>
            <div class="stat-lbl">Alumnos</div>
        </div>
        <div class="card stat">
            <div class="stat-val" style="color:#15803d;">{alumnos_sin_faltas}</div>
            <div class="stat-lbl">Sin faltas</div>
        </div>
        <div class="card stat">
            <div class="stat-val" style="color:#b91c1c;">{total_ausencias}</div>
            <div class="stat-lbl">Ausencias (clases)</div>
        </div>
        <div class="card stat">
            <div class="stat-val" style="color:#0369a1;">{total_clases_global}</div>
            <div class="stat-lbl">Total clases registradas</div>
        </div>
    </div>

    <!-- Tabla de alumnos -->
    <div class="card">
        <div class="filter-bar">
            <input type="search" id="buscarAlumno" placeholder="🔍 Buscar alumno…"
                   oninput="filtrar()" style="flex:1;min-width:180px;max-width:300px;">
            <select id="filtroTurno" onchange="filtrar()">
                <option value="">Todos los turnos</option>
                <option value="mañana">☀ Mañana</option>
                <option value="tarde">◑ Tarde</option>
                <option value="ambos">◈ Ambos</option>
            </select>
            <select id="filtroFaltas" onchange="filtrar()">
                <option value="">Todos</option>
                <option value="con">Con faltas</option>
                <option value="sin">Sin faltas</option>
            </select>
            <span id="contadorFiltro" style="font-size:0.78rem;color:#94a3b8;margin-left:auto;"></span>
        </div>
        <table>
            <thead>
                <tr>
                    <th>Alumno/a</th>
                    <th>Grupo</th>
                    <th>Turno</th>
                    <th style="text-align:center;">✅ Clases<br>presentes</th>
                    <th style="text-align:center;">❌ Clases<br>ausente</th>
                    <th style="text-align:center;">Total<br>clases</th>
                    <th style="text-align:center;">% Asistencia</th>
                </tr>
            </thead>
            <tbody id="tbodyAlumnos">
                {filas_tabla}
            </tbody>
        </table>
    </div>

    <p style="text-align:center;font-size:0.7rem;color:#94a3b8;margin-top:8px;">
        Generado automáticamente por Asistenciator · {nombre_centro}
    </p>
</div>

<script>
function filtrar() {{
    const q      = document.getElementById('buscarAlumno').value.toLowerCase();
    const turno  = document.getElementById('filtroTurno').value;
    const faltas = document.getElementById('filtroFaltas').value;
    const filas  = document.querySelectorAll('#tbodyAlumnos .alumno-row');
    let visibles = 0;

    filas.forEach(fila => {{
        const nombre = fila.dataset.nombre || '';
        const fTurno = fila.dataset.turno  || '';
        const tds    = fila.querySelectorAll('td');
        // td index 4 = nº ausencias
        const ausNum = parseInt(tds[4]?.textContent?.trim() || '0');

        let ok = true;
        if (q     && !nombre.includes(q))                          ok = false;
        if (turno && fTurno !== turno)                             ok = false;
        if (faltas === 'con' && ausNum === 0)                      ok = false;
        if (faltas === 'sin' && ausNum > 0)                        ok = false;

        fila.style.display = ok ? '' : 'none';
        // También ocultar la fila de detalle si el alumno está oculto
        const detalle = fila.nextElementSibling;
        if (detalle && detalle.classList.contains('detalle-row')) {{
            detalle.style.display = 'none';
        }}
        if (ok) visibles++;
    }});

    const cnt = document.getElementById('contadorFiltro');
    cnt.textContent = visibles === filas.length ? `${{filas.length}} alumnos` : `${{visibles}} / ${{filas.length}} alumnos`;
}}

function toggleDetalle(fila) {{
    const detalle = fila.nextElementSibling;
    if (!detalle || !detalle.classList.contains('detalle-row')) return;
    const visible = detalle.style.display !== 'none';
    detalle.style.display = visible ? 'none' : 'table-row';
    const icon = fila.querySelector('.expand-icon');
    if (icon) icon.textContent = visible ? ' ▸' : ' ▾';
}}

// Inicializar contador
document.addEventListener('DOMContentLoaded', () => {{
    const cnt   = document.getElementById('contadorFiltro');
    const total = document.querySelectorAll('#tbodyAlumnos .alumno-row').length;
    cnt.textContent = `${{total}} alumnos`;
}});
</script>
</body>
</html>"""


# =============================================================================
# LIMPIEZA DE REGISTROS
# =============================================================================

def limpiar_registros_semana(cursor, fecha_inicio: date, fecha_fin: date) -> int:
    """
    Borra los registros de asistencia y estado_alumno_dia de la semana.
    Devuelve el número de registros de asistencia eliminados.
    """
    cursor.execute(
        "DELETE FROM asistencia WHERE fecha BETWEEN %s AND %s",
        (fecha_inicio, fecha_fin)
    )
    borrados = cursor.rowcount

    cursor.execute(
        "DELETE FROM estado_alumno_dia WHERE fecha BETWEEN %s AND %s",
        (fecha_inicio, fecha_fin)
    )

    return borrados


# =============================================================================
# FUNCIÓN PRINCIPAL
# =============================================================================

def generar_informe(semana_actual: bool = False) -> dict:
    """
    Genera el informe semanal completo.

    Args:
        semana_actual: True → semana en curso (generación manual o viernes 23:59).
                       False → semana anterior (compatibilidad con versiones antiguas).

    Returns:
        dict con: exito (bool), mensaje (str), id_informe (int|None)
    """
    # Nombre del centro desde la BD (fallback a variable de entorno)
    _nc_conn   = conectar_db()
    _nc_cur    = _nc_conn.cursor()
    _nc_cur.execute("SELECT valor FROM configuracion WHERE clave='nombre_centro' LIMIT 1")
    _nc_row    = _nc_cur.fetchone()
    _nc_conn.close()
    nombre_centro = ((_nc_row['valor'] if isinstance(_nc_row, dict) else _nc_row[0]) if _nc_row else None) \
                    or os.environ.get('NOMBRE_CENTRO', 'Centro educativo')

    fecha_inicio, fecha_fin = calcular_semana_actual() if semana_actual else calcular_semana_anterior()
    print(f"\n[Informe] Generando informe del {fecha_inicio} al {fecha_fin}...")

    conexion = conectar_db()
    cursor   = conexion.cursor()

    try:
        cursor.execute(
            "SELECT id_informe FROM informes WHERE semana_inicio = %s",
            (fecha_inicio,)
        )
        if cursor.fetchone():
            msg = f"Ya existe un informe para la semana del {fecha_inicio}."
            print(f"[Informe] ⚠️  {msg}")
            return {'exito': False, 'mensaje': msg, 'id_informe': None}

        registros = obtener_datos_semana(cursor, fecha_inicio, fecha_fin)
        agrupado  = agrupar_por_alumno(registros)

        print(f"[Informe] {len(registros)} registros, {len(agrupado)} alumnos.")

        html = generar_html(fecha_inicio, fecha_fin, agrupado, nombre_centro)

        cursor.execute(
            """
            INSERT INTO informes (semana_inicio, semana_fin, generado_en, contenido_html)
            VALUES (%s, %s, NOW(), %s)
            """,
            (fecha_inicio, fecha_fin, html)
        )
        id_informe = cursor.lastrowid

        borrados = limpiar_registros_semana(cursor, fecha_inicio, fecha_fin)
        print(f"[Informe] {borrados} registros de asistencia eliminados.")

        conexion.commit()

        msg = (f"Informe generado para la semana del "
               f"{fecha_inicio.strftime('%d/%m/%Y')} al {fecha_fin.strftime('%d/%m/%Y')}.")
        print(f"[Informe] ✅ {msg}")
        return {'exito': True, 'mensaje': msg, 'id_informe': id_informe}

    except Exception as e:
        conexion.rollback()
        msg = f"Error al generar el informe: {e}"
        print(f"[Informe] ❌ {msg}")
        return {'exito': False, 'mensaje': msg, 'id_informe': None}

    finally:
        conexion.close()


# =============================================================================
# PUNTO DE ENTRADA (ejecución desde cron o terminal)
# =============================================================================

if __name__ == '__main__':
    semana_actual = '--semana-actual' in sys.argv
    resultado = generar_informe(semana_actual=semana_actual)
    sys.exit(0 if resultado['exito'] else 1)
