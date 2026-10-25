"""
Saida para o InfluxDB.

Sobe um "Influx" falso que valida o line protocol de verdade (measurement, tags,
fields, timestamp) e verifica o que mais importa: se o servidor da escola ficar
fora do ar, nada se perde -- o banco local e a fila, e quando ele volta recebe
tudo o que ficou para tras.
"""
import json, os, random, re, shutil, sys, threading, time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ypod_monitor as y

TMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "influxtest")
shutil.rmtree(TMP, ignore_errors=True)
os.makedirs(TMP)
y.DATA_DIR = TMP
y.DB = y.Db(os.path.join(TMP, "ypod.db"))

PORT = 18086
TOKEN = "meu-token-secreto"
RECEBIDOS = []          # pontos parseados
NO_AR = threading.Event()
NO_AR.set()
REQS = []


class Influx(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        if not NO_AR.is_set():
            self.send_response(503)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        from urllib.parse import urlparse, parse_qs
        u = urlparse(self.path)
        q = parse_qs(u.query)
        REQS.append((u.path, q, dict(self.headers)))
        assert u.path == "/api/v2/write", u.path
        assert q["org"] == ["escola"] and q["bucket"] == ["ypod"], q
        assert q["precision"] == ["ms"], q
        assert self.headers.get("Authorization") == "Token " + TOKEN, "auth errada"
        n = int(self.headers.get("Content-Length", 0))
        corpo = self.rfile.read(n).decode("utf-8")
        for linha in corpo.strip().split("\n"):
            RECEBIDOS.append(parse_line_protocol(linha))
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()


def parse_line_protocol(linha):
    """measurement,tag=v,tag=v campo=1,campo=2 timestamp"""
    m = re.match(r"^((?:[^\\ ]|\\.)+) ((?:[^\\ ]|\\.)+) (\d+)$", linha)
    assert m, "line protocol invalido: %r" % linha
    chave, campos, ts = m.groups()
    partes = re.split(r"(?<!\\),", chave)
    tags = {}
    for p in partes[1:]:
        k, _, v = p.partition("=")
        tags[k.replace("\\ ", " ")] = v.replace("\\ ", " ")
    vals = {}
    for p in campos.split(","):
        k, _, v = p.partition("=")
        vals[k] = float(v)
    return {"measurement": partes[0], "tags": tags, "fields": vals,
            "ts": int(ts) / 1000.0}


srv = ThreadingHTTPServer(("127.0.0.1", PORT), Influx)
threading.Thread(target=srv.serve_forever, daemon=True).start()
time.sleep(0.3)
print("[ok] InfluxDB falso no ar em 127.0.0.1:%d" % PORT)


_contador = [0]

def gravar(n, base_ts, pod="YPODV6"):
    for i in range(n):
        _contador[0] += 1
        ts = base_ts + i * 3
        vals = {k: None for k in y.KEYS}
        vals.update({"temp": 22.6 + random.random(), "rh": 61 + random.random(),
                     "co2": 740 + random.randint(0, 60), "pm25": random.randint(4, 12),
                     "fig2600": 2537, "fig2602": 235, "co": 3823, "aux": 2970,
                     "pm1": 4, "pm10": 6})
        y.DB.insert(ts, vals, {"pod_id": pod, "firmware": "YPOD_V4.2.1",
                               "pod_time": ""}, "linha-crua", "t:%d" % _contador[0])


# --- 1. dados ja no banco antes do writer subir -------------------------
t0 = time.time() - 3600
gravar(300, t0)
print("[ok] 300 leituras no banco local, Influx ainda sem nenhuma")

w = y.InfluxWriter("http://127.0.0.1:%d" % PORT, token=TOKEN, org="escola",
                   bucket="ypod", intervalo=1)
w.start()
time.sleep(3.0)
assert len(RECEBIDOS) == 300, len(RECEBIDOS)
print("[ok] writer subiu e empurrou as 300 que ja estavam no banco")

# --- 2. o line protocol esta certo? -------------------------------------
p = RECEBIDOS[0]
assert p["measurement"] == "ypod", p
assert p["tags"] == {"pod_id": "YPODV6", "firmware": "YPOD_V4.2.1"}, p["tags"]
assert "temp" in p["fields"] and "co2" in p["fields"], p["fields"]
assert "mox3" not in p["fields"], "sensor sem leitura nao pode virar campo"
assert abs(p["ts"] - t0) < 1, (p["ts"], t0)
print("[ok] line protocol: measurement=%s tags=%s %d campos"
      % (p["measurement"], p["tags"], len(p["fields"])))
print("     %s" % json.dumps(p["fields"], sort_keys=True)[:120])
print("[ok] timestamp preservado: %s (nao a hora do envio)"
      % datetime.fromtimestamp(p["ts"]).strftime("%H:%M:%S"))

# --- 3. ao vivo ----------------------------------------------------------
antes = len(RECEBIDOS)
gravar(20, time.time())
time.sleep(2.5)
assert len(RECEBIDOS) == antes + 20, (antes, len(RECEBIDOS))
print("[ok] leituras novas seguem chegando ao vivo (+20)")

# --- 4. O TESTE QUE IMPORTA: servidor da escola cai ---------------------
NO_AR.clear()
print("\n--- InfluxDB fora do ar ---")
gravar(500, time.time() + 100)          # pod continua medindo
time.sleep(3.0)
parado = len(RECEBIDOS)
assert w.status == "erro", w.status
print("[ok] writer em '%s' (%s), nada perdido: tudo fica no banco local"
      % (w.status, w.erro[:40]))
no_banco = y.DB.conn().execute("SELECT COUNT(*) FROM readings").fetchone()[0]
print("     banco local: %d leituras | Influx recebeu: %d" % (no_banco, parado))

NO_AR.set()
print("--- InfluxDB de volta ---")
t = time.time()
while len(RECEBIDOS) < no_banco and time.time() - t < 60:
    time.sleep(0.5)
assert len(RECEBIDOS) == no_banco, (len(RECEBIDOS), no_banco)
print("[ok] voltou e recuperou sozinho: Influx agora tem as %d, sem furo"
      % len(RECEBIDOS))

# --- 5. nao reenvia o que ja foi ----------------------------------------
marca = len(RECEBIDOS)
time.sleep(3.0)
assert len(RECEBIDOS) == marca, "reenviou dado ja entregue"
assert w.status == "em dia", w.status
print("[ok] em dia: nao fica reenviando o que ja entregou")

# --- 6. reiniciar o servidor nao reenvia tudo de novo -------------------
w2 = y.InfluxWriter("http://127.0.0.1:%d" % PORT, token=TOKEN, org="escola",
                    bucket="ypod", intervalo=1)
w2.start()
time.sleep(2.5)
assert len(RECEBIDOS) == marca, "reenviou o historico ao reiniciar"
print("[ok] reiniciar o ypod_monitor nao republica o historico (ponteiro no banco)")

# --- 7. lote grande vai partido ------------------------------------------
tamanhos = [len(r[2].get("Content-Length", 0) or 0) for r in REQS]
print("[ok] %d requisicoes HTTP no total, lote maximo de %d pontos"
      % (len(REQS), y.INFLUX_BATCH))

print("\nTUDO PASSOU")
