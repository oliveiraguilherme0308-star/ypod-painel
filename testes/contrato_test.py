"""
Contrato firmware <-> servidor.

Os formatos das mensagens sao EXTRAIDOS do .ino e usados para montar o que a
ponte realmente manda. Assim, se alguem mexer no printf do firmware sem mexer no
parser do Python (ou vice-versa), este teste quebra -- que foi exatamente o furo
que passou batido quando o teste usava uma ponte falsa escrita a mao.
"""
import os, re, shutil, socket, sys, threading, time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ypod_monitor as y

INO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "firmware", "ypod_bridge", "ypod_bridge.ino")
TMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "contrato")
shutil.rmtree(TMP, ignore_errors=True)
os.makedirs(TMP)
y.DATA_DIR = TMP
y.DB = y.Db(os.path.join(TMP, "ypod.db"))

src = open(INO, encoding="utf-8").read()


def formato(marcador):
    """Pega a string de formato do printf/snprintf que contem `marcador`."""
    for m in re.finditer(r'"((?:[^"\\]|\\.)*)"', src):
        s = m.group(1)
        if marcador in s:
            return s.encode().decode("unicode_escape")
    raise AssertionError("formato com %r nao encontrado no firmware" % marcador)


def c_para_py(f):
    return (f.replace("%08lx", "%08x").replace("%lu", "%d")
             .replace("%u", "%d").replace("%s", "%s"))


FMT_HELLO = c_para_py(formato("$HELLO"))
FMT_LINHA = c_para_py(formato("#%lu"))
FMT_LIVE  = c_para_py(formato("$LIVE"))
FMT_PING  = c_para_py(formato("$PING"))
FMT_BEACON = c_para_py(formato("YPOD-BRIDGE"))
print("formatos lidos do firmware:")
for n, f in (("HELLO", FMT_HELLO), ("LINHA", FMT_LINHA), ("LIVE", FMT_LIVE),
             ("PING", FMT_PING), ("BEACON", FMT_BEACON)):
    print("   %-7s %r" % (n, f))

# confere que a linha leva 3 campos separados por TAB: seq, millis, conteudo
assert FMT_LINHA.count("\t") == 2, "a linha do firmware nao tem seq+millis+dados"

BOOT = 0xa1b2c3d4
PORT = 13555
T0 = time.time() - 900          # a ponte "ligou" 15 min atras


def pod_line(when):
    return ("%s,,,YPODV6,YPOD_V4.2.1,,,22.66,61.41,,2537,235,65535,,3823,2970,"
            "748.00,4,6,6," % when.strftime("%Y-%m-%dT%H:%M:%S"))


BACKLOG = []                     # (seq, millis, linha)
for i in range(1, 201):          # 10 min a cada 3 s
    ms = i * 3000
    BACKLOG.append((i, ms, pod_line(datetime.fromtimestamp(T0 + ms / 1000))))


class Ponte(threading.Thread):
    """Fala exatamente o que o firmware falaria."""
    daemon = True

    def __init__(self):
        super().__init__()
        self.cmd = None
        self.srv = socket.socket()
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", PORT))
        self.srv.listen(1)

    def millis(self):
        return int((time.time() - T0) * 1000)

    def run(self):
        cli, _ = self.srv.accept()
        f = cli.makefile("rwb")
        f.write((FMT_HELLO % (BOOT, len(BACKLOG), len(BACKLOG), 1,
                              self.millis())).encode())
        f.flush()
        self.cmd = f.readline().decode().strip()
        comeco = 1 if int(self.cmd.split()[1]) < 0 else int(self.cmd.split()[1]) + 1
        for seq, ms, linha in BACKLOG:
            if seq >= comeco:
                f.write((FMT_LINHA % (seq, ms, linha)).encode())
        f.write((FMT_LIVE % self.millis()).encode())
        f.flush()
        seq = len(BACKLOG)
        while True:
            time.sleep(0.2)
            seq += 1
            f.write((FMT_LINHA % (seq, self.millis(), pod_line(datetime.now()))).encode())
            f.flush()


# --- beacon no formato do firmware --------------------------------------
def beacon():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    msg = FMT_BEACON % ("ypod-bridge", "127.0.0.1", PORT, BOOT, 5)
    for _ in range(14):
        s.sendto(msg.encode(), ("127.0.0.1", y.BRIDGE_BEACON_PORT))
        time.sleep(0.25)

threading.Thread(target=beacon, daemon=True).start()
achado = y.discover_bridge(timeout=4.0)
assert achado == ("127.0.0.1", PORT), achado
print("\n[ok] beacon do firmware entendido pelo servidor ->", achado)

# --- stream --------------------------------------------------------------
p = Ponte()
p.start()
time.sleep(0.2)
threading.Thread(target=y.tcp_loop, args=("127.0.0.1", PORT, False), daemon=True).start()
time.sleep(4.0)

