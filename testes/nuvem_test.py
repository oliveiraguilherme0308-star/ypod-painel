"""
Saida para a nuvem (Cloudflare Worker + D1).

Sobe um Worker falso que confere o token e o formato do /api/push, guarda as
linhas pelo "k" (como o INSERT OR IGNORE do D1) e verifica o que importa: com
a internet fora, nada se perde; quando volta, o atraso sobe em lotes; e
reiniciar o PC nao republica o historico.

O Worker de verdade (worker/src/index.js) e testado com `wrangler dev`.
"""
import json, os, shutil, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ypod_monitor as y

TMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nuventest")
shutil.rmtree(TMP, ignore_errors=True)
os.makedirs(TMP)
y.DATA_DIR = TMP
y.DB = y.Db(os.path.join(TMP, "ypod.db"))

PORT = 18787
TOKEN = "token-de-teste"
D1 = {}                 # k -> linha (o que o D1 teria)
LIVE = {}               # ultimo retrato
POSTS = []              # (n linhas, user-agent)
NO_AR = threading.Event()
NO_AR.set()


class Worker(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _resp(self, code, body=b""):
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        # Le o corpo ANTES de decidir a resposta, inclusive quando vai recusar.
        # Responder sem consumir o que o cliente esta mandando faz o socket
        # fechar no meio do envio, e o cliente ve um reset em vez do 401. Era
        # a causa deste teste falhar em ~1 de cada 3 execucoes.
        n = int(self.headers.get("Content-Length", 0))
        bruto = self.rfile.read(n) if n else b"{}"
        if not NO_AR.is_set():
            return self._resp(503)
        assert self.path == "/api/push", self.path
        if self.headers.get("Authorization") != "Bearer " + TOKEN:
            return self._resp(401, b"token invalido")
        corpo = json.loads(bruto)
        rows = corpo["rows"]
        assert len(rows) <= 1000, "lote acima do limite do Worker"
        POSTS.append((len(rows), self.headers.get("User-Agent", "")))
        novas = 0
        for r in rows:
            assert r["k"] and isinstance(r["ts"], float), r
            if r["k"] not in D1:
                D1[r["k"]] = r
                novas += 1
        LIVE.clear()
        LIVE.update(corpo["live"])
        self._resp(200, json.dumps({"ok": True, "inseridas": novas}).encode())


srv = ThreadingHTTPServer(("127.0.0.1", PORT), Worker)
threading.Thread(target=srv.serve_forever, daemon=True).start()
time.sleep(0.3)
URL = "http://127.0.0.1:%d" % PORT
print("[ok] Worker falso no ar em %s" % URL)


def gravar_minutos(n, inicio):
    """Como o AcumuladorPorMinuto grava: uma media por minuto, src_key min:<epoch>."""
    for i in range(n):
        b = int(inicio // 60) * 60 + i * 60
        vals = {k: None for k in y.KEYS}
        vals.update({"temp": 20.5 + i % 3, "rh": 76.0, "co2": 348.0, "pm25": 10.0})
        y.DB.insert(b + 30, vals, {"pod_id": "YPODV6", "firmware": "YPOD_V4.2.1",
                                   "pod_time": ""}, "linha", "min:%d" % b)


# --- 1. historico que ja estava no banco sobe sozinho, em lotes ------------
gravar_minutos(1200, time.time() - 1200 * 60)
w = y.NuvemWriter(URL, TOKEN, intervalo=1)
y.NUVEM = w
w.start()
t = time.time()
while len(D1) < 1200 and time.time() - t < 20:
    time.sleep(0.2)
assert len(D1) == 1200, len(D1)
assert max(n for n, _ in POSTS) == y.NUVEM_LOTE, POSTS[:5]
print("[ok] 1200 minutos de historico subiram em lotes de %d" % y.NUVEM_LOTE)

# --- 2. formato ---------------------------------------------------------
r = next(iter(D1.values()))
assert r["k"].startswith("min:") and r["pod_id"] == "YPODV6", r
assert "mox3" not in r, "sensor sem leitura nao vai na linha"
assert "ypod-monitor" in POSTS[0][1], "User-Agent padrao do urllib e barrado pela Cloudflare"
assert LIVE.get("fields") and LIVE.get("groups") and "cards" in LIVE, list(LIVE)
assert {f["key"] for f in LIVE["fields"]} == set(y.KEYS)
print("[ok] linha com k/ts/pod_id e so sensores com valor; retrato traz cards, fields, groups")

# --- 3. sem nada novo, ainda manda o retrato (o "ao vivo" do site) -------
antes = len(POSTS)
time.sleep(2.5)
assert len(POSTS) > antes and POSTS[-1][0] == 0, POSTS[-3:]
assert w.status == "em dia", w.status
print("[ok] em dia: segue mandando o retrato ao vivo, sem reenviar linha")

# --- 4. internet cai ----------------------------------------------------
NO_AR.clear()
print("\n--- Worker fora do ar ---")
gravar_minutos(300, time.time() + 3600)
time.sleep(2.5)
assert w.status == "erro", w.status
print("[ok] writer em '%s', as 300 novas esperam no banco local" % w.status)
NO_AR.set()
print("--- Worker de volta ---")
t = time.time()
while len(D1) < 1500 and time.time() - t < 40:
    time.sleep(0.3)
assert len(D1) == 1500, len(D1)
print("[ok] voltou e recuperou sozinho: D1 com 1500, sem furo")

# --- 5. token errado nao avanca o ponteiro -------------------------------
ruim = y.NuvemWriter(URL, "errado", intervalo=1)
try:
    ruim._enviar({"rows": [], "live": {}})
    raise AssertionError("token errado foi aceito")
except AssertionError:
    raise
except Exception as exc:                              # noqa: BLE001
    assert "401" in str(exc), exc
print("[ok] token errado -> 401")

# --- 6. reiniciar o PC nao republica ------------------------------------
w2 = y.NuvemWriter(URL, TOKEN, intervalo=1)
marca = sum(n for n, _ in POSTS)
w2.start()
time.sleep(2.5)
assert sum(n for n, _ in POSTS) == marca, "reenviou o historico ao reiniciar"
print("[ok] reiniciar o ypod_monitor nao republica o historico (ponteiro no banco)")

print("\nTUDO PASSOU")
# os._exit nao esvazia o buffer do stdout: com a saida num pipe (ou numa
# execucao automatizada) o teste passava mudo, indistinguivel de nao ter
# rodado. O _exit continua ali porque e ele que mata o Worker falso sem
# esperar as threads.
sys.stdout.flush()
os._exit(0)
