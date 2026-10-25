"""Volume (6 meses de dados), import dos CSVs antigos e modo USB."""
import csv, json, os, random, shutil, sqlite3, sys, threading, time, urllib.request
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ypod_monitor as y

TMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voltest")
shutil.rmtree(TMP, ignore_errors=True)
os.makedirs(TMP)
y.DATA_DIR = TMP

# --- 1. CSV da versao antiga, para testar a importacao -------------------
old = os.path.join(TMP, "ypod_2026-09-20.csv")
base = datetime.now() - timedelta(days=18)
with open(old, "w", newline="", encoding="utf-8") as fh:
    w = csv.writer(fh)
    w.writerow(y.CSV_HEADER)
    for i in range(500):
        t = base + timedelta(seconds=3 * i)
        w.writerow([t.isoformat(timespec="seconds"), t.isoformat(), "YPODV6",
                    "YPOD_V4.2.1", 21.5, 59.0, 2500, 230, "", 3800, 2960,
                    740, 4, 6, 6, "linha-antiga"])

y.DB = y.Db(os.path.join(TMP, "ypod.db"))
n = y.DB.import_csv_files()
assert n == 500, n
n2 = y.DB.import_csv_files()          # roda de novo: nao pode importar duas vezes
assert n2 == 0 and y.DB.range()["rows"] == 500
print("[ok] CSVs da versao antiga importados: %d linhas (e nao reimporta)" % n)

# --- 2. volume: 6 meses de verdade, uma leitura a cada 3 s --------------
STEP = 3.0
N = int(180 * 86400 / STEP)          # ~5,2 milhoes
c = y.DB.conn()
cols = ["ts", "pod_id", "firmware"] + y.KEYS
sql = ("INSERT INTO readings (%s) VALUES (%s)"
       % (",".join(cols), ",".join("?" * len(cols))))
t0 = time.time() - 180 * 86400
start = time.time()
done = 0
while done < N:
    rows = []
    for i in range(done, min(done + 250_000, N)):
        # Montado POR CHAVE, nao como tupla posicional: quando o vento foi
        # acrescentado a KEYS, a tupla antiga ficou com 14 valores para 16
        # colunas e este teste quebrou calado por dois dias. Assim, sensor novo
        # entra com valor None em vez de derrubar o teste.
        v = {
            "temp": 22 + 3 * random.random(),
            "rh": 60 + 8 * random.random(),
            "fig2600": 2500 + random.randint(-80, 80),
            "fig2602": 235 + random.randint(-20, 20),
            "mox3": None,
            "co": 3800 + random.randint(-120, 120),
            "aux": 2960 + random.randint(-40, 40),
            "co2": 740 + random.randint(0, 120),
            "pm1": random.randint(2, 9),
            "pm25": random.randint(4, 14),
            "pm10": random.randint(4, 18),
            "vento_vel": random.randint(0, 900),
            "vento_dir": random.randint(0, 359),
        }
        rows.append((t0 + i * STEP, "YPODV6", "YPOD_V4.2.1")
                    + tuple(v.get(k) for k in y.KEYS))
    c.executemany(sql, rows)
    c.commit()
    done += len(rows)
size = os.path.getsize(os.path.join(TMP, "ypod.db")) / 1e6
# inserimos por fora da API (executemany direto): reabrir o banco tem que
# perceber que ha dados mais antigos que o resumo e refaze-lo sozinho
y.DB = y.Db(os.path.join(TMP, "ypod.db"))
print("[ok] %s leituras (6 meses a cada 3 s) em %.0fs | banco: %.0f MB"
      % (format(N, ","), time.time() - start, size))

srv = y.ThreadingHTTPServer(("127.0.0.1", 8734), y.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
time.sleep(0.3)

def timed(path):
    t = time.time()
    with urllib.request.urlopen("http://127.0.0.1:8734" + path, timeout=60) as r:
        body = r.read()
    return (time.time() - t) * 1000, body

for label, q in (("1 h", "window=3600"), ("24 h", "window=86400"),
                 ("7 d", "window=604800"), ("30 d", "window=2592000"),
                 ("6 meses", "window=15552000")):
    ms, body = timed("/api/window?" + q)
    d = json.loads(body)
    pts = len(d["series"]["temp"])
    assert pts <= 1400
    print("   %-8s %6.0f ms  %5d pontos  balde %5ds  n=%s"
          % (label, ms, pts, d["bucket"], format(d["stats"]["temp"]["n"], ",")))

ms, _ = timed("/api/live")
print("[ok] /api/live (o que roda a cada 5 s): %.0f ms" % ms)

# intervalo arbitrario no meio do historico
a = (t0 + 90 * 86400) * 1000
b = a + 6 * 3600 * 1000
ms, body = timed("/api/window?from=%d&to=%d" % (a, b))
d = json.loads(body)
print("[ok] intervalo de 6 h, 3 meses atras: %.0f ms, %d pontos, n=%s"
      % (ms, len(d["series"]["temp"]), format(d["stats"]["temp"]["n"], ",")))

ms, body = timed("/api/export?from=%d&to=%d" % (a, b))
print("[ok] export desse intervalo: %.0f ms, %.1f MB de CSV"
      % (ms, len(body) / 1e6))

# --- 3. modo USB continua funcionando ------------------------------------
class FakeSerial:
    opens = 0
    def __init__(self, *a, **k):
        FakeSerial.opens += 1
        self.n = 0
    def readline(self):
        self.n += 1
        if 5 <= self.n <= 7:
            time.sleep(0.05)
            return b""                  # pod calado
        time.sleep(0.01)
        t = datetime.now()
        return ("%s,,,YPODV6,YPOD_V4.2.1,,,22.66,61.41,,2537,235,65535,,3823,2970,748.00,4,6,6,\r\n"
                % t.strftime("%Y-%m-%dT%H:%M:%S")).encode()
    def reset_input_buffer(self): pass
    def close(self): pass

y.serial.Serial = FakeSerial
before = y.LIVE.count
threading.Thread(target=y.serial_loop, args=("FAKE", 9600, False), daemon=True).start()
time.sleep(1.5)
assert y.LIVE.count - before > 10 and y.LIVE.status == "conectado"
assert FakeSerial.opens == 1
live = json.loads(urllib.request.urlopen("http://127.0.0.1:8734/api/live").read())
assert live["source"].startswith("USB")
print("[ok] modo USB: %d linhas, fonte='%s', porta aberta 1 vez"
      % (y.LIVE.count - before, live["source"]))

print("\nTUDO PASSOU")