n = y.DB.conn().execute("SELECT COUNT(*) FROM readings").fetchone()[0]
assert n >= 200, "so %d linhas gravadas" % n
assert p.cmd.startswith("SINCE"), p.cmd
print("[ok] handshake: ponte mandou $HELLO, servidor respondeu %r" % p.cmd)
print("[ok] %d leituras gravadas a partir do formato real do firmware" % n)

# --- o ponto que estava quebrado: horario do backlog ---------------------
lo, hi = y.DB.conn().execute("SELECT MIN(ts), MAX(ts) FROM readings").fetchone()
span_min = (hi - lo) / 60
assert span_min > 9, ("backlog achatado no tempo: %.1f min de diferenca entre a "
                      "primeira e a ultima leitura (esperado ~10)" % span_min)
print("[ok] backlog espalhado no tempo certo: %.1f min entre a 1a e a ultima" % span_min)
print("     primeira %s | ultima %s"
      % (datetime.fromtimestamp(lo).strftime("%H:%M:%S"),
         datetime.fromtimestamp(hi).strftime("%H:%M:%S")))

# as leituras do backlog tem que cair ANTES de agora, nao todas no instante do envio
antigas = y.DB.conn().execute(
    "SELECT COUNT(*) FROM readings WHERE ts < ?", (time.time() - 300,)).fetchone()[0]
assert antigas > 100, "so %d leituras ficaram no passado" % antigas
print("[ok] %d leituras ficaram no passado (reenvio nao carimbou tudo como agora)"
      % antigas)

# --------------------------------------------------------------------------
# Contrato dos INDICES dos campos
# --------------------------------------------------------------------------
# Desde a compactacao do buffer, o firmware tambem precisa saber em que posicao
# da linha fica cada sensor -- para conseguir tirar a media. Sao dois lugares
# que precisam concordar: CAMPOS_MEDIA/IDX_* em ponte.h e FIELDS/META em
# ypod_monitor.py. Divergindo, a ponte calcularia a media da coluna errada e
# mandaria numeros plausiveis e falsos. Este bloco compara os dois.
PONTE_H = os.path.join(os.path.dirname(INO), "ponte.h")
ph = open(PONTE_H, encoding="utf-8").read()


def define(nome):
    m = re.search(r"^#define\s+%s\s+(\S+)" % re.escape(nome), ph, re.M)
    assert m, "#define %s nao encontrado em ponte.h" % nome
    return int(m.group(1))


# indices dos campos numericos declarados no firmware
m = re.search(r"#define CAMPOS_MEDIA\s*\{(.*?)\}\s*$", ph, re.S | re.M)
assert m, "CAMPOS_MEDIA nao encontrado em ponte.h"
pares = re.findall(r"\{\s*(\d+)\s*,\s*(\d+)\s*\}", m.group(1))
idx_firmware = [int(a) for a, _ in pares]
circulares_fw = [int(a) for a, b in pares if int(b)]

idx_servidor = [f[0] for f in y.FIELDS]
assert idx_firmware == idx_servidor, (
    "indices divergentes!\n  ponte.h:        %s\n  ypod_monitor.py: %s"
    % (idx_firmware, idx_servidor))
print("[ok] indices dos %d campos batem entre ponte.h e ypod_monitor.py"
      % len(idx_firmware))

assert define("N_CAMPOS_MEDIA") == len(y.FIELDS), (
    "N_CAMPOS_MEDIA=%d mas FIELDS tem %d" % (define("N_CAMPOS_MEDIA"), len(y.FIELDS)))
assert define("IDX_POD_ID") == y.META["pod_id"]
assert define("IDX_FIRMWARE") == y.META["firmware"]
assert define("CAMPOS_NA_LINHA") == y.MIN_CAMPOS, (
    "o firmware acha que a linha tem %d campos e o servidor exige %d"
    % (define("CAMPOS_NA_LINHA"), y.MIN_CAMPOS))
print("[ok] contagem de campos e posicoes de pod_id/firmware tambem batem")

# as circulares tem que ser as mesmas dos dois lados
circ_servidor = [f[0] for f in y.FIELDS if f[1] in y.CIRCULARES]
assert circulares_fw == circ_servidor, (
    "campos circulares divergem: firmware %s, servidor %s"
    % (circulares_fw, circ_servidor))
print("[ok] campos circulares (media por vetor) iguais nos dois: %s" % circ_servidor)

# --------------------------------------------------------------------------
# A linha compactada tem que ser aceita pelo parser
# --------------------------------------------------------------------------
# O firmware monta a media no mesmo formato da linha do pod. Se ele errar a
# contagem de campos, o servidor -- que desde 24/10 recusa linha fora do
# formato -- descarta tudo em silencio. Aqui montamos a linha exatamente como
# as regras de ponte.h mandam e conferimos que ela passa.
n_campos = max(idx_firmware) + 1
campos = [""] * max(n_campos, define("CAMPOS_NA_LINHA"))
campos[define("IDX_POD_TIME")] = "2026-07-31T10:53:37"
campos[define("IDX_POD_ID")] = "YPODV6"
campos[define("IDX_FIRMWARE")] = "YPOD_V4.2.1"
valores = {7: "28.84", 8: "61.27", 10: "2577.00", 11: "468.00", 12: "4500.00",
           14: "4496.00", 15: "2868.00", 16: "1390.00", 17: "16.00",
           18: "28.00", 19: "28.00", 20: "120.5", 21: "47.5"}
