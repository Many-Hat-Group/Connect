"""List sockets listening on non-loopback addresses on this host (the LXC).
Phase 1: only 8080 (internal NIC) and the TURN ports may appear.
Usage: python3 tests/integration/listening_ports.py [allowed ports...]"""
import socket
import struct
import sys

ALLOWED = {int(p) for p in sys.argv[1:]} or {8080, 3478, 5349}


def parse(path, proto, listen_state="0A"):
    out = []
    try:
        lines = open(path).read().splitlines()[1:]
    except FileNotFoundError:
        return out
    for line in lines:
        f = line.split()
        local, state = f[1], f[3]
        if proto.startswith("tcp") and state != listen_state:
            continue
        addr_hex, port_hex = local.split(":")
        port = int(port_hex, 16)
        if len(addr_hex) == 8:
            addr = socket.inet_ntoa(struct.pack("<I", int(addr_hex, 16)))
        else:
            b = bytes.fromhex(addr_hex)
            addr = socket.inet_ntop(socket.AF_INET6, b"".join(b[i:i + 4][::-1] for i in range(0, 16, 4)))
        out.append((proto, addr, port))
    return out


socks = []
for path, proto in (("/proc/net/tcp", "tcp"), ("/proc/net/tcp6", "tcp6"), ("/proc/net/udp", "udp"), ("/proc/net/udp6", "udp6")):
    socks += parse(path, proto)
bad = 0
for proto, addr, port in sorted(set(socks), key=lambda x: (x[2], x[0])):
    if addr.startswith("127.") or addr == "::1":
        continue
    if proto.startswith("udp") and 49152 <= port <= 65535:
        ok = True  # TURN relay range (and ephemeral client sockets)
    else:
        ok = port in ALLOWED
    print(f"{'OK  ' if ok else 'FAIL'} {proto:5} {addr}:{port}")
    bad += not ok
sys.exit(1 if bad else 0)
