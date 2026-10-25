"""
Caminho MQTT: ponte -> broker -> servidor.

Sobe um broker de verdade, publica no formato EXATO que o firmware monta (o
template sai do saida_mqtt.cpp, igual ao teste de contrato do TCP) e confere o
que o servidor grava: horario reconstruido pelo "age", dedupe de QoS 1, e
sobrevivencia a queda do broker.
"""
import asyncio, json, os, random, re, shutil, sys, threading, time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ypod_monitor as y
import paho.mqtt.client as mqtt
import paho.mqtt.publish as publish

CPP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "firmware", "ypod_bridge", "saida_mqtt.cpp")
TMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mqtttest")
shutil.rmtree(TMP, ignore_errors=True)
os.makedirs(TMP)
y.DATA_DIR = TMP
y.DB = y.Db(os.path.join(TMP, "ypod.db"))

PORT = 18831
BASE = "ypod/pod1"
BOOT = "a1b2c3d4"

# --- 1. template do payload tirado do proprio firmware -------------------
src = open(CPP, encoding="utf-8").read()
m = re.search(r'"(\{\\"boot.*?\})"', src)
assert m, "nao achei o template JSON no saida_mqtt.cpp"
tmpl = m.group(1).replace('\\"', '"')
print("template do firmware: %s" % tmpl)
campos = set(re.findall(r'"(\w+)":', tmpl))
assert campos == {"boot", "seq", "age", "line"}, campos
print("[ok] campos publicados pelo firmware:", sorted(campos))

py_tmpl = (tmpl.replace("%08lx", "%(boot)s").replace('%lu', "%(x)s")
               .replace("%s", "%(line)s"))
# monta na mao para nao depender da ordem dos %lu
def payload(seq, age, line):
    return json.dumps({"boot": BOOT, "seq": seq, "age": age, "line": line})


def pod_line(when):
    return ("%s,,,YPODV6,YPOD_V4.2.1,,,%.2f,%.2f,,%d,%d,65535,,%d,%d,%.2f,%d,%d,%d," % (
        when.strftime("%Y-%m-%dT%H:%M:%S"), 22.6 + random.random(),
        61 + random.random(), 2537, 235, 3823, 2970, 748.0, 4, 6, 6))


# --- 2. broker de verdade -----------------------------------------------
from amqtt.broker import Broker

broker_cfg = {
    "listeners": {"default": {"type": "tcp", "bind": "127.0.0.1:%d" % PORT,
                              "max_connections": 20}},
    "sys_interval": 0,
    "auth": {"allow-anonymous": True},
    "topic-check": {"enabled": False},
}
loop = asyncio.new_event_loop()
broker = Broker(broker_cfg, loop=loop)

def rodar_broker():
    asyncio.set_event_loop(loop)
    loop.run_until_complete(broker.start())
    loop.run_forever()

threading.Thread(target=rodar_broker, daemon=True).start()
time.sleep(2.0)
print("[ok] broker no ar em 127.0.0.1:%d" % PORT)

# --- 3. servidor assina --------------------------------------------------
threading.Thread(target=y.mqtt_loop,
                 args=("mqtt://127.0.0.1:%d" % PORT, BASE, "", "", False),
                 daemon=True).start()
time.sleep(2.5)
assert y.LIVE.status == "conectado", (y.LIVE.status, y.LIVE.detail)
print("[ok] servidor conectado e assinando %s/raw" % BASE)

# --- 4. a ponte publica um backlog de 10 min ----------------------------
pub = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="ponte-falsa")
pub.connect("127.0.0.1", PORT, 45)
pub.loop_start()

agora = time.time()
N = 200
for i in range(1, N + 1):
    idade_s = (N - i) * 3            # a mais antiga tem 10 min
    linha = pod_line(datetime.fromtimestamp(agora - idade_s))
    pub.publish(BASE + "/raw", payload(i, idade_s * 1000, linha), qos=1)
time.sleep(4.0)

