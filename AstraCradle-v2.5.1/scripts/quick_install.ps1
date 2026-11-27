# AstraCradle 1-Line Remote Installer (Windows 10/11 PowerShell)
# Usage: irm https://raw.githubusercontent.com/jackbashroot-boop/AstraCradle/main/scripts/quick_install.ps1 | iex

$ErrorActionPreference = "Stop"
$RepoOwner = "jackbashroot-boop"
$RepoName = "AstraCradle"
$Branch = "main"
$ZipUrl = "https://github.com/$RepoOwner/$RepoName/archive/refs/heads/$Branch.zip"
$InstallRoot = Join-Path $env:USERPROFILE "AstraCradle"
$TmpDir = Join-Path $env:TEMP ("astracradle-install-" + [guid]::NewGuid().ToString("N"))

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  AstraCradle 1-Step Installer ($RepoOwner/$RepoName)" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

New-Item -ItemType Directory -Force -Path $TmpDir | Out-Null
try {
    $ZipPath = Join-Path $TmpDir "astracradle.zip"
    Write-Host "[1/3] Downloading latest AstraCradle from GitHub..."
    Invoke-WebRequest -Uri $ZipUrl -OutFile $ZipPath -UseBasicParsing
    Unblock-File -Path $ZipPath -ErrorAction SilentlyContinue

    Write-Host "[2/3] Extracting into $InstallRoot..."
    Expand-Archive -Path $ZipPath -DestinationPath $TmpDir -Force
    $Extracted = Get-ChildItem -Path $TmpDir -Directory | Select-Object -First 1
    if (-not $Extracted) {
        throw "Failed to locate extracted AstraCradle folder."
    }

    if (-not (Test-Path $InstallRoot)) {
        New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null
    }
    Get-ChildItem -Path $Extracted.FullName -Force | ForEach-Object {
        $Dest = Join-Path $InstallRoot $_.Name
        if ($_.PSIsContainer -and $_.Name -eq ".venv") { return }
        Copy-Item -Path $_.FullName -Destination $Dest -Recurse -Force
    }

    Write-Host "[3/3] Running Install_AstraCradle.bat..."
    Set-Location $InstallRoot
    & cmd.exe /c "Install_AstraCradle.bat"
    if ($LASTEXITCODE -ne 0) {
        throw "Install_AstraCradle.bat exited with code $LASTEXITCODE."
    }
}
finally {
    if (Test-Path $TmpDir) {
        Remove-Item -Path $TmpDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}
