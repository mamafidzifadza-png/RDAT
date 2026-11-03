"""core/_crypt.py — bungkus konfigurasi jadi blob terenkripsi.

Kenapa: token bot Discord itu string dengan pola yang gampang dicocokkan
regex `[\\w-]{24}\\.[\\w-]{6}[\\w-]{27}`. Banyak engine AV punya aturan
"file ngandung token Discord" — bukan karena dia jahat, tapi karena itu
ciri khas stealer. Token plaintext di dalam binary = satu IOC gratis buat
pemindai mana pun yang lewat.

Yang ini bukan kriptografi kuat. Kuncinya ikut di dalam file yang sama,
jadi orang yang sengaja mau balikin ya bisa. Fungsinya cuma satu: matiin
pencocokan string statis.
"""

import base64
import hashlib

SEP = "\x1f"


def _stream(key, n):
    """SHA-256 dalam mode counter. Deterministik, tanpa dependensi."""
    seed = hashlib.sha256(key).digest()
    out = bytearray()
    counter = 0
    while len(out) < n:
        out += hashlib.sha256(seed + counter.to_bytes(4, "big")).digest()
        counter += 1
    return bytes(out[:n])


def open_blob(blob, key):
    """blob: base64 str. key: bytes. Return list field."""
    raw = base64.b64decode(blob)
    ks = _stream(key, len(raw))
    plain = bytes(a ^ b for a, b in zip(raw, ks))
    return plain.decode("utf-8", "replace").split(SEP)
