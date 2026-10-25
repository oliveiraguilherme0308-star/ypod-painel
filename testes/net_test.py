"""Testa o caminho ESP32 -> PC com uma ponte falsa que fala o mesmo protocolo."""
import json, os, random, shutil, socket, sys, threading, time, urllib.request
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ypod_monitor as y

TMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nettest")
shutil.rmtree(TMP, ignore_errors=True)
os.makedirs(TMP)
y.DATA_DIR = TMP
y.DB = y.Db(os.path.join(TMP, "ypod.db"))

BOOT = "a1b2c3d4"
PORT = 13333


def make_line(t):
    return ("%s,,,YPODV6,YPOD_V4.2.1,,,%.2f,%.2f,,%d,%d,65535,,%d,%d,%.2f,%d,%d,%d," % (
        t.strftime("%Y-%m-%dT%H:%M:%S"),
        22.6 + 1.5 * random.random(), 61 + 2 * random.random(),
        2537 + random.randint(-40, 40), 235 + random.randint(-8, 8),
        3823 + random.randint(-60, 60), 2970 + random.randint(-15, 15),
        748 + 40 * random.random(),
        random.randint(3, 6), random.randint(5, 9), random.randint(5, 10)))


class FakeBridge(threading.Thread):
    """Ponte com 120 linhas acumuladas (ultimos 6 min) + stream ao vivo."""
    daemon = True

    def __init__(self, ignore_since=False):
        super().__init__()
        self.t0 = time.time() - 360          # "ligou" 6 min atras
        self.backlog = []                    # (seq, millis, linha)
        for i in range(1, 121):
            ms = i * 3000
            when = datetime.fromtimestamp(self.t0 + ms / 1000.0)
            self.backlog.append((i, ms, make_line(when)))
        self.seq = 120
        self.ignore_since = ignore_since
        self.since_received = None
        self.sent = 0
        self.stop = False
        self.srv = socket.socket()
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", PORT))
        self.srv.listen(2)
        self.srv.settimeout(1)

    def millis(self):
        return int((time.time() - self.t0) * 1000)

    def run(self):
        while not self.stop:
            try:
                cli, _ = self.srv.accept()
            except socket.timeout:
                continue
            threading.Thread(target=self.serve, args=(cli,), daemon=True).start()

    def serve(self, cli):
        f = cli.makefile("rwb")
        f.write(("$HELLO boot=%s seq=%d buf=%d first=1 now=%d\n"
                 % (BOOT, self.seq, len(self.backlog), self.millis())).encode())
        f.flush()
        cmd = f.readline().decode().strip()
        self.since_received = cmd
        start = 1 if self.ignore_since else max(1, int(cmd.split()[1]) + 1)
        for seq, ms, line in self.backlog:
            if seq >= start:
                f.write(("#%d\t%d\t%s\n" % (seq, ms, line)).encode())
                self.sent += 1
        f.write(b"$LIVE\n")
        f.flush()
        try:
            while not self.stop:                     # ao vivo, 1 linha a cada 0,2 s
                time.sleep(0.2)
                self.seq += 1
                ms = self.millis()
                line = make_line(datetime.now())
                self.backlog.append((self.seq, ms, line))
                f.write(("#%d\t%d\t%s\n" % (self.seq, ms, line)).encode())
                f.flush()
                self.sent += 1
        except OSError:
            pass


def rows():
    return y.DB.conn().execute("SELECT COUNT(*) FROM readings").fetchone()[0]


# --- 1. descoberta por beacon UDP ---------------------------------------
def beacon():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    for _ in range(12):
        s.sendto(("YPOD-BRIDGE host=ypod-bridge ip=127.0.0.1 port=%d boot=%s seq=5"
                  % (PORT, BOOT)).encode(), ("127.0.0.1", y.BRIDGE_BEACON_PORT))
        time.sleep(0.25)

threading.Thread(target=beacon, daemon=True).start()
found = y.discover_bridge(timeout=4.0)
assert found == ("127.0.0.1", PORT), found
print("[ok] descoberta por beacon UDP ->", found)

# --- 2. primeira conexao: puxa todo o backlog ----------------------------
bridge = FakeBridge()
bridge.start()
time.sleep(0.2)
threading.Thread(target=y.tcp_loop, args=("127.0.0.1", PORT, False), daemon=True).start()
time.sleep(3.0)

n1 = rows()
assert bridge.since_received == "SINCE -1", bridge.since_received
assert n1 >= 120, n1
print("[ok] 1a conexao: pediu '%s' e gravou %d leituras (120 de backlog + ao vivo)"
      % (bridge.since_received, n1))

# os timestamps do backlog tem que ficar no PASSADO, nao todos "agora"
first, last = y.DB.conn().execute("SELECT MIN(ts), MAX(ts) FROM readings").fetchone()
span = last - first
assert span > 300, "backlog achatado no tempo: span=%.0fs" % span
print("[ok] backlog reconstruido no tempo certo: %.0f min entre a 1a e a ultima"
      % (span / 60))