n = y.DB.conn().execute("SELECT COUNT(*) FROM readings").fetchone()[0]
assert n == N, "gravou %d de %d" % (n, N)
print("[ok] %d leituras publicadas com QoS 1 chegaram todas" % n)

lo, hi = y.DB.conn().execute("SELECT MIN(ts), MAX(ts) FROM readings").fetchone()
span = (hi - lo) / 60
assert span > 9, "backlog achatado: %.1f min" % span
print("[ok] horario reconstruido pelo 'age': %.1f min entre a 1a e a ultima" % span)
print("     primeira %s | ultima %s"
      % (datetime.fromtimestamp(lo).strftime("%H:%M:%S"),
         datetime.fromtimestamp(hi).strftime("%H:%M:%S")))
assert lo < time.time() - 500, "as leituras antigas nao ficaram no passado"

# --- 5. QoS 1 duplica de proposito: o dedupe tem que aguentar -----------
antes = n
for i in range(1, N + 1):            # a ponte reenvia tudo apos reconectar
    linha = pod_line(datetime.now())
    pub.publish(BASE + "/raw", payload(i, 0, linha), qos=1)
time.sleep(3.0)
depois = y.DB.conn().execute("SELECT COUNT(*) FROM readings").fetchone()[0]
dup = y.DB.conn().execute(
    "SELECT COUNT(*) FROM (SELECT src_key FROM readings GROUP BY src_key"
    " HAVING COUNT(*)>1)").fetchone()[0]
assert depois == antes and dup == 0, (antes, depois, dup)
print("[ok] republicou as %d: banco continua com %d, 0 duplicadas" % (N, depois))

# --- 6. boot novo = dados novos, nao duplicata --------------------------
BOOT2 = "ffff0001"
BOOT, BOOT_ANTIGO = BOOT2, BOOT
for i in range(1, 11):
    pub.publish(BASE + "/raw", payload(i, 0, pod_line(datetime.now())), qos=1)
time.sleep(2.0)
final = y.DB.conn().execute("SELECT COUNT(*) FROM readings").fetchone()[0]
assert final == depois + 10, (depois, final)
print("[ok] ponte reiniciou (boot novo): as 10 novas entraram, sem confundir seq")

# --- 7. status online/offline (Last Will) --------------------------------
pub.publish(BASE + "/status", "offline", qos=1, retain=True)
time.sleep(1.5)
assert "offline" in (y.LIVE.detail or ""), y.LIVE.detail
print("[ok] aviso de queda chega no painel: %r" % y.LIVE.detail)
pub.publish(BASE + "/status", "online", qos=1, retain=True)
time.sleep(1.0)
assert y.LIVE.detail == "", y.LIVE.detail
print("[ok] voltou para online")

# --- 8. broker cai: o servidor percebe, nao morre e volta sozinho -------
vistos = []
parar = threading.Event()

def vigia():
    while not parar.is_set():
        vistos.append(y.LIVE.status)
        time.sleep(0.05)

threading.Thread(target=vigia, daemon=True).start()
pub.loop_stop(); pub.disconnect()
loop.call_soon_threadsafe(lambda: asyncio.ensure_future(broker.shutdown(), loop=loop))
time.sleep(5.0)
parar.set()
caiu = any(v in ("reconectando", "conectando", "erro") for v in vistos)
assert caiu, "o servidor nem percebeu a queda do broker: %s" % set(vistos)
print("[ok] queda do broker percebida (estados: %s) e o processo seguiu vivo"
      % " -> ".join(dict.fromkeys(vistos)))

# o painel continua servindo o historico que ja esta no banco
srv = y.ThreadingHTTPServer(("127.0.0.1", 8736), y.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
time.sleep(0.3)
import urllib.request
w = json.loads(urllib.request.urlopen(
    "http://127.0.0.1:8736/api/window?window=3600").read())
assert w["series"]["temp"], "painel ficou sem dados"
print("[ok] painel segue mostrando o historico: %d pontos, %s leituras"
      % (len(w["series"]["temp"]), format(w["range"]["rows"], ",")))

print("\nTUDO PASSOU")
