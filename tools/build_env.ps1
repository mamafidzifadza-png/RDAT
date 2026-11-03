# tools\build_env.ps1 — build dengan MSVC BuildTools 18.
#
# Kenapa skrip ini ada: BuildTools 18 nggak mendaftar di
# HKLM\SOFTWARE\Microsoft\VisualStudio\SxS\VS7, jadi Nuitka nggak nemu MSVC
# sendiri. Yang dibaca Nuitka adalah VSCMD_ARG_TGT_ARCH + VCToolsInstallDir,
# dan dua-duanya cuma diisi oleh vcvars64.bat.
#
# Pakai:
#   .\tools\build_env.ps1 --token "<token>" --channel 123 --name SysUpdate --low-memory
#
# Semua argumen diteruskan apa adanya ke builder.py.

param([Parameter(ValueFromRemainingArguments = $true)][string[]]$BuilderArgs)

$ErrorActionPreference = "Continue"
$root = Split-Path $PSScriptRoot -Parent

$vc = 'C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Auxiliary\Build\vcvars64.bat'
if (Test-Path $vc) {
    cmd /c "`"$vc`" >nul 2>&1 && set" | ForEach-Object {
        if ($_ -match '^([^=]+)=(.*)$') { Set-Item -Path ("env:" + $matches[1]) -Value $matches[2] }
    }
    Write-Host "[*] vcvars    : $env:VSCMD_ARG_TGT_ARCH / $env:VCToolsInstallDir"
} else {
    Write-Host "[!] vcvars64.bat nggak ketemu, Nuitka bakal gagal deteksi MSVC"
}

$env:PYTHONIOENCODING = "utf-8"
$py = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'
if (-not (Test-Path $py)) { $py = "python" }

Set-Location $root
& $py "builder.py" @BuilderArgs
exit $LASTEXITCODE
