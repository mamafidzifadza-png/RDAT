"""tools/unpack.py — ambil payload dari PNG mode chunk.

Dipakai buat membuktikan chunk-nya bisa dibalik, dan buat lu sendiri kalau
mau narik exe keluar dari gambar tanpa 7-Zip.

    python tools/unpack.py foto_chunk.png hasil.exe
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from carrier import CHUNK_ID, extract_chunk, iter_chunks  # noqa: E402


def main():
    if len(sys.argv) != 3:
        print("pakai: python tools/unpack.py <gambar.png> <hasil.exe>")
        return 1
    src, dst = sys.argv[1], sys.argv[2]
    data = open(src, "rb").read()

    print("[*] chunk di dalam %s:" % os.path.basename(src))
    for off, length, name, _, _ in iter_chunks(data):
        label = name.decode("latin-1")
        print("    %-4s offset=%-9d len=%d" % (label, off, length))

    try:
        extract_chunk(src, dst)
    except ValueError as exc:
        print("[!] %s" % exc)
        return 1
    print("[+] %s (%d B)" % (dst, os.path.getsize(dst)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
