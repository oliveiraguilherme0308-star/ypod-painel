#!/usr/bin/env python3
"""
Ponte falsa - finge ser o ESP32 para você testar o PC antes de montar o hardware.

    python tools/ponte_falsa.py

Em outro terminal, rode o monitor normalmente (`python ypod_monitor.py`): ele vai
achar esta ponte pelo beacon UDP, puxar o "backlog" e seguir ao vivo. Dá para
desligar aqui no Ctrl+C e ligar de novo para ver a retomada sem duplicar dados.
"""
import argparse
import random
import socket
import threading
import time
from datetime import datetime

BOOT = "%08x" % random.getrandbits(32)
TCP_PORT = 3333
BEACON_PORT = 3334

t0 = time.time() - 600          # finge estar ligada ha 10 min
backlog = []                    # (seq, millis, linha)
seq = 0
lock = threading.Lock()


def millis():
    return int((time.time() - t0) * 1000)


def make_line(when):
    """Mesmo formato do YPOD de verdade, com uma variacao suave ao longo do dia."""
    h = when.hour + when.minute / 60.0
    temp = 22.5 + 2.5 * (1 + math_sin(h)) + random.uniform(-0.15, 0.15)
    rh = 62 - 6 * (1 + math_sin(h)) + random.uniform(-0.4, 0.4)
    return ("%s,,,YPODV6,YPOD_V4.2.1,,,%.2f,%.2f,,%d,%d,65535,,%d,%d,%.2f,%d,%d,%d," % (
        when.strftime("%Y-%m-%dT%H:%M:%S"), temp, rh,
        2530 + random.randint(-45, 45), 233 + random.randint(-9, 9),
        3810 + random.randint(-70, 70), 2968 + random.randint(-16, 16),
        735 + 60 * (1 + math_sin(h)) + random.uniform(-8, 8),
        random.randint(2, 7), random.randint(4, 11), random.randint(4, 13)))


def math_sin(h):
    import math
    return math.sin((h - 9) / 24 * 2 * math.pi)


def fill_backlog():
    global seq
    for i in range(200):                  # 10 min a cada 3 s
        seq += 1
        ms = i * 3000
        backlog.append((seq, ms, make_line(datetime.fromtimestamp(t0 + ms / 1000))))


def generator():
    """Uma leitura nova a cada 3 s, como o pod faz."""
    global seq
    while True:
        time.sleep(3)
        with lock:
            seq += 1
            backlog.append((seq, millis(), make_line(datetime.now())))
            if len(backlog) > 6000:
                del backlog[:1000]


def beacon(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    while True:
        msg = ("YPOD-BRIDGE host=ponte-falsa ip=127.0.0.1 port=%d boot=%s seq=%d"
               % (port, BOOT, seq))
        for alvo in ("127.0.0.1", "255.255.255.255"):
            try:
                s.sendto(msg.encode(), (alvo, BEACON_PORT))
            except OSError:
                pass
        time.sleep(3)


def serve(cli, addr):
    print("[ponte] PC conectado de %s:%d" % addr)
    f = cli.makefile("rwb")
    try:
        with lock:
            first = backlog[0][0] if backlog else seq
            f.write(("$HELLO boot=%s seq=%d buf=%d first=%d now=%d\n"
                     % (BOOT, seq, len(backlog), first, millis())).encode())
        f.flush()
        cmd = f.readline().decode().strip()
        want = int(cmd.split()[1]) if cmd.startswith("SINCE") else -1
        start = first if want < 0 else want + 1
        print("[ponte] pediu '%s' -> mandando a partir de %d" % (cmd, start))

        sent = 0
        with lock:
            pend = [x for x in backlog if x[0] >= start]
        for s_, ms, line in pend:
            f.write(("#%d\t%d\t%s\n" % (s_, ms, line)).encode())
            sent += 1
        f.write(b"$LIVE\n")
        f.flush()
        print("[ponte] %d linhas de backlog enviadas, agora ao vivo" % sent)

        last = seq
        while True:
            time.sleep(0.5)
            with lock:
                novos = [x for x in backlog if x[0] > last]
            for s_, ms, line in novos:
                f.write(("#%d\t%d\t%s\n" % (s_, ms, line)).encode())
                last = s_
            if not novos:
                f.write(("$PING now=%d\n" % millis()).encode())
            f.flush()
    except (OSError, ValueError, IndexError) as exc:
        print("[ponte] cliente saiu (%s)" % exc)
    finally:
        cli.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=TCP_PORT)
    args = ap.parse_args()

    fill_backlog()
    threading.Thread(target=generator, daemon=True).start()
    threading.Thread(target=beacon, args=(args.port,), daemon=True).start()

    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", args.port))
    srv.listen(3)
    print("ponte falsa no ar: porta %d, boot=%s, %d linhas no buffer"
          % (args.port, BOOT, len(backlog)))
    print("agora rode:  python ypod_monitor.py")
    try:
        while True:
            cli, addr = srv.accept()
            threading.Thread(target=serve, args=(cli, addr), daemon=True).start()
    except KeyboardInterrupt:
        print("\nponte desligada.")


if __name__ == "__main__":
    main()