print("     primeira: %s | ultima: %s"
      % (datetime.fromtimestamp(first).strftime("%H:%M:%S"),
         datetime.fromtimestamp(last).strftime("%H:%M:%S")))

saved = y.DB.get_state("seq:" + BOOT)
print("[ok] ponto de retomada salvo no banco: seq=%s" % saved)

# --- 3. reconexao: nao pode duplicar -------------------------------------
before = rows()
bridge.stop = True
time.sleep(0.3)
bridge.srv.close()

bridge2 = FakeBridge(ignore_since=True)   # ponte teimosa: reenvia TUDO
bridge2.backlog = bridge.backlog[:]
bridge2.seq = bridge.seq
bridge2.t0 = bridge.t0
bridge2.start()
time.sleep(6.0)                            # o cliente reconecta em ~4 s
after = rows()
dup = y.DB.conn().execute(
    "SELECT COUNT(*) FROM (SELECT src_key FROM readings GROUP BY src_key HAVING COUNT(*)>1)"
).fetchone()[0]
assert dup == 0, "%d chaves duplicadas" % dup
reenviadas = len(bridge2.backlog)
print("[ok] reconexao: ponte reenviou %d linhas antigas, banco subiu so %d (sem duplicar)"
      % (reenviadas, after - before))
print("[ok] pedido de retomada: '%s'" % bridge2.since_received)
bridge2.stop = True

# --- 4. API: janela relativa e intervalo absoluto ------------------------
srv = y.ThreadingHTTPServer(("127.0.0.1", 8733), y.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
time.sleep(0.3)

def get(path):
    with urllib.request.urlopen("http://127.0.0.1:8733" + path, timeout=10) as r:
        return r.status, r.read()

live = json.loads(get("/api/live")[1])
assert live["status"] == "conectado" and live["meta"]["pod_id"] == "YPODV6"
assert live["cards"][0]["value"] is not None
print("[ok] /api/live  fonte=%s  temp=%s  status=%s"
      % (live["source"], live["cards"][0]["value"], live["status"]))

w = json.loads(get("/api/window?window=3600")[1])
assert w["series"]["temp"], "serie vazia"
print("[ok] /api/window janela 1 h: %d pontos, balde de %ds, stats temp=%s"
      % (len(w["series"]["temp"]), w["bucket"], w["stats"]["temp"]))

# intervalo absoluto: so os 2 primeiros minutos do backlog
t0 = (first // 60 * 60) * 1000          # bordas redondas de minuto
t1 = t0 + 120 * 1000
w2 = json.loads(get("/api/window?from=%d&to=%d" % (t0, t1))[1])
n_full = w["stats"]["temp"]["n"]
n_slice = w2["stats"]["temp"]["n"]
assert 0 < n_slice < n_full, (n_slice, n_full)
print("[ok] /api/window intervalo fixo (2 min): %d leituras (de %d na janela inteira)"
      % (n_slice, n_full))
print("     de %s a %s" % (datetime.fromtimestamp(w2["from"]/1000).strftime("%H:%M:%S"),
                           datetime.fromtimestamp(w2["to"]/1000).strftime("%H:%M:%S")))

# balde adaptativo: janela de 30 dias nao pode estourar pontos
w3 = json.loads(get("/api/window?window=2592000")[1])
assert len(w3["series"]["temp"]) <= 1400
assert w3["bucket"] == 60, "com poucos dados o balde tem que continuar de 1 min, veio %d" % w3["bucket"]
print("[ok] balde adaptativo: pedir 30 d com ~6 min gravados -> %d pontos, balde de %d s"
      % (len(w3["series"]["temp"]), w3["bucket"]))

tb = json.loads(get("/api/table?window=3600&limit=50")[1])
assert len(tb["rows"]) == 50
print("[ok] /api/table  %d linhas" % len(tb["rows"]))

rng = json.loads(get("/api/range")[1])
print("[ok] /api/range  %d leituras no banco" % rng["rows"])

code, body = get("/api/export?from=%d&to=%d" % (t0, t1))
lines = body.decode("utf-8").strip().split("\n")
assert lines[0].startswith("pc_time,pod_time") and len(lines) - 1 == n_slice
print("[ok] /api/export do intervalo: %d linhas de CSV" % (len(lines) - 1))

code, body = get("/")
assert b"YPOD Monitor" in body and b"datetime-local" in body
print("[ok] /  painel servido com o seletor de intervalo (%d bytes)" % len(body))

# --- 5. banco sobrevive a reinicio --------------------------------------
y.DB = y.Db(os.path.join(TMP, "ypod.db"))
r2 = y.DB.range()
assert r2["rows"] >= after
print("[ok] reabrindo o banco: %d leituras continuam la" % r2["rows"])

print("\nTUDO PASSOU")
