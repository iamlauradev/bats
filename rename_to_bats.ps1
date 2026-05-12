# =============================================================================
# BATS - Script de renombrado: Asistenciator -> BATS / v*.* -> v1.0
# Ejecutar desde PowerShell en el directorio raíz del proyecto:
#   cd C:\Users\laura\Documents\proyecto\bats
#   .\rename_to_bats.ps1
# =============================================================================

$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
if (-not $projectRoot) { $projectRoot = Get-Location }

Write-Host "=== BATS Rename Script ===" -ForegroundColor Cyan
Write-Host "Directorio: $projectRoot" -ForegroundColor Yellow

# ---------------------------------------------------------------------------
# Función helper: reemplaza texto en un fichero (UTF-8, preserva CRLF/LF)
# ---------------------------------------------------------------------------
function Replace-InFile {
    param([string]$FilePath, [hashtable]$Replacements)
    if (-not (Test-Path $FilePath)) {
        Write-Host "  [SKIP] No existe: $FilePath" -ForegroundColor DarkGray
        return
    }
    $content = Get-Content -Path $FilePath -Raw -Encoding UTF8
    $changed = $false
    foreach ($old in $Replacements.Keys) {
        $new = $Replacements[$old]
        if ($content -like "*$old*") {
            $content = $content.Replace($old, $new)
            $changed = $true
        }
    }
    if ($changed) {
        Set-Content -Path $FilePath -Value $content -Encoding UTF8 -NoNewline
        Write-Host "  [OK]   $FilePath" -ForegroundColor Green
    } else {
        Write-Host "  [--]   $FilePath (sin cambios)" -ForegroundColor DarkGray
    }
}

# ---------------------------------------------------------------------------
# Reemplazos comunes (en todos los ficheros de texto del proyecto)
# ---------------------------------------------------------------------------
$commonReplacements = @{
    "ASISTENCIATOR"          = "BATS"
    "Asistenciator"          = "BATS"
    "asistenciator"          = "bats"
}

# Versiones específicas (solo en ficheros que las tengan)
$versionReplacements = @{
    "v2.5" = "v1.0"
    "v2.4" = "v1.0"
    "v2.1" = "v1.0"
}

# ---------------------------------------------------------------------------
# Lista de ficheros Python / Shell / Config a procesar
# ---------------------------------------------------------------------------
$filesToProcess = @(
    "frontend\app.py",
    "frontend\detector.py",
    "frontend\generar_informe.py",
    "frontend\notificaciones\correo.py",
    "frontend\notificaciones\telegram_bot.py",
    "frontend\requirements.txt",
    "docker-compose.yml",
    "scheduler\crontab",
    "scheduler\escaneo.sh",
    "scheduler\informe.sh",
    "db\scripts\init.sql",
    ".env.example",
    "logrotate.conf",
    "scripts\backup.sh",
    "scripts\restaurar.sh",
    "scripts\instalar_cron_backup.sh",
    "README.md"
)

# Plantillas HTML (por si la sesión anterior no las llegó a guardar)
$htmlTemplates = @(
    "frontend\templates\login.html",
    "frontend\templates\base.html",
    "frontend\templates\setup.html",
    "frontend\templates\index.html",
    "frontend\templates\alumnos.html",
    "frontend\templates\informes.html",
    "frontend\templates\configuracion.html",
    "frontend\templates\usuarios.html",
    "frontend\templates\horarios.html",
    "frontend\templates\asignaturas.html",
    "frontend\templates\asistencia_total.html",
    "frontend\templates\error_base.html",
    "frontend\templates\error.html",
    "frontend\templates\400.html",
    "frontend\templates\403.html",
    "frontend\templates\404.html",
    "frontend\templates\429.html",
    "frontend\templates\500.html"
)

Write-Host "`n--- Ficheros Python / Shell / Config ---" -ForegroundColor Cyan
foreach ($relPath in $filesToProcess) {
    $fullPath = Join-Path $projectRoot $relPath
    $allRepl = $commonReplacements.Clone()
    foreach ($k in $versionReplacements.Keys) { $allRepl[$k] = $versionReplacements[$k] }
    Replace-InFile -FilePath $fullPath -Replacements $allRepl
}

Write-Host "`n--- Plantillas HTML ---" -ForegroundColor Cyan
foreach ($relPath in $htmlTemplates) {
    $fullPath = Join-Path $projectRoot $relPath
    $allRepl = $commonReplacements.Clone()
    foreach ($k in $versionReplacements.Keys) { $allRepl[$k] = $versionReplacements[$k] }
    Replace-InFile -FilePath $fullPath -Replacements $allRepl
}

# ---------------------------------------------------------------------------
# Docs SVG / Mermaid
# ---------------------------------------------------------------------------
Write-Host "`n--- Documentos / Diagramas ---" -ForegroundColor Cyan
$docFiles = @(
    "docs\project_architecture_diagram.svg",
    "docs\project_architecture_diagram.mmd",
    "docs\db_diagram.mmd"
)
foreach ($relPath in $docFiles) {
    $fullPath = Join-Path $projectRoot $relPath
    $allRepl = $commonReplacements.Clone()
    foreach ($k in $versionReplacements.Keys) { $allRepl[$k] = $versionReplacements[$k] }
    Replace-InFile -FilePath $fullPath -Replacements $allRepl
}

# ---------------------------------------------------------------------------
# Verificacion final: buscar ocurrencias restantes
# ---------------------------------------------------------------------------
Write-Host "`n--- Verificacion: buscando 'asistenciator'/'ASISTENCIATOR' residuales ---" -ForegroundColor Cyan
$extensions = "*.py","*.sh","*.yml","*.yaml","*.html","*.md","*.sql","*.conf","*.txt","*.mmd","*.svg","*.example"
$found = $false
foreach ($ext in $extensions) {
    Get-ChildItem -Path $projectRoot -Recurse -Filter $ext -File |
        Where-Object { $_.FullName -notmatch "\\.git\\" } |
        ForEach-Object {
            $content = Get-Content $_.FullName -Raw -Encoding UTF8 -ErrorAction SilentlyContinue
            if ($content -match "(?i)asistenciator") {
                Write-Host "  [!] Residuo en: $($_.FullName)" -ForegroundColor Red
                $found = $true
            }
        }
}
if (-not $found) {
    Write-Host "  Sin residuos encontrados." -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# Recordatorio para las imagenes
# ---------------------------------------------------------------------------
Write-Host @"

=== RECORDATORIO - Imagenes ===
Has subido dos imagenes nuevas a frontend\static\img:
  - imagen.png   -> hero del login  (referenciada en login.html)
  - logo.*       -> logo del sidebar (referenciada en base.html y setup.html)

Comprueba en login.html que el <img> del hero apunte a 'imagen.png'
y que el logo de la barra apunte al nombre exacto de tu fichero logo.
Si tras ejecutar este script siguen apuntando a 'bats_logo.png' o similar,
edita manualmente esas dos referencias.
"@ -ForegroundColor Yellow

Write-Host "`n=== Script completado ===" -ForegroundColor Cyan
