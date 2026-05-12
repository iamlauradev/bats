-- =============================================================================
-- BATS IoT — Esquema de base de datos v1.0
-- =============================================================================
-- Autora  : Laura Linares — iamlaura.dev
-- Motor   : MariaDB 10.11
-- Charset : utf8mb4
-- =============================================================================

USE control_asistencia;

-- =============================================================================
-- 1. USUARIOS
-- =============================================================================
CREATE TABLE IF NOT EXISTS usuarios (
    id_usuario     INT           NOT NULL AUTO_INCREMENT,
    nombre         VARCHAR(150)  NOT NULL,
    email          VARCHAR(200)  NOT NULL,
    password_hash  VARCHAR(255)  NOT NULL,
    rol            ENUM('admin','tutor','profesor')
                   NOT NULL DEFAULT 'profesor',
    activo         BOOLEAN       NOT NULL DEFAULT TRUE,
    creado_en      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id_usuario),
    UNIQUE KEY uk_usuarios_email (email)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- =============================================================================
-- 2. ASIGNATURAS
-- =============================================================================
CREATE TABLE IF NOT EXISTS asignaturas (
    id_asignatura  INT           NOT NULL AUTO_INCREMENT,
    nombre         VARCHAR(150)  NOT NULL,
    PRIMARY KEY (id_asignatura)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- =============================================================================
-- 2b. ASIGNATURA_PROFESORES (N:M)
-- =============================================================================
CREATE TABLE IF NOT EXISTS asignatura_profesores (
    id              INT NOT NULL AUTO_INCREMENT,
    id_asignatura   INT NOT NULL,
    id_usuario      INT NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT fk_ap_asig
        FOREIGN KEY (id_asignatura) REFERENCES asignaturas(id_asignatura)
        ON DELETE CASCADE,
    CONSTRAINT fk_ap_usuario
        FOREIGN KEY (id_usuario) REFERENCES usuarios(id_usuario)
        ON DELETE CASCADE,
    UNIQUE KEY uk_ap (id_asignatura, id_usuario)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- =============================================================================
-- 3. HORARIOS
-- =============================================================================
CREATE TABLE IF NOT EXISTS horarios (
    id_horario     INT           NOT NULL AUTO_INCREMENT,
    id_asignatura  INT           NOT NULL,
    dia_semana     TINYINT       NOT NULL CHECK (dia_semana BETWEEN 0 AND 4),
    hora_inicio    TIME          NOT NULL,
    hora_fin       TIME          NOT NULL,
    aula           VARCHAR(50)   NOT NULL,
    PRIMARY KEY (id_horario),
    CONSTRAINT fk_horario_asig
        FOREIGN KEY (id_asignatura) REFERENCES asignaturas(id_asignatura)
        ON DELETE CASCADE,
    UNIQUE KEY uk_horario_unico (id_asignatura, dia_semana, hora_inicio)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE INDEX IF NOT EXISTS idx_horarios_dia_hora
    ON horarios (dia_semana, hora_inicio, hora_fin);


-- =============================================================================
-- 4. ALUMNOS
-- =============================================================================
CREATE TABLE IF NOT EXISTS alumnos (
    id_alumno      INT           NOT NULL AUTO_INCREMENT,
    nombre         VARCHAR(100)  NOT NULL,
    apellidos      VARCHAR(150)  NOT NULL,
    mac_bluetooth  VARCHAR(17)   NULL DEFAULT NULL,   -- Opcional
    grupo          VARCHAR(50)   NOT NULL,
    turno          ENUM('mañana','tarde','ambos')
                   NOT NULL DEFAULT 'ambos',          -- Turno escolar
    email_tutor    VARCHAR(200)  NULL DEFAULT NULL,   -- Opcional
    telefono_tutor VARCHAR(20)   NULL DEFAULT NULL,   -- Opcional
    activo         BOOLEAN       NOT NULL DEFAULT TRUE,
    creado_en      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id_alumno),
    UNIQUE KEY uk_alumnos_mac (mac_bluetooth)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- =============================================================================
-- 5. MATRICULAS
-- =============================================================================
CREATE TABLE IF NOT EXISTS matriculas (
    id_matricula   INT  NOT NULL AUTO_INCREMENT,
    id_alumno      INT  NOT NULL,
    id_asignatura  INT  NOT NULL,
    PRIMARY KEY (id_matricula),
    CONSTRAINT fk_mat_alumno
        FOREIGN KEY (id_alumno) REFERENCES alumnos(id_alumno)
        ON DELETE CASCADE,
    CONSTRAINT fk_mat_asig
        FOREIGN KEY (id_asignatura) REFERENCES asignaturas(id_asignatura)
        ON DELETE CASCADE,
    UNIQUE KEY uk_matricula_unica (id_alumno, id_asignatura)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- =============================================================================
-- 6. ASISTENCIA
-- =============================================================================
CREATE TABLE IF NOT EXISTS asistencia (
    id_registro    INT          NOT NULL AUTO_INCREMENT,
    id_alumno      INT          NOT NULL,
    id_horario     INT,
    fecha          DATE         NOT NULL,
    hora_registro  TIME         NOT NULL,
    estado         ENUM('PRESENTE','AUSENTE') NOT NULL,
    PRIMARY KEY (id_registro),
    CONSTRAINT fk_asist_alumno
        FOREIGN KEY (id_alumno) REFERENCES alumnos(id_alumno)
        ON DELETE CASCADE,
    CONSTRAINT fk_asist_horario
        FOREIGN KEY (id_horario) REFERENCES horarios(id_horario)
        ON DELETE SET NULL,
    UNIQUE KEY uk_asist_unica (id_alumno, id_horario, fecha)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE INDEX IF NOT EXISTS idx_asist_fecha
    ON asistencia (fecha);
CREATE INDEX IF NOT EXISTS idx_asist_alumno_fecha
    ON asistencia (id_alumno, fecha);


-- =============================================================================
-- 7. ESTADO_ALUMNO_DIA
-- =============================================================================
CREATE TABLE IF NOT EXISTS estado_alumno_dia (
    id                   INT       NOT NULL AUTO_INCREMENT,
    id_alumno            INT       NOT NULL,
    fecha                DATE      NOT NULL,
    estado_actual        ENUM('PRESENTE','AUSENTE') NOT NULL,
    notificado           BOOLEAN   NOT NULL DEFAULT FALSE,
    ultima_actualizacion DATETIME  NOT NULL DEFAULT CURRENT_TIMESTAMP
                                   ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    CONSTRAINT fk_estado_alumno
        FOREIGN KEY (id_alumno) REFERENCES alumnos(id_alumno)
        ON DELETE CASCADE,
    UNIQUE KEY uk_estado_dia (id_alumno, fecha)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE INDEX IF NOT EXISTS idx_estado_fecha
    ON estado_alumno_dia (fecha);


-- =============================================================================
-- 8. INFORMES
-- =============================================================================
CREATE TABLE IF NOT EXISTS informes (
    id_informe     INT          NOT NULL AUTO_INCREMENT,
    semana_inicio  DATE         NOT NULL,
    semana_fin     DATE         NOT NULL,
    generado_en    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    contenido_html MEDIUMTEXT,
    PRIMARY KEY (id_informe),
    UNIQUE KEY uk_informe_semana (semana_inicio)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- =============================================================================
-- 9. CONFIGURACION
-- =============================================================================
CREATE TABLE IF NOT EXISTS configuracion (
    id      INT          NOT NULL AUTO_INCREMENT,
    clave   VARCHAR(100) NOT NULL,
    valor   VARCHAR(255) NOT NULL DEFAULT '',
    PRIMARY KEY (id),
    UNIQUE KEY uk_config_clave (clave)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- =============================================================================
-- DATOS INICIALES
-- =============================================================================
INSERT INTO configuracion (clave, valor) VALUES
    ('escaneo_pausado',         'false'),
    ('horario_override',        ''),
    ('recreo_inicio',           '11:15'),
    ('recreo_fin',              '11:45'),
    ('nombre_centro',           ''),
    -- Ventanas de escaneo configurables por el admin
    ('escaneo_manana_activo',   'true'),
    ('escaneo_manana_inicio',   '08:15'),
    ('escaneo_manana_fin',      '14:45'),
    ('escaneo_tarde_activo',    'true'),
    ('escaneo_tarde_inicio',    '16:00'),
    ('escaneo_tarde_fin',       '21:45'),
    -- Frecuencia entre escaneos automáticos (en minutos)
    ('escaneo_frecuencia_min',  '10'),
    -- Timestamp ISO del último escaneo (lo actualiza /escanear)
    ('escaneo_ultima_ejecucion', '')
ON DUPLICATE KEY UPDATE valor = VALUES(valor);


-- =============================================================================
-- PERMISOS
-- =============================================================================
GRANT SELECT, INSERT, UPDATE, DELETE
    ON control_asistencia.*
    TO 'bats_app'@'%';

FLUSH PRIVILEGES;
