# Construit CUTWEAVE en dossier autonome (dist\CUTWEAVE) avec PyInstaller.
# Prérequis : environnement virtuel avec `pip install -r requirements-lock.txt` puis `pip install -e .[build]`.
# FFmpeg/FFprobe ne sont PAS embarqués : fournir des binaires locaux (PATH, Paramètres, ou dossier ffmpeg\ à côté de l'exe).
param(
    [string]$Python = ".\.venv\Scripts\python.exe",
    [switch]$SkipSmokeTest
)
# PyInstaller journalise sur stderr : les échecs sont détectés par $LASTEXITCODE, pas par ErrorActionPreference.
$ErrorActionPreference = "Continue"
Set-Location (Split-Path -Parent $PSScriptRoot)

& $Python -m PyInstaller --noconfirm --clean --windowed --name CUTWEAVE `
    --paths "$PWD\src" --specpath build `
    --collect-submodules cutweave `
    --icon "$PWD\src\cutweave\ui\resources\cutweave.ico" `
    --add-data "$PWD\src\cutweave\ui\resources;cutweave\ui\resources" `
    --exclude-module tkinter --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtWebEngineWidgets `
    --exclude-module PySide6.QtPdf --exclude-module PySide6.Qt3DCore --exclude-module PySide6.QtQuick3D `
    "$PWD\src\cutweave\__main__.py"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller a échoué ($LASTEXITCODE)." }

& $Python -m PyInstaller --noconfirm --console --name cutweave-cli --paths "$PWD\src" --specpath build --collect-submodules cutweave `
    --exclude-module tkinter --distpath dist\cli-build `
    "$PWD\src\cutweave\cli.py"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller (CLI) a échoué ($LASTEXITCODE)." }
Copy-Item dist\cli-build\cutweave-cli\cutweave-cli.exe dist\CUTWEAVE\ -Force -ErrorAction Stop

if (-not $SkipSmokeTest) {
    # Démarre l'interface hors écran 3 s puis vérifie un code de sortie nul.
    $env:QT_QPA_PLATFORM = "offscreen"
    $env:CUTWEAVE_SMOKE_EXIT_MS = "3000"
    $process = Start-Process -FilePath dist\CUTWEAVE\CUTWEAVE.exe -PassThru -Wait
    Remove-Item Env:CUTWEAVE_SMOKE_EXIT_MS, Env:QT_QPA_PLATFORM
    if ($process.ExitCode -ne 0) { throw "Le test de démarrage de l'exécutable a échoué ($($process.ExitCode))." }
    Write-Host "Test de démarrage réussi : dist\CUTWEAVE\CUTWEAVE.exe"
}
