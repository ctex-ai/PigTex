param(
    [switch]$ReuseWindow,
    [switch]$CheckOnly
)

$projectRoot = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $projectRoot 'backend'
$pythonExe = Join-Path $backendDir 'venv\Scripts\python.exe'
$runFile = Join-Path $backendDir 'run.py'

if (-not (Test-Path -LiteralPath $backendDir)) {
    Write-Error "Backend folder not found: $backendDir"
    exit 1
}

if (-not (Test-Path -LiteralPath $pythonExe)) {
    Write-Error "Backend venv Python not found: $pythonExe"
    exit 1
}

if (-not (Test-Path -LiteralPath $runFile)) {
    Write-Error "Backend entry file not found: $runFile"
    exit 1
}

if ($CheckOnly) {
    Write-Output "Backend launcher OK"
    Write-Output "BackendDir=$backendDir"
    Write-Output "PythonExe=$pythonExe"
    Write-Output "RunFile=$runFile"
    exit 0
}

$escapedBackendDir = $backendDir.Replace("'", "''")
$escapedPythonExe = $pythonExe.Replace("'", "''")
$escapedRunFile = $runFile.Replace("'", "''")

$command = @"
\$Host.UI.RawUI.WindowTitle = 'PigTex Backend'
Set-Location -LiteralPath '$escapedBackendDir'
& '$escapedPythonExe' '$escapedRunFile'
"@

if ($ReuseWindow) {
    Invoke-Expression $command
    exit $LASTEXITCODE
}

Start-Process -FilePath 'powershell.exe' -WorkingDirectory $backendDir -ArgumentList @(
    '-NoExit',
    '-ExecutionPolicy', 'Bypass',
    '-Command', $command
)
