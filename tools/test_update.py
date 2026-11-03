"""tools/test_update.py — buktikan mekanisme swap jalan, tanpa Discord.

Yang diuji nyata, bukan simulasi:

  1. exe tiruan benar-benar dijalankan (jadi file-nya terkunci Windows)
  2. skrip swap ditulis dan dijalankan terpisah
  3. skrip menunggu lock lepas, lalu menimpa
  4. build baru dijalankan lagi
  5. skrip menghapus dirinya sendiri

Plus tiga penolakan yang wajib: file teks, file kekecilan, sha256 salah.

Pakai:
    python tools\\test_update.py
"""

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from core import update  # noqa: E402

NO_WINDOW = 0x08000000


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(262144), b""):
            h.update(chunk)
    return h.hexdigest()


def wait_for(predicate, seconds, step=0.5):
    end = time.time() + seconds
    while time.time() < end:
        if predicate():
            return True
        time.sleep(step)
    return False


def kill(name):
    subprocess.run(["taskkill", "/IM", name, "/F"], capture_output=True,
                   creationflags=NO_WINDOW)
    time.sleep(1.0)


def main():
    source = os.path.join(HERE, "dist", "payload.7z.exe")
    if not os.path.isfile(source):
        print("[!] tidak ada %s" % source)
        return 2

    fails = []

    # --- penolakan -------------------------------------------------------
    tmp = tempfile.mkdtemp(prefix="updtest_")

    text = os.path.join(tmp, "text.exe")
    with open(text, "w") as fh:
        fh.write("bukan exe" * 4000)
    ok, why = update.verify(text)
    print("[*] tolak teks     : %s (%s)" % (not ok, why))
    if ok:
        fails.append("file teks diterima")

    small = os.path.join(tmp, "small.exe")
    with open(small, "wb") as fh:
        fh.write(b"MZ" + b"\x00" * 100)
    ok, why = update.verify(small)
    print("[*] tolak kecil    : %s (%s)" % (not ok, why))
    if ok:
        fails.append("file kekecilan diterima")

    ok, why = update.verify(source, "0" * 64)
    print("[*] tolak sha salah: %s (%s)" % (not ok, why))
    if ok:
        fails.append("sha256 salah diterima")

    real = sha(source)
    ok, why = update.verify(source, real)
    print("[*] terima sha benar: %s (%s)" % (ok, why))
    if not ok:
        fails.append("build sah ditolak")

    # --- swap nyata ------------------------------------------------------
    work = os.path.join(tmp, "run")
    os.makedirs(work, exist_ok=True)
    app = os.path.join(work, "app.exe")
    staged = os.path.join(tmp, "new_build.exe")
    shutil.copy2(source, app)
    shutil.copy2(source, staged)

    old_sha = sha(app)
    old_size = os.path.getsize(app)
    print("[*] exe lama       : %d B sha=%s" % (old_size, old_sha[:16]))

    proc = subprocess.Popen([app], cwd=work, creationflags=NO_WINDOW)
    time.sleep(4)
    alive = proc.poll() is None
    print("[*] exe jalan      : %s" % alive)
    if not alive:
        fails.append("exe tiruan langsung mati")

    # Build baru dibuat beda dari yang lama, supaya perubahan bisa dibuktikan.
    with open(staged, "r+b") as fh:
        fh.seek(0, os.SEEK_END)
        fh.write(os.urandom(64))
    new_sha = sha(staged)
    print("[*] build baru     : %d B sha=%s" % (os.path.getsize(staged), new_sha[:16]))

    script = update.apply(staged, [app], app)
    print("[*] skrip swap     : %s" % script)
    if not script or not os.path.isfile(script):
        fails.append("skrip swap tidak tertulis")
        kill("app.exe")
        shutil.rmtree(tmp, ignore_errors=True)
        return 1

    # Biarkan skrip mencoba beberapa kali selagi file masih terkunci.
    time.sleep(5)
    locked_ok = sha(app) == old_sha
    print("[*] masih terkunci : %s (sha belum berubah)" % locked_ok)
    if not locked_ok:
        fails.append("file berubah padahal proses masih hidup")

    # Ini yang dilakukan os._exit(0) di payload: proses keluar sendiri.
    kill("app.exe")

    swapped = wait_for(lambda: sha(app) == new_sha, 25)
    print("[*] swap selesai   : %s" % swapped)
    if not swapped:
        fails.append("file tidak tertimpa setelah proses mati")

    restarted = wait_for(
        lambda: subprocess.run(["tasklist", "/FI", "IMAGENAME eq app.exe"],
                               capture_output=True, text=True,
                               creationflags=NO_WINDOW).stdout.lower().count("app.exe") > 0,
        15)
    print("[*] jalan lagi     : %s" % restarted)
    if not restarted:
        fails.append("build baru tidak dijalankan ulang")

    gone = wait_for(lambda: not os.path.exists(script), 10)
    print("[*] skrip hapus diri: %s" % gone)
    if not gone:
        fails.append("skrip swap tidak menghapus dirinya")

    kill("app.exe")
    shutil.rmtree(tmp, ignore_errors=True)

    print()
    if fails:
        for f in fails:
            print("[!] GAGAL: %s" % f)
        return 1
    print("[+] semua uji update lolos")
    return 0


if __name__ == "__main__":
    sys.exit(main())