for i, v in valores.items():
    campos[i] = v
linha_media = ",".join(campos)

r = y.parse_line(linha_media)
assert r is not None, ("o servidor recusaria a linha compactada da ponte:\n  %s"
                       % linha_media)
_, v, meta = r
assert abs(v["temp"] - 28.84) < 0.01
assert abs(v["vento_dir"] - 47.5) < 0.01
assert meta["pod_id"] == "YPODV6"
print("[ok] linha compactada (%d campos) aceita pelo parser, valores no lugar certo"
      % len(campos))


# ---------------------------------------------------------------------------
# Terceiro lugar do contrato: o Worker da Cloudflare.
#
# Ate 30/10 este teste comparava SO ponte.h com ypod_monitor.py, e por isso nao
# viu que o worker/ tinha ficado com o mapa de campos de antes de 25/10. O push
# e lido por NOME (json_extract '$.<chave>'), entao chave faltando nao da erro:
# o PC manda, o Worker descarta calado. Quatro campos (tvoc, o3, co_we, co_ae)
# ficaram cinco dias fora do historico publico enquanto a tela mostrava certo,
# porque o /api/live e retrato repassado do PC e nao passa por essas colunas.
#
# Sao TRES arquivos que precisam concordar. Agora os tres sao comparados.
# ---------------------------------------------------------------------------
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKER_JS = os.path.join(RAIZ, "worker", "src", "index.js")
SCHEMA_SQL = os.path.join(RAIZ, "worker", "schema.sql")

js = open(WORKER_JS, encoding="utf-8").read()
m = re.search(r"const\s+KEYS\s*=\s*\[(.*?)\]\s*;", js, re.S)
assert m, "const KEYS nao encontrado em worker/src/index.js"
keys_worker = re.findall(r'"([A-Za-z0-9_]+)"', m.group(1))
assert keys_worker == y.KEYS, (
    "KEYS do Worker difere do servidor.\n"
    "  worker/src/index.js : %s\n"
    "  ypod_monitor.py     : %s\n"
    "  so no Worker        : %s\n"
    "  so no servidor      : %s"
    % (keys_worker, y.KEYS,
       sorted(set(keys_worker) - set(y.KEYS)),
       sorted(set(y.KEYS) - set(keys_worker))))
print("[ok] KEYS do Worker igual ao do servidor (%d campos, mesma ordem)"
      % len(keys_worker))

sql = open(SCHEMA_SQL, encoding="utf-8").read()


def bloco(tabela):
    m = re.search(r"CREATE TABLE IF NOT EXISTS %s\s*\((.*?)\n\);" % tabela, sql, re.S)
    assert m, "CREATE TABLE %s nao encontrado em schema.sql" % tabela
    # tira os comentarios -- "-- ..." pode citar nome de coluna no texto
    return "\n".join(l.split("--")[0] for l in m.group(1).splitlines())


# leituras: uma coluna REAL por campo, fora o ts
cols = [c for c in re.findall(r"(\w+)\s+REAL", bloco("leituras")) if c != "ts"]
assert cols == y.KEYS, (
    "colunas de leituras em schema.sql diferem de KEYS.\n"
    "  schema.sql      : %s\n"
    "  ypod_monitor.py : %s\n"
    "  so no schema    : %s\n"
    "  so no servidor  : %s"
    % (cols, y.KEYS,
       sorted(set(cols) - set(y.KEYS)), sorted(set(y.KEYS) - set(cols))))
print("[ok] colunas da tabela leituras iguais a KEYS (%d)" % len(cols))

# hora: quatro colunas por campo (_s/_n/_lo/_hi); a chave sai do _s
hora_src = bloco("hora")
hora_keys = re.findall(r"(\w+)_s\s+REAL", hora_src)
assert hora_keys == y.KEYS, (
    "colunas da tabela hora em schema.sql diferem de KEYS.\n"
    "  schema.sql      : %s\n"
    "  ypod_monitor.py : %s\n"
    "  so no schema    : %s\n"
    "  so no servidor  : %s"
    % (hora_keys, y.KEYS,
       sorted(set(hora_keys) - set(y.KEYS)), sorted(set(y.KEYS) - set(hora_keys))))
for k in y.KEYS:
    for suf in ("_s", "_n", "_lo", "_hi"):
        assert re.search(r"\b%s%s\s+(REAL|INTEGER)" % (k, suf), hora_src), \
            "falta a coluna %s%s na tabela hora" % (k, suf)
print("[ok] tabela hora tem _s/_n/_lo/_hi para os %d campos" % len(hora_keys))

print("\nTUDO PASSOU")
