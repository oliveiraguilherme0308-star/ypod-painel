#!/usr/bin/env python3
"""
YPOD monitor - mini servidor
----------------------------
Recebe as linhas do YPOD (pela ponte ESP32 na rede, ou direto pelo USB),
guarda tudo num banco SQLite e serve o painel web.

    YPOD --> ESP32-S3 --> Wi-Fi --\
                                   +--> ypod_monitor.py --> navegador
    YPOD --> USB (COM5) ----------/

Uso:
    python ypod_monitor.py                 # acha a ponte na rede; se nao achar, USB
    python ypod_monitor.py --source net    # so rede
    python ypod_monitor.py --source serial --port COM5
    python ypod_monitor.py --host 192.168.0.42
    python ypod_monitor.py --list          # portas seriais da maquina
"""

import argparse
import csv
import io
import json
import math
import os
import re
import socket
import sqlite3
import sys
import threading
import time
import webbrowser
from collections import deque
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, quote

try:
    import serial
    from serial.tools import list_ports
except ImportError:                                   # a serial e opcional no modo rede
    serial = None
    list_ports = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
STATIC_DIR = os.path.join(BASE_DIR, "static")
DB_PATH = os.path.join(DATA_DIR, "ypod.db")

# ---------------------------------------------------------------------------
# MAPA DE COLUNAS
# ---------------------------------------------------------------------------
# A linha do YPOD e um CSV com varios campos vazios, por exemplo:
#
#   2026-07-30T04:25:39,,,YPODV6,YPOD_V4.2.1,,,22.66,61.41,,2537,235,65535,,3823,2970,748.00,4,6,6,
#   [0]                 1 2 [3]  [4]         5 6 [7]  [8]   9 [10][11][12] 13[14][15][16]  [17-19]
#
# Este mapa NAO foi deduzido: foi lido do firmware do proprio pod, em
# github.com/HanniganAirQuality/YPOD, pasta YPOD_V4.2.1 -- a mesma versao que
# este pod informa no campo 4. A ordem das colunas sai do .ino e a identidade
# dos sensores sai do cabecalho de ads_module.h:
#
#   "ADS1115 to include Fig 2600, Fig 2602, MiCS-2611, CO-B4"
#
# Dois campos eram rotulados errado ate 25/10: o 12 nao era um terceiro MOx, e
# sim o sensor de OZONIO (MiCS-2611), e o 15 nao era "auxiliar/O3", e sim o
# segundo eletrodo do MESMO sensor de CO (o Alphasense CO-B4 tem eletrodo ativo
# e auxiliar, este ultimo para compensar deriva termica).
#
# (Colunas novas sao criadas no banco sozinhas; as antigas ficam.)
#
#   idx, chave, rotulo, unidade, grupo, slot de cor
FIELDS = [
    (7,  "temp",    "Temperatura",  "°C",     "clima",   1),
    (8,  "rh",      "Umidade rel.", "%",      "umidade", 3),
    # Campos 9 e 13 so sao preenchidos com CALIBRATE = 1 no YPOD_node.h do pod.
    # Hoje saem vazios -- e e por isso que falta o COV, que e requisito do
    # projeto. Mapeados desde ja: ligada a calibracao, o dado cai sozinho aqui.
    (9,  "tvoc",    "COV total",    "",       "cov",     1),
    (10, "fig2600", "TGS2600",      "ADC",    "gases",   1),
    (11, "fig2602", "TGS2602",      "ADC",    "gases",   2),
    (12, "o3",      "Ozônio",       "ADC",    "gases",   3),
    (13, "co",      "CO",           "ppm",    "co",      1),
    (14, "co_we",   "CO · eletrodo ativo", "ADC", "gases", 4),
    (15, "co_ae",   "CO · eletrodo aux.",  "ADC", "gases", 7),
    (16, "co2",     "CO₂",          "ppm",    "co2",     2),
    (17, "pm1",     "PM1.0",        "µg/m³",  "pm",      1),
    (18, "pm25",    "PM2.5",        "µg/m³",  "pm",      2),
    (19, "pm10",    "PM10",         "µg/m³",  "pm",      3),
    # Acrescentados pela ponte no fim da linha do pod: sao sensores do ESP,
    # nao do YPOD. Se o ima do AS5600 sair do lugar, os campos vem vazios.
    (20, "vento_vel", "Vel. do vento",   "rpm",  "vento_vel", 4),
    (21, "vento_dir", "Dir. do vento",   "°",    "vento_dir", 7),
]

META = {"pod_id": 3, "firmware": 4}

# Grupos viram um grafico cada. Series dentro de um grafico compartilham a
# mesma unidade -- nunca dois eixos y no mesmo grafico.
GROUPS = [
    ("clima",   "Temperatura",           "°C"),
    ("umidade", "Umidade relativa",      "%"),
    ("co2",     "CO₂",                   "ppm"),
    ("co",      "Monóxido de carbono",   "ppm"),
    ("pm",      "Material particulado",  "µg/m³"),
    ("cov",     "Compostos orgânicos voláteis", ""),
    ("gases",   "Sinal bruto dos sensores de gás", "contagens ADC"),
    ("vento_vel", "Velocidade do vento",  "rpm"),
    ("vento_dir", "Direção do vento",     "graus"),
]

# Valores sentinela de sensor desconectado / defeituoso.
INVALID = {-999.0, 9999.0}
# Canal saturado / sensor aquecendo. O pod nao manda um valor fixo: ja vimos
# 65534, 65535 e 64537 (este ultimo nos primeiros minutos depois de energizar,
# com os MOx ainda aquecendo). Qualquer coisa nessa faixa e lixo, nao leitura --
# deixar passar vira um paredao que achata a escala do grafico de gases.
SATURADO = 60000.0
# O que cada valor acima de 60000 significa, lido do read_raw() em
# ads_module.cpp do firmware do pod:
#
#   64537  o ADS1115 nao inicializou. A funcao devolve -999 de um uint16_t, e
#          -999 em 16 bits sem sinal e 64537. Aparece nas cinco colunas de gas
#          ao mesmo tempo, em geral logo depois de energizar (I2C ainda nao
#          pronto). Nos dados ate 25/10: 6 linhas em 3246.
#   65535  a leitura do canal deu -1 em int16: entrada aberta le um pouco
#   65534  abaixo de zero. Sensor solto, morto, ou canal sem nada ligado.
#
# No pod desta estacao, o canal 12 (ozonio, MiCS-2611) esta em 65535/65534 em
# 99,2% das leituras, enquanto os outros quatro canais do MESMO ADS1115 vao bem
# em 99,8%. Isso e sensor, nao conversor.

# Uma linha inteira do pod tem EXATAMENTE 21 campos (a virgula final conta como
# um campo vazio); 22 quando a ponte acrescenta os dois do vento.
#
# A contagem e a invariante estrutural desta linha, e por isso o limite e 21 e
# nao "uns 20": este parser le POR POSICAO, entao um campo a menos desloca todo
# o resto e ele passa a ler a coluna errada -- sem erro, sem aviso, com numeros
# plausiveis. Os dois casos reais que motivaram isto:
#
#   20 campos: "2026-07-31T11:11:45,,,YPODV6YYPOD_V4.2.1,,,27.69,62.11,..."
#              a virgula entre pod_id e firmware virou um "Y" na serial; os
#              dois campos fundiram e a umidade foi gravada como temperatura.
#   14 campos: "28.65,61.09,,2564,415,65535,,4665,2876,..."
#              linha truncada; 4665, que e o ADC do sensor de CO, virou 4665
#              graus de temperatura no banco.
MIN_CAMPOS = 21

# Faixa fisicamente possivel de cada grandeza. E a rede de protecao generica:
# INVALID pega os sentinelas conhecidos e SATURADO pega o canal estourado, mas
# nenhum dos dois pega 128 C ou 2876% de umidade, que e o que sai quando a
# linha vem deslocada por um byte corrompido na serial.
#
# Quem nao esta aqui (os MOx em contagem de ADC) nao tem faixa fisica util --
# para esses, SATURADO ja basta.
FAIXAS = {
    "temp":      (-40.0, 85.0),      # faixa do proprio sensor
    "rh":        (0.0, 100.0),       # umidade relativa, por definicao
    "co2":       (0.0, 20000.0),     # acima disso nao e ar respiravel
    # CO calibrado: a equacao embarcada do pod pode devolver um pouco abaixo de
    # zero quando a concentracao e baixa -- deriva normal de sensor
    # eletroquimico. Cortar em zero esconderia isso; a faixa aceita a deriva e
    # barra o disparate.
    "co":        (-10.0, 2000.0),
    "pm1":       (0.0, 2000.0),
    "pm25":      (0.0, 2000.0),
    "pm10":      (0.0, 2000.0),
    "vento_vel": (0.0, 20000.0),     # rpm; so pega disparate
    "vento_dir": (0.0, 360.0),
}

KEYS = [f[1] for f in FIELDS]
CSV_HEADER = ["pc_time", "pod_time", "pod_id", "firmware"] + KEYS + ["raw"]

# Grandezas CIRCULARES: a escala da a volta, entao media aritmetica mente.
# A media de 350 e 10 graus e 180 -- a direcao oposta. E o erro nao e aleatorio:
# ele aparece exatamente quando o vento vem do NORTE, que e onde a escala
# fecha o circulo.
#
# O certo e somar os vetores unitarios e tirar o angulo da soma:
#     angulo = atan2(media dos senos, media dos cossenos)
#
# Minimo e maximo tambem nao significam nada num circulo (qual e o "menor"
# entre 350 e 10?), por isso para estas chaves so a media e publicada.
# Tupla, nao conjunto: a ordem precisa ser a mesma na hora de montar as colunas
# do SQL e na hora de ler o resultado.
CIRCULARES = ("vento_dir",)

# ---------------------------------------------------------------------------
# IQAr - Indice de Qualidade do Ar (CONAMA 506/2024)
# ---------------------------------------------------------------------------
# O indice e o MAIOR entre os sub-indices de cada poluente. Cada sub-indice sai
# de interpolacao linear dentro da faixa onde a concentracao caiu:
#
#     I = Iini + (Ifin - Iini)/(Cfin - Cini) * (C - Cini)      [Equacao 1]
#
# ORIGEM DE CADA LIMITE, porque isto vai para um trabalho que cita a norma:
#
#   fim da faixa "Boa"   CONAMA 506/2024, Anexo II -- lido da integra da
#                        resolucao. E a unica faixa que a resolucao define;
#                        sao os valores-guia da OMS 2021.
#   Moderada -> Ruim     padrao intermediario PI-2, CONAMA 506/2024 Anexo I.
#   Ruim -> Muito Ruim   Interim Target 1 (IT-1) da OMS 2021.
#   M. Ruim -> Pessima   nivel de atencao, CONAMA 491/2018.
#
# A tabela inteira foi CONFERIDA contra a Tabela 5 do "Relatorio de Evolucao da
# Qualidade do Ar da RMGV - 2024" do IEMA-ES, que reproduz as faixas novas e
# cita a Orientacao Tecnica do MMA (jan/2025) textualmente -- o PDF do MMA so
# abre com login no gov.br. Os limites de MP10 e MP2,5 batem nos cinco niveis,
# inclusive os topos da faixa "Pessima" (600 e 300).
#
# O CO naquele relatorio esta em µg/m³ porque o IEMA converteu; a norma usa
# ppm. A 25 °C, 9 ppm = 10.311 µg/m³, e a tabela deles diz 10.314 -- os quatro
# limites conferem, entao em ppm sao 9, 11, 13 e 15, como esta aqui.
#
# O unico numero que NAO e da norma e o teto de 50 ppm no fim da faixa do CO:
# a norma deixa "Pessima" aberta, e aqui ele existe so para a interpolacao
# parar em 400 em vez de crescer sem limite.
#
# PERIODO DE MEDIA (Anexo I): MP10 e MP2,5 em 24 h; O3 e CO em 8 h; NO2 em 1 h.
# Nao e o valor do minuto -- e media movel. Ver iqar_de_janela().
#
#   poluente: [(Cini, Iini, Cfin, Ifin), ...]
IQAR_FAIXAS = {
    "pm25": [(0, 0, 15, 40), (15, 40, 50, 80), (50, 80, 75, 120),
             (75, 120, 125, 200), (125, 200, 300, 400)],
    "pm10": [(0, 0, 45, 40), (45, 40, 100, 80), (100, 80, 150, 120),
             (150, 120, 250, 200), (250, 200, 600, 400)],
    "co":   [(0, 0, 9, 40), (9, 40, 11, 80), (11, 80, 13, 120),
             (13, 120, 15, 200), (15, 200, 50, 400)],
}
# Horas de media exigidas por poluente (Anexo I da 506/2024).
IQAR_JANELA_H = {"pm25": 24, "pm10": 24, "co": 8}

# Faixas de classificacao: (limite superior do indice, nome, cor)
IQAR_CLASSES = [
    (40,  "Boa",        "#00b050"),
    (80,  "Moderada",   "#ffd600"),
    (120, "Ruim",       "#ff8c00"),
    (200, "Muito ruim", "#e53935"),
    (400, "Péssima",    "#7b1fa2"),
]


def iqar_sub(valor, poluente):
    """Sub-indice de um poluente, ou None se nao houver faixa para ele."""
    faixas = IQAR_FAIXAS.get(poluente)
    if faixas is None or valor is None:
        return None
    for c_ini, i_ini, c_fim, i_fim in faixas:
        if c_ini <= valor <= c_fim:
            if c_fim == c_ini:
                return float(i_ini)
            return (i_fim - i_ini) / (c_fim - c_ini) * (valor - c_ini) + i_ini
    return 400.0 if valor > faixas[-1][2] else None


def iqar_classe(indice):
    for limite, nome, cor in IQAR_CLASSES:
        if indice <= limite:
            return nome, cor
    return IQAR_CLASSES[-1][1], IQAR_CLASSES[-1][2]


def iqar(medias):
    """Recebe {poluente: concentracao media no periodo} e devolve o indice.

    Devolve None quando nenhum poluente do indice tem dado. O campo
    `calculado_com` existe porque esta estacao so mede parte dos poluentes: o
    O3 esta com o sensor morto, NO2 e SO2 nao sao medidos, e o CO so tem
    unidade fisica depois que a calibracao do pod for ligada. Publicar o indice
    sem dizer de onde ele saiu seria passar por completo o que e parcial.
    """
    subs = {}
    for pol, valor in medias.items():
        s = iqar_sub(valor, pol)
        if s is not None:
            subs[pol] = round(s)
    if not subs:
        return None
    dominante = max(subs, key=subs.get)
    indice = subs[dominante]
    nome, cor = iqar_classe(indice)
    return {"indice": indice, "classe": nome, "cor": cor,
            "dominante": dominante, "sub": subs,
            "calculado_com": sorted(subs)}


def _sen_g(graus):
    return None if graus is None else math.sin(math.radians(graus))


def _cos_g(graus):
    return None if graus is None else math.cos(math.radians(graus))


def media_circular(soma_sen, soma_cos, casas=4):
    """Angulo medio em graus a partir das somas de seno e cosseno.

    Devolve sempre no intervalo [0, 360) -- nunca 360, que e o mesmo que 0 e
    confunde quem le. O arredondamento vem antes da normalizacao de proposito:
    359,99998 arredondado viraria "360,0", e e isso que precisa virar zero.
    """
    if soma_sen == 0.0 and soma_cos == 0.0:
        return None                      # vetores se cancelaram: sem direcao
    ang = math.degrees(math.atan2(soma_sen, soma_cos)) % 360.0
    return round(ang, casas) % 360.0

RAW_HISTORY = 2000              # leituras recentes em memoria (sparklines)
STALE_AFTER = 30                # s sem dados => "sem sinal"
MAX_POINTS = 1400               # pontos por serie devolvidos ao painel

BRIDGE_TCP_PORT = 3333
BRIDGE_BEACON_PORT = 3334
BRIDGE_HOSTNAME = "ypod-bridge.local"


# ---------------------------------------------------------------------------
# Banco
# ---------------------------------------------------------------------------
class Db:
    """SQLite com uma conexao por thread (WAL deixa leitura e escrita juntas)."""

    def __init__(self, path):
        self.path = path
        self._local = threading.local()
        self._roll_lock = threading.Lock()
        self._dirty_from = float("inf")   # leitura gravada "no passado" (backlog)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._setup()

    def conn(self):
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=20)
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            # sin()/radians() existem na maioria das builds do SQLite, mas nao
            # em todas -- sao opcionais em tempo de compilacao. Registrar as
            # nossas evita o resumo quebrar num PC com build diferente.
            c.create_function("sen_g", 1, _sen_g, deterministic=True)
            c.create_function("cos_g", 1, _cos_g, deterministic=True)
            self._local.conn = c
        return c

    def _setup(self):
        c = self.conn()
        cols = ", ".join("%s REAL" % k for k in KEYS)
        c.execute(
            "CREATE TABLE IF NOT EXISTS readings ("
            " id INTEGER PRIMARY KEY, ts REAL NOT NULL, src_key TEXT,"
            " pod_time TEXT, pod_id TEXT, firmware TEXT, raw TEXT, %s)" % cols)
        c.execute("CREATE INDEX IF NOT EXISTS idx_ts ON readings(ts)")
        # Dedupe do backlog da ponte: cada linha e unica por (boot do ESP, seq).
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_src ON readings(src_key)"
                  " WHERE src_key IS NOT NULL")
        c.execute("CREATE TABLE IF NOT EXISTS state (k TEXT PRIMARY KEY, v TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS imported (file TEXT PRIMARY KEY,"
                  " rows INTEGER, at TEXT)")
        # Resumo por minuto. Todo grafico usa balde de 1 min ou mais, entao ele
        # responde qualquer janela sem varrer as leituras cruas -- e a diferenca
        # entre "6 meses em 10 s" e "6 meses num piscar".
        roll = ", ".join("%s_s REAL, %s_n INTEGER, %s_lo REAL, %s_hi REAL"
                         % (k, k, k, k) for k in KEYS)
        # Para as grandezas circulares guardamos tambem a soma dos senos e dos
        # cossenos. Sem isso, juntar minutos num balde de hora voltaria a fazer
        # media aritmetica de angulo -- o mesmo erro, so que no desenho do
        # grafico em vez de na gravacao.
        roll += "".join(", %s_sen REAL, %s_cos REAL" % (k, k) for k in CIRCULARES)
        c.execute("CREATE TABLE IF NOT EXISTS roll_min"
                  " (m INTEGER PRIMARY KEY, n INTEGER, %s)" % roll)
        rcols = {r[1] for r in c.execute("PRAGMA table_info(roll_min)")}
        if ("n" not in rcols or any("%s_s" % k not in rcols for k in KEYS)
                or any("%s_sen" % k not in rcols for k in CIRCULARES)):
            c.execute("DROP TABLE roll_min")
            c.execute("CREATE TABLE roll_min (m INTEGER PRIMARY KEY, n INTEGER, %s)" % roll)
            c.execute("DELETE FROM state WHERE k='roll_until'")
            print("[db] resumo por minuto sera reconstruido")
        # Apareceu leitura mais antiga do que o resumo cobre (banco restaurado,
        # CSV importado a mao)? Entao o resumo e refeito do zero.
        lo = c.execute("SELECT MIN(ts) FROM readings").fetchone()[0]
        lo_roll = c.execute("SELECT MIN(m) FROM roll_min").fetchone()[0]
        if lo is not None and (lo_roll is None or lo < lo_roll * 60 - 60):
            c.execute("DELETE FROM state WHERE k='roll_until'")
        # Campo novo no FIELDS => coluna nova, sem perder o historico.
        have = {r[1] for r in c.execute("PRAGMA table_info(readings)")}
        for k in KEYS:
            if k not in have:
                c.execute("ALTER TABLE readings ADD COLUMN %s REAL" % k)
                print("[db] coluna nova: %s" % k)
        c.commit()

    # -- escrita ------------------------------------------------------------
    def insert(self, ts, values, meta, raw, src_key=None):
        cols = ["ts", "src_key", "pod_time", "pod_id", "firmware", "raw"] + KEYS
        vals = [ts, src_key, meta.get("pod_time"), meta.get("pod_id"),
                meta.get("firmware"), raw] + [values.get(k) for k in KEYS]
        c = self.conn()
        c.execute("INSERT OR IGNORE INTO readings (%s) VALUES (%s)"
                  % (",".join(cols), ",".join("?" * len(cols))), vals)
        c.commit()
        # Backlog da ponte e CSV importado chegam com hora antiga: o resumo por
        # minuto precisa voltar ate ali na proxima atualizacao.
        if ts < self._dirty_from:
            self._dirty_from = ts

    def set_state(self, k, v):
        c = self.conn()
        c.execute("INSERT OR REPLACE INTO state (k, v) VALUES (?, ?)", (k, str(v)))
        c.commit()

    def get_state(self, k, default=None):
        row = self.conn().execute("SELECT v FROM state WHERE k=?", (k,)).fetchone()
        return row[0] if row else default

    # -- leitura ------------------------------------------------------------
    def range(self):
        # MIN/MAX saem do indice; o total vem do resumo -- COUNT(*) em milhoes
        # de linhas custaria 300 ms em toda atualizacao do painel.
        self.refresh_rollup()
        c = self.conn()
        lo = c.execute("SELECT MIN(ts) FROM readings").fetchone()[0]
        hi = c.execute("SELECT MAX(ts) FROM readings").fetchone()[0]
        n = c.execute("SELECT SUM(n) FROM roll_min").fetchone()[0] or 0
        return {"first": lo, "last": hi, "rows": n}

    @staticmethod
    def bucket_for(span_s):
        """Largura do balde para o grafico nunca passar de MAX_POINTS pontos."""
        for b in (60, 120, 300, 600, 900, 1800, 3600, 7200, 21600, 43200, 86400):
            if span_s / b <= MAX_POINTS:
                return b
        return 86400

    def refresh_rollup(self):
        """Poe o resumo por minuto em dia. So recalcula do ultimo ponto em
        diante, entao o custo normal e de alguns minutos de dados."""
        with self._roll_lock:
            c = self.conn()
            since = min(float(self.get_state("roll_until") or 0), self._dirty_from)
            self._dirty_from = float("inf")
            newest = c.execute("SELECT MAX(ts) FROM readings").fetchone()[0]
            if newest is None or newest < since:
                return
            sel = ["COUNT(*)"]
            for k in KEYS:
                sel += ["SUM(%s)" % k, "COUNT(%s)" % k, "MIN(%s)" % k, "MAX(%s)" % k]
            cols = ["n"]
            for k in KEYS:
                cols += ["%s_s" % k, "%s_n" % k, "%s_lo" % k, "%s_hi" % k]
            for k in CIRCULARES:
                sel += ["SUM(sen_g(%s))" % k, "SUM(cos_g(%s))" % k]
                cols += ["%s_sen" % k, "%s_cos" % k]
            t = time.time()
            c.execute(
                "INSERT OR REPLACE INTO roll_min (m, %s)"
                " SELECT CAST(ts/60 AS INTEGER), %s FROM readings"
                " WHERE ts >= ? GROUP BY 1" % (",".join(cols), ",".join(sel)),
                (since,))
            c.commit()
            # o minuto corrente ainda vai receber leituras: refazemos ele depois
            self.set_state("roll_until", int(newest // 60) * 60)
            took = time.time() - t
            if took > 1:
                print("[db] resumo por minuto reconstruido em %.0fs" % took)

    def iqar_agora(self, ate=None):
        """IQAr no instante `ate`, com a janela de media que a norma pede.

        Cada poluente tem periodo proprio (MP em 24 h, CO em 8 h), entao e uma
        consulta por poluente -- nao uma janela so. Sai do resumo por minuto,
        que ja existe, e por isso custa quase nada mesmo com meses gravados.
        """
        self.refresh_rollup()
        c = self.conn()
        fim = time.time() if ate is None else ate
        medias, amostras = {}, {}
        for pol, horas in IQAR_JANELA_H.items():
            m0 = int((fim - horas * 3600) // 60)
            m1 = math.ceil(fim / 60)
            row = c.execute(
                "SELECT SUM(%s_s), SUM(%s_n) FROM roll_min WHERE m >= ? AND m < ?"
                % (pol, pol), (m0, m1)).fetchone()
            if row and row[1]:
                medias[pol] = row[0] / row[1]
                amostras[pol] = row[1]
        r = iqar(medias)
        if r is not None:
            r["janelas_h"] = {p: IQAR_JANELA_H[p] for p in r["calculado_com"]}
            r["amostras"] = {p: amostras[p] for p in r["calculado_com"]}
            r["medias"] = {p: round(medias[p], 2) for p in r["calculado_com"]}
        return r

    def series(self, t0, t1):
        self.refresh_rollup()
        c = self.conn()
        # bordas alinhadas ao minuto: 14:00->15:00 pega exatamente 60 minutos
        m0, m1 = int(t0 // 60), math.ceil(t1 / 60)
        # O balde segue o periodo que REALMENTE tem dados: pedir "30 dias" com
        # um dia gravado nao pode virar um ponto so.
        row = c.execute("SELECT MIN(m), MAX(m) FROM roll_min WHERE m >= ? AND m < ?",
                        (m0, m1)).fetchone()
        if row[0] is None:
            return {k: [] for k in KEYS}, 60
        b = self.bucket_for(max((row[1] - row[0]) * 60, 60))
        # As circulares saem como par (soma dos senos, soma dos cossenos) e o
        # angulo e montado aqui; as demais saem ja como media.
        partes = []
        for k in KEYS:
            if k in CIRCULARES:
                partes.append("SUM(%s_sen), SUM(%s_cos)" % (k, k))
            else:
                partes.append("SUM(%s_s)/SUM(%s_n)" % (k, k))
        rows = c.execute(
            "SELECT CAST(m*60/%d AS INTEGER) g, %s FROM roll_min"
            " WHERE m >= ? AND m < ? GROUP BY g ORDER BY g" % (b, ", ".join(partes)),
            (m0, m1)).fetchall()
        out = {k: [] for k in KEYS}
        for row in rows:
            t = row[0] * b * 1000
            i = 1
            for k in KEYS:
                if k in CIRCULARES:
                    sen, cos = row[i], row[i + 1]
                    i += 2
                    if sen is not None and cos is not None:
                        ang = media_circular(sen, cos, 3)
                        if ang is not None:
                            out[k].append([t, ang])
                else:
                    if row[i] is not None:
                        out[k].append([t, round(row[i], 3)])
                    i += 1
        return out, b

    def stats(self, t0, t1):
        self.refresh_rollup()
        sel = []
        for k in KEYS:
            sel += ["MIN(%s_lo)" % k, "SUM(%s_s)" % k, "MAX(%s_hi)" % k, "SUM(%s_n)" % k]
        for k in CIRCULARES:
            sel += ["SUM(%s_sen)" % k, "SUM(%s_cos)" % k]
        row = self.conn().execute(
            "SELECT %s FROM roll_min WHERE m >= ? AND m < ?" % ",".join(sel),
            (int(t0 // 60), math.ceil(t1 / 60))).fetchone()
        out = {}
        for i, k in enumerate(KEYS):
            lo, total, hi, n = row[i * 4:i * 4 + 4]
            out[k] = ({"min": round(lo, 2), "avg": round(total / n, 2),
                       "max": round(hi, 2), "n": n} if n else None)
        # Nas circulares a media vem dos vetores, e minimo/maximo nao existem:
        # num circulo nao da para dizer qual dos dois angulos e "o menor".
        base = len(KEYS) * 4
        for j, k in enumerate(CIRCULARES):
            sen, cos = row[base + j * 2], row[base + j * 2 + 1]
            n = out[k]["n"] if out.get(k) else 0
            ang = (media_circular(sen, cos, 2)
                   if (sen is not None and cos is not None) else None)
            out[k] = ({"min": None, "avg": ang, "max": None, "n": n}
                      if n and ang is not None else None)
        return out

    def rows(self, t0, t1, limit=200):
        cur = self.conn().execute(
            "SELECT ts, %s FROM readings WHERE ts >= ? AND ts < ?"
            " ORDER BY ts DESC LIMIT ?" % ",".join(KEYS), (t0, t1, limit))
        return [(r[0], {k: r[i + 1] for i, k in enumerate(KEYS)}) for r in cur]

    def export_csv(self, t0, t1):
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(CSV_HEADER)
        cur = self.conn().execute(
            "SELECT ts, pod_time, pod_id, firmware, %s, raw FROM readings"
            " WHERE ts >= ? AND ts < ? ORDER BY ts" % ",".join(KEYS), (t0, t1))
        for r in cur:
            w.writerow([datetime.fromtimestamp(r[0]).isoformat(timespec="seconds")]
                       + list(r[1:4]) + list(r[4:4 + len(KEYS)]) + [r[-1]])
        return buf.getvalue()

    def import_csv_files(self):
        """Puxa os CSVs da versao antiga (data/ypod_AAAA-MM-DD.csv) uma unica vez."""
        if not os.path.isdir(DATA_DIR):
            return 0
        c = self.conn()
        done = {r[0] for r in c.execute("SELECT file FROM imported")}
        total = 0
        for name in sorted(os.listdir(DATA_DIR)):
            if not (name.startswith("ypod_") and name.endswith(".csv")) or name in done:
                continue
            n = 0
            with open(os.path.join(DATA_DIR, name), newline="", encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    try:
                        ts = datetime.fromisoformat(row["pc_time"]).timestamp()
                    except (ValueError, KeyError, TypeError):
                        continue
                    vals = {}
                    for k in KEYS:
                        cell = (row.get(k) or "").strip()
                        try:
                            vals[k] = float(cell) if cell else None
                        except ValueError:
                            vals[k] = None
                    self.insert(ts, vals, {"pod_time": row.get("pod_time"),
                                           "pod_id": row.get("pod_id"),
                                           "firmware": row.get("firmware")},
                                row.get("raw"), src_key="csv:%s:%d" % (name, n))
                    n += 1
            c.execute("INSERT OR REPLACE INTO imported VALUES (?,?,?)",
                      (name, n, datetime.now().isoformat(timespec="seconds")))
            c.commit()
            total += n
            print("[db] importei %d linhas de %s" % (n, name))
        return total


# ---------------------------------------------------------------------------
# Estado vivo (memoria): valor atual, sparklines, saude da conexao
# ---------------------------------------------------------------------------
class Live:
    def __init__(self):
        self.lock = threading.Lock()
        self.raw = deque(maxlen=RAW_HISTORY)
        self.latest = {}
        self.latest_ts = 0.0
        self.meta = {"pod_id": None, "firmware": None}
        self.count = 0
        self.gravadas = 0
        self.bad_lines = 0
        self.started = time.time()
        self.source = "-"
        self.status = "iniciando"
        self.detail = ""
        self.backlog = 0

    def add(self, ts, values, meta=None, live=True):
        with self.lock:
            self.count += 1
            if meta:
                self.meta.update({k: v for k, v in meta.items()
                                  if v and k in self.meta})
            if live or ts >= self.latest_ts:
                self.raw.append((ts, values))
                self.latest = values
                self.latest_ts = ts

    def snapshot(self):
        with self.lock:
            raw = list(self.raw)
            latest, ts = dict(self.latest), self.latest_ts
            meta = dict(self.meta)
        return raw, latest, ts, meta


DB = None
LIVE = Live()

# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
NUM_RE = re.compile(r"^-?\d+(?:\.\d+)?$")


def parse_number(token):
    token = token.strip()
    if not token or not NUM_RE.match(token):
        return None
    val = float(token)
    if val in INVALID or val >= SATURADO:
        return None
    return val


def parse_line(line):
    parts = line.split(",")
    # 1. A linha tem o formato esperado? Ler por posicao so faz sentido se as
    #    posicoes existirem.
    if len(parts) < MIN_CAMPOS:
        return None

    # 2. O primeiro campo e a data do pod. Se vier outra coisa -- um numero,
    #    por exemplo --, a linha esta deslocada e TODAS as posicoes seguintes
    #    apontam para a coluna errada. Nao da para adivinhar de quanto foi o
    #    deslocamento, entao descarta.
    #
    #    Isso cobre dois casos ja vistos de verdade:
    #      - linha truncada, comecando direto no "28.65,61.09,..."
    #      - virgula corrompida no caminho ("YPODV6YYPOD_V4.2.1"), que funde
    #        dois campos em um e empurra o resto uma casa para a esquerda
    #
    #    Data impossivel ("2000-84-90T00:03:00") tambem cai aqui: nao e so um
    #    relogio desacertado, e sinal de que o pod estava em mau estado quando
    #    montou a linha, e nenhum numero dela vale.
    pod_time = parts[0].strip()
    if pod_time:
        try:
            datetime.fromisoformat(pod_time)
        except ValueError:
            return None

    # 3. Cada valor ainda precisa ser fisicamente possivel.
    values = {}
    got = 0
    for item in FIELDS:
        idx, key = item[0], item[1]
        v = parse_number(parts[idx]) if idx < len(parts) else None
        if v is not None:
            faixa = FAIXAS.get(key)
            if faixa is not None and not (faixa[0] <= v <= faixa[1]):
                v = None          # sensor fora da faixa: descarta o valor,
                                  # nao a linha -- os outros podem estar bons
        values[key] = v
        if v is not None:
            got += 1
    if got == 0:
        return None
    meta = {name: (parts[i].strip() if i < len(parts) else "")
            for name, i in META.items()}
    meta["pod_time"] = pod_time
    return pod_time, values, meta


class AcumuladorPorMinuto:
    """Junta as leituras de cada minuto e devolve uma media quando o minuto fecha.

    A tela nao passa por aqui: os valores atuais vem da memoria (LIVE) e seguem
    em tempo real. Isto e so sobre o que vai parar no banco.

    Quem agrega e o servidor, nao a ponte. Assim a ponte continua guardando e
    reenviando cada leitura, e trocar GRAVACAO_S para 0 volta a gravar tudo sem
    precisar tocar no firmware.
    """

    def __init__(self, janela_s=60):
        self.janela = janela_s
        self.abertos = {}        # inicio_do_balde -> {chave: [soma, n]}
        self.meta = {}           # inicio_do_balde -> (meta, linha crua)
        self.ultimo_visto = 0.0

    def add(self, ts, values, meta, line):
        """Devolve a lista de baldes fechados por esta leitura."""
        balde = int(ts // self.janela) * self.janela
        acc = self.abertos.setdefault(balde, {})
        for k, v in values.items():
            if v is None:
                continue
            if k in CIRCULARES:
                # guarda [soma dos senos, soma dos cossenos, n]
                r = math.radians(v)
                par = acc.get(k)
                if par is None:
                    acc[k] = [math.sin(r), math.cos(r), 1]
                else:
                    par[0] += math.sin(r)
                    par[1] += math.cos(r)
                    par[2] += 1
                continue
            par = acc.get(k)
            if par is None:
                acc[k] = [v, 1]
            else:
                par[0] += v
                par[1] += 1
        self.meta[balde] = (meta, line)
        self.ultimo_visto = max(self.ultimo_visto, ts)
        return self._fechar(antes_de=balde)

    def _fechar(self, antes_de):
        prontos = []
        for b in sorted(self.abertos):
            if b >= antes_de:
                break
            acc = self.abertos.pop(b)
            meta, line = self.meta.pop(b, ({}, ""))
            medias = {}
            for k, par in acc.items():
                if k in CIRCULARES:
                    sen, cos, n = par
                    ang = media_circular(sen / n, cos / n, 4)
                    if ang is not None:
                        medias[k] = ang
                else:
                    medias[k] = round(par[0] / par[1], 4)
            n_total = max((par[-1] for par in acc.values()), default=0)
            # o carimbo e o meio do balde: representa melhor a media do periodo
            prontos.append((b + self.janela / 2.0, medias, meta, line, n_total, b))
        return prontos

    def fechar_tudo(self):
        return self._fechar(antes_de=float("inf"))


ACUM = None


def ingest(ts, line, src_key=None, live=True):
    """Uma linha crua -> memoria (tempo real) + banco (a cada GRAVACAO_S).

    Devolve True se era uma leitura valida.
    """
    parsed = parse_line(line)
    if not parsed:
        LIVE.bad_lines += 1
        return False
    _, values, meta = parsed

    # 1. memoria: sempre, leitura a leitura. E o que a tela mostra.
    LIVE.add(ts, values, meta, live=live)

    # 2. banco: so quando o balde de tempo fecha.
    if ACUM is None:                      # GRAVACAO_S = 0 -> grava tudo
        try:
            DB.insert(ts, values, meta, line, src_key)
        except sqlite3.Error as exc:
            print("[db] erro gravando: %s" % exc)
        return True

    for ts_balde, medias, m, linha, n, inicio in ACUM.add(ts, values, meta, line):
        m = dict(m)
        m["amostras"] = n
        try:
            # a chave do minuto torna a gravacao idempotente: reenvio do mesmo
            # periodo nao cria linha duplicada
            DB.insert(ts_balde, medias, m, linha, "min:%d" % inicio)
            LIVE.gravadas += 1
        except sqlite3.Error as exc:
            print("[db] erro gravando: %s" % exc)
    return True


# ---------------------------------------------------------------------------
# Fonte 1: USB / serial
# ---------------------------------------------------------------------------
def serial_loop(port, baud, echo=False):
    if serial is None:
        LIVE.status = "erro"
        LIVE.detail = "pyserial nao instalado (pip install pyserial)"
        return
    LIVE.source = "USB %s @ %d" % (port, baud)
    while True:
        ser = None
        try:
            LIVE.status = "conectando"
            ser = serial.Serial(port, baud, timeout=2)
            time.sleep(0.3)
            ser.reset_input_buffer()
            LIVE.status = "conectado"
            LIVE.detail = ""
            print("[serial] %s @ %d aberta" % (port, baud))
            while True:
                # readline() com timeout devolve b"" quando o pod esta calado;
                # nesse caso seguimos esperando -- reabrir a porta resetaria a
                # placa (DTR) e perderia dados.
                chunk = ser.readline()
                if not chunk:
                    continue
                line = chunk.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                if echo:
                    print(line)
                ingest(time.time(), line)
        except serial.SerialException as exc:
            LIVE.status = "erro"
            LIVE.detail = str(exc)
            print("[serial] %s" % exc)
        except Exception as exc:                      # noqa: BLE001
            LIVE.status = "erro"
            LIVE.detail = repr(exc)
            print("[serial] falha inesperada: %r" % exc)
        finally:
            if ser:
                try:
                    ser.close()
                except Exception:                     # noqa: BLE001
                    pass
        LIVE.status = "reconectando"
        time.sleep(3)


# ---------------------------------------------------------------------------
# Fonte 2: ponte ESP32 na rede
# ---------------------------------------------------------------------------
def discover_bridge(timeout=5.0, verbose=True):
    """Acha a ponte: primeiro o beacon UDP que ela manda, depois o mDNS."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("", BRIDGE_BEACON_PORT))
        sock.settimeout(timeout)
        if verbose:
            print("[rede] procurando a ponte (beacon UDP, %.0fs)..." % timeout)
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                data, addr = sock.recvfrom(256)
            except socket.timeout:
                break
            text = data.decode("utf-8", "replace")
            if not text.startswith("YPOD-BRIDGE"):
                continue
            info = dict(p.split("=", 1) for p in text.split()[1:] if "=" in p)
            host = info.get("ip", addr[0])
            port = int(info.get("port", BRIDGE_TCP_PORT))
            if verbose:
                print("[rede] ponte encontrada em %s:%d" % (host, port))
            return host, port
    except OSError as exc:
        if verbose:
            print("[rede] nao consegui escutar o beacon (%s)" % exc)
    finally:
        sock.close()
    try:                                   # plano B: mDNS (precisa de Bonjour)
        host = socket.gethostbyname(BRIDGE_HOSTNAME)
        if verbose:
            print("[rede] ponte via mDNS: %s" % host)
        return host, BRIDGE_TCP_PORT
    except OSError:
        return None


def tcp_loop(host, port, echo=False):
    """Le o stream da ponte, pedindo de onde parou para nao perder nem repetir."""
    while True:
        target = (host, port)
        if host in (None, "auto"):
            found = discover_bridge()
            if not found:
                LIVE.status = "procurando"
                LIVE.detail = "nenhuma ponte respondeu na rede"
                time.sleep(5)
                continue
            target = found
        LIVE.source = "rede %s:%d" % target
        sock = None
        try:
            LIVE.status = "conectando"
            sock = socket.create_connection(target, timeout=10)
            sock.settimeout(45)             # a ponte manda $PING a cada 15 s
            stream = sock.makefile("rwb")
            LIVE.status = "conectado"
            LIVE.detail = ""
            print("[rede] conectado em %s:%d" % target)

            boot = None
            clock_offset = None             # epoch do PC - millis da ponte/1000
            live = False

            for raw in stream:
                text = raw.decode("utf-8", "replace").strip()
                if not text:
                    continue

                if text.startswith("$"):
                    parts = text.split()
                    info = dict(p.split("=", 1) for p in parts[1:] if "=" in p)
                    if "now" in info:
                        clock_offset = time.time() - int(info["now"]) / 1000.0
                    if parts[0] == "$HELLO":
                        boot = info.get("boot", "?")
                        last = DB.get_state("seq:" + boot)
                        since = int(last) if last else -1
                        LIVE.backlog = max(0, int(info.get("seq", 0)) - max(since, 0))
                        stream.write(("SINCE %d\n" % since).encode())
                        stream.flush()
                        print("[rede] ponte boot=%s seq=%s - pedindo a partir de %d"
                              % (boot, info.get("seq"), since + 1))
                    elif parts[0] == "$LIVE":
                        live = True
                        LIVE.backlog = 0
                        print("[rede] em tempo real")
                    elif parts[0] == "$BUSY":
                        LIVE.detail = "a ponte ja tem clientes demais"
                    continue

                if not text.startswith("#"):
                    continue
                try:
                    head, millis, line = text[1:].split("\t", 2)
                    seq = int(head)
                    ts = (clock_offset + int(millis) / 1000.0
                          if clock_offset is not None else time.time())
                except ValueError:
                    LIVE.bad_lines += 1
                    continue
                if echo:
                    print(line)
                ingest(ts, line, src_key="%s:%d" % (boot, seq), live=live)
                if seq % 20 == 0 or live:
                    DB.set_state("seq:" + boot, seq)
                if not live and LIVE.backlog > 0:
                    LIVE.backlog -= 1
        except (OSError, ValueError) as exc:
            LIVE.status = "erro"
            LIVE.detail = str(exc)
            print("[rede] %s" % exc)
        finally:
            if sock:
                try:
                    sock.close()
                except OSError:
                    pass
        LIVE.status = "reconectando"
        time.sleep(4)


# ---------------------------------------------------------------------------
# Fonte 3: MQTT (a ponte publica num broker e nos assinamos)
# ---------------------------------------------------------------------------
# Diferente do TCP, aqui nao somos nos que alcancamos a ponte: ela e o servidor
# fazem conexao de saida para o broker. E o que permite o servidor ficar fora
# da rede da escola sem ninguem abrir porta em roteador.
#
# A ponte publica JSON em <base>/raw com QoS 1:
#   {"boot":"a1b2c3d4","seq":1234,"age":2500,"line":"2026-...,,,YPODV6,..."}
#
# "age" e ha quantos ms a linha foi lida, nao um horario: os dois relogios nao
# precisam estar sincronizados (a ponte nem tem relogio). QoS 1 entrega "pelo
# menos uma vez", entao repetido acontece de proposito -- e o indice unico em
# (boot, seq) descarta.
def mqtt_loop(broker, topic_base, user=None, password=None, echo=False):
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        LIVE.status = "erro"
        LIVE.detail = "falta o paho-mqtt (pip install paho-mqtt)"
        print("[mqtt] falta o paho-mqtt: pip install paho-mqtt")
        return

    host, port, tls = parse_broker(broker)
    topic_raw = topic_base.rstrip("/") + "/raw"
    topic_status = topic_base.rstrip("/") + "/status"
    LIVE.source = "MQTT %s:%d %s" % (host, port, topic_raw)

    def on_connect(client, userdata, flags, reason_code, properties=None):
        # no paho 2.x isto e um objeto ReasonCode, nao um int
        falhou = getattr(reason_code, "is_failure", None)
        if falhou is None:
            falhou = reason_code != 0
        if falhou:
            LIVE.status = "erro"
            LIVE.detail = "broker recusou: %s" % reason_code
            print("[mqtt] conexao recusada: %s" % reason_code)
            return
        LIVE.status = "conectado"
        LIVE.detail = ""
        client.subscribe([(topic_raw, 1), (topic_status, 1)])
        print("[mqtt] conectado, assinando %s" % topic_raw)

    def on_disconnect(client, userdata, flags, reason_code, properties=None):
        LIVE.status = "reconectando"
        print("[mqtt] desconectado (%s), tentando de novo" % reason_code)

    def on_message(client, userdata, msg):
        if msg.topic == topic_status:
            estado = msg.payload.decode("utf-8", "replace")
            LIVE.detail = "" if estado == "online" else "a ponte reportou: %s" % estado
            return
        try:
            d = json.loads(msg.payload.decode("utf-8", "replace"))
            line = d["line"]
            boot = d.get("boot", "?")
            seq = int(d.get("seq", 0))
            ts = time.time() - float(d.get("age", 0)) / 1000.0
        except (ValueError, KeyError, TypeError) as exc:
            LIVE.bad_lines += 1
            print("[mqtt] payload estranho (%s): %.80s" % (exc, msg.payload))
            return
        if echo:
            print(line)
        ingest(ts, line, src_key="%s:%d" % (boot, seq))

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                         client_id="ypod-monitor-%d" % os.getpid())
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    if user:
        client.username_pw_set(user, password or "")
    if tls:
        client.tls_set()
    client.reconnect_delay_set(min_delay=1, max_delay=30)

    while True:
        try:
            LIVE.status = "conectando"
            client.connect(host, port, keepalive=45)
            client.loop_forever(retry_first_connection=False)
        except Exception as exc:                      # noqa: BLE001
            LIVE.status = "erro"
            LIVE.detail = str(exc)
            print("[mqtt] %s" % exc)
            time.sleep(5)


def parse_broker(uri):
    """mqtt://host:1883, mqtts://host:8883, host:1883 ou so host."""
    tls = uri.startswith("mqtts://")
    for prefixo in ("mqtt://", "mqtts://", "tcp://", "ssl://"):
        if uri.startswith(prefixo):
            uri = uri[len(prefixo):]
            break
    host, _, porta = uri.partition(":")
    try:
        porta = int(porta)
    except ValueError:
        porta = 8883 if tls else 1883
    return host, porta, tls


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def card_meta():
    return [{"key": k, "label": lb, "unit": u, "group": g, "slot": s}
            for _, k, lb, u, g, s in FIELDS]


def live_state():
    raw, latest, ts, meta = LIVE.snapshot()
    age = (time.time() - ts) if ts else None
    cutoff = time.time() - 300
    prev = {}
    for t, v in raw:
        if t <= cutoff:
            for k, val in v.items():
                if val is not None:
                    prev[k] = val
    cards = []
    for info in card_meta():
        k = info["key"]
        cur = latest.get(k)
        before = prev.get(k)
        spark = [v.get(k) for _, v in raw[-40:] if v.get(k) is not None]
        cards.append(dict(info, value=cur, spark=spark,
                          delta=(round(cur - before, 2)
                                 if cur is not None and before is not None else None)))
    try:
        indice = DB.iqar_agora()
    except Exception:                                 # noqa: BLE001
        indice = None            # o indice nunca derruba a tela ao vivo
    return {
        "iqar": indice,
        "status": LIVE.status, "detail": LIVE.detail, "source": LIVE.source,
        "stale": age is None or age > STALE_AFTER,
        "age": None if age is None else round(age, 1),
        "backlog": LIVE.backlog, "meta": meta, "count": LIVE.count,
        "bad_lines": LIVE.bad_lines, "gravadas": LIVE.gravadas,
        "uptime": round(time.time() - LIVE.started),
        "influx": (None if INFLUX is None else
                   {"status": INFLUX.status, "enviadas": INFLUX.enviadas,
                    "erro": INFLUX.erro}),
        "nuvem": (None if NUVEM is None else
                  {"status": NUVEM.status, "enviadas": NUVEM.enviadas,
                   "erro": NUVEM.erro}),
        "server_time": time.time() * 1000, "cards": cards,
    }


def window_state(t0, t1):
    series, bucket = DB.series(t0, t1)
    return {"from": t0 * 1000, "to": t1 * 1000, "bucket": bucket,
            "stats": DB.stats(t0, t1), "series": series,
            "groups": [{"key": g, "title": t, "unit": u} for g, t, u in GROUPS],
            "fields": card_meta(), "range": DB.range()}


def parse_window(query):
    """?from=&to= em ms (ou ISO), senao ?window= em segundos ate agora."""
    def num(name):
        v = query.get(name, [None])[0]
        if not v:
            return None
        try:
            return float(v) / 1000.0
        except ValueError:
            try:
                return datetime.fromisoformat(v).timestamp()
            except ValueError:
                return None

    t0, t1 = num("from"), num("to")
    if t0 is not None and t1 is not None and t1 > t0:
        return t0, t1
    try:
        win = int(query.get("window", ["3600"])[0])
    except ValueError:
        win = 3600
    now = time.time()
    return now - max(60, win), now + 1


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):      # silencia o log de acesso
        pass

    def _send(self, body, ctype="application/json; charset=utf-8", code=200,
              download=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if download:
            self.send_header("Content-Disposition",
                             'attachment; filename="%s"' % download)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):                        # noqa: N802
        url = urlparse(self.path)
        path, query = url.path, parse_qs(url.query)
        try:
            if path in ("/", "/index.html"):
                return self._file(os.path.join(STATIC_DIR, "dashboard.html"),
                                  "text/html; charset=utf-8")
            if path == "/api/live":
                return self._send(json.dumps(live_state()))
            if path == "/api/window":
                t0, t1 = parse_window(query)
                return self._send(json.dumps(window_state(t0, t1)))
            if path == "/api/range":
                return self._send(json.dumps(DB.range()))
            if path == "/api/table":
                t0, t1 = parse_window(query)
                try:
                    limit = min(int(query.get("limit", ["100"])[0]), 2000)
                except ValueError:
                    limit = 100
                rows = [{"t": ts * 1000, "v": v} for ts, v in DB.rows(t0, t1, limit)]
                return self._send(json.dumps({"rows": rows, "keys": card_meta()}))
            if path == "/api/export":
                t0, t1 = parse_window(query)
                name = "ypod_%s_a_%s.csv" % (
                    datetime.fromtimestamp(t0).strftime("%Y-%m-%d_%H%M"),
                    datetime.fromtimestamp(min(t1, time.time())).strftime("%Y-%m-%d_%H%M"))
                return self._send(DB.export_csv(t0, t1), "text/csv; charset=utf-8",
                                  download=name)
            if path.startswith("/static/"):
                name = os.path.basename(path[len("/static/"):])
                ctype = "text/javascript" if name.endswith(".js") else "text/plain"
                return self._file(os.path.join(STATIC_DIR, name), ctype)
        except Exception as exc:                      # noqa: BLE001
            return self._send(json.dumps({"error": repr(exc)}), code=500)
        self._send("not found", "text/plain", 404)

    def _file(self, path, ctype):
        if not os.path.isfile(path):
            return self._send("not found: %s" % os.path.basename(path),
                              "text/plain", 404)
        with open(path, "rb") as fh:
            body = fh.read()
        self._send(body, ctype)


# ---------------------------------------------------------------------------
# Saida InfluxDB (para o Grafana da escola)
# ---------------------------------------------------------------------------
# O banco local e a fila: este escritor le as linhas em ordem de insercao
# (coluna id, nao ts) e marca ate onde ja mandou. Com isso o Influx pode ficar
# dias fora do ar que, quando voltar, recebe tudo o que perdeu -- e o backlog da
# ponte, que chega com hora antiga, tambem vai junto, porque o id avanca mesmo
# quando o ts e do passado.
#
# Influx aceita ponto fora de ordem sem reclamar, entao nao precisamos ordenar
# por tempo; e reescrever o mesmo ponto (mesma serie + mesmo timestamp) apenas
# sobrescreve, o que torna o reenvio seguro.
INFLUX_BATCH = 5000


def escapar_tag(v):
    return (str(v).replace("\\", "\\\\").replace(" ", "\\ ")
            .replace(",", "\\,").replace("=", "\\="))


def linha_influx(measurement, tags, campos, ts_s):
    partes = [escapar_tag(measurement)]
    for k, v in tags.items():
        if v:
            partes.append("%s=%s" % (escapar_tag(k), escapar_tag(v)))
    vals = ",".join("%s=%s" % (k, v) for k, v in campos.items())
    return "%s %s %d" % (",".join(partes), vals, int(ts_s * 1000))


class InfluxWriter(threading.Thread):
    daemon = True

    def __init__(self, url, token="", org="", bucket="", db="",
                 user="", password="", measurement="ypod", intervalo=10):
        super().__init__(name="influx")
        self.url = url.rstrip("/")
        self.token, self.org, self.bucket = token, org, bucket
        self.db, self.user, self.password = db, user, password
        self.measurement = measurement
        self.intervalo = intervalo
        self.v2 = bool(bucket)
        self.enviadas = 0
        self.status = "iniciando"
        self.erro = ""

    def _endpoint(self):
        if self.v2:
            return ("%s/api/v2/write?org=%s&bucket=%s&precision=ms"
                    % (self.url, quote(self.org), quote(self.bucket)))
        return "%s/write?db=%s&precision=ms" % (self.url, quote(self.db))

    def _headers(self):
        h = {"Content-Type": "text/plain; charset=utf-8"}
        if self.v2 and self.token:
            h["Authorization"] = "Token %s" % self.token
        elif self.user:
            import base64
            cred = base64.b64encode(
                ("%s:%s" % (self.user, self.password)).encode()).decode()
            h["Authorization"] = "Basic %s" % cred
        return h

    def _enviar(self, corpo):
        import urllib.request
        req = urllib.request.Request(self._endpoint(),
                                     data=corpo.encode("utf-8"),
                                     headers=self._headers(), method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status

    def _pendentes(self, desde_id):
        cols = ["id", "ts", "pod_id", "firmware"] + KEYS
        cur = DB.conn().execute(
            "SELECT %s FROM readings WHERE id > ? ORDER BY id LIMIT %d"
            % (",".join(cols), INFLUX_BATCH), (desde_id,))
        return cur.fetchall()

    def run(self):
        import urllib.error
        while True:
            try:
                ultimo = int(DB.get_state("influx_id") or 0)
                linhas = self._pendentes(ultimo)
                if not linhas:
                    self.status = "em dia"
                    time.sleep(self.intervalo)
                    continue
                corpo = []
                for r in linhas:
                    campos = {}
                    for i, k in enumerate(KEYS, start=4):
                        if r[i] is not None:
                            campos[k] = r[i]
                    if not campos:
                        continue
                    corpo.append(linha_influx(
                        self.measurement,
                        {"pod_id": r[2] or "ypod", "firmware": r[3] or ""},
                        campos, r[1]))
                if corpo:
                    self._enviar("\n".join(corpo))
                    self.enviadas += len(corpo)
                DB.set_state("influx_id", linhas[-1][0])
                atraso = len(linhas) == INFLUX_BATCH
                self.status = "enviando atraso" if atraso else "em dia"
                self.erro = ""
                if not atraso:
                    time.sleep(self.intervalo)
            except urllib.error.HTTPError as exc:
                self.status = "erro"
                corpo_erro = ""
                try:
                    corpo_erro = exc.read().decode("utf-8", "replace")[:200]
                except Exception:                     # noqa: BLE001
                    pass
                self.erro = "HTTP %s %s" % (exc.code, corpo_erro)
                print("[influx] %s" % self.erro)
                time.sleep(15)
            except Exception as exc:                  # noqa: BLE001
                self.status = "erro"
                self.erro = str(exc)
                print("[influx] %s" % exc)
                time.sleep(15)


INFLUX = None


# ---------------------------------------------------------------------------
# Saida para a nuvem (Cloudflare Worker + D1 -> painel no GitHub Pages)
# ---------------------------------------------------------------------------
# Mesma ideia do InfluxWriter: o banco local e a fila, um ponteiro no banco
# (state "nuvem_id") marca ate onde ja foi aceito. PC sem internet por dias
# nao perde nada -- quando volta, manda o atraso em lotes.
#
# Cada POST leva tambem um retrato do /api/live (cards, sparklines, status),
# para o site publico mostrar o "agora" com ~15 s de atraso. O Worker guarda
# so o ultimo retrato, e as medias por minuto vao para o D1.
#
# O Worker deduplica pelo src_key ("min:<epoch>"), entao reenviar e seguro.
NUVEM_LOTE = 500


class NuvemWriter(threading.Thread):
    daemon = True

    def __init__(self, url, token, intervalo=15):
        super().__init__(name="nuvem")
        self.endpoint = url.rstrip("/") + "/api/push"
        self.token = token
        self.intervalo = intervalo
        self.enviadas = 0
        self.status = "iniciando"
        self.erro = ""

    def _pendentes(self, desde_id):
        cols = ["id", "ts", "src_key", "pod_id", "firmware"] + KEYS
        cur = DB.conn().execute(
            "SELECT %s FROM readings WHERE id > ? ORDER BY id LIMIT %d"
            % (",".join(cols), NUVEM_LOTE), (desde_id,))
        return cur.fetchall()

    @staticmethod
    def _linha(r):
        d = {"k": r[2] or "ts:%.3f" % r[1], "ts": r[1],
             "pod_id": r[3], "firmware": r[4]}
        for i, k in enumerate(KEYS, start=5):
            if r[i] is not None:
                d[k] = r[i]
        return d

    def _enviar(self, corpo):
        import urllib.request
        req = urllib.request.Request(
            self.endpoint,
            data=json.dumps(corpo, separators=(",", ":")).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer %s" % self.token,
                     # a Cloudflare costuma barrar o User-Agent padrao do urllib
                     "User-Agent": "ypod-monitor/1.0"},
            method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read() or b"{}")

    def run(self):
        import urllib.error
        while True:
            try:
                ultimo = int(DB.get_state("nuvem_id") or 0)
                linhas = self._pendentes(ultimo)
                retrato = live_state()
                retrato["fields"] = card_meta()
                retrato["groups"] = [{"key": g, "title": t, "unit": u}
                                     for g, t, u in GROUPS]
                self._enviar({"rows": [self._linha(r) for r in linhas],
                              "live": retrato})
                if linhas:
                    DB.set_state("nuvem_id", linhas[-1][0])
                    self.enviadas += len(linhas)
                atraso = len(linhas) == NUVEM_LOTE
                self.status = "enviando atraso" if atraso else "em dia"
                self.erro = ""
                if not atraso:
                    time.sleep(self.intervalo)
            except urllib.error.HTTPError as exc:
                self.status = "erro"
                corpo_erro = ""
                try:
                    corpo_erro = exc.read().decode("utf-8", "replace")[:200]
                except Exception:                     # noqa: BLE001
                    pass
                self.erro = "HTTP %s %s" % (exc.code, corpo_erro)
                print("[nuvem] %s" % self.erro)
                time.sleep(30 if exc.code == 401 else 15)
            except Exception as exc:                  # noqa: BLE001
                self.status = "erro"
                self.erro = str(exc)
                print("[nuvem] %s" % exc)
                time.sleep(15)


NUVEM = None


def fetch_chartjs():
    """Baixa o Chart.js uma vez para static/, para o painel funcionar offline."""
    dest = os.path.join(STATIC_DIR, "chart.umd.min.js")
    if os.path.exists(dest) and os.path.getsize(dest) > 10000:
        return True
    import urllib.request
    url = "https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = resp.read()
        if len(data) < 10000:
            return False
        os.makedirs(STATIC_DIR, exist_ok=True)
        with open(dest, "wb") as fh:
            fh.write(data)
        print("[setup] Chart.js salvo em static/chart.umd.min.js")
        return True
    except Exception as exc:                          # noqa: BLE001
        print("[setup] nao baixei o Chart.js (%s) - o painel tenta o CDN" % exc)
        return False


def main():
    global DB
    ap = argparse.ArgumentParser(description="Mini servidor do YPOD")
    ap.add_argument("--source", choices=("auto", "net", "serial", "mqtt"),
                    default="auto",
                    help="auto: usa o broker se --broker for dado, senao procura"
                         " a ponte na rede, senao cai para o USB")
    ap.add_argument("--broker", default=os.environ.get("YPOD_BROKER", ""),
                    help="broker MQTT, ex: mqtt://192.168.0.10:1883"
                         " (ou a variavel de ambiente YPOD_BROKER)")
    ap.add_argument("--topic", default=os.environ.get("YPOD_TOPIC", "ypod/pod1"),
                    help="topico base, o mesmo MQTT_TOPICO_BASE da ponte")
    ap.add_argument("--mqtt-user", default=os.environ.get("YPOD_MQTT_USER", ""))
    ap.add_argument("--mqtt-pass", default=os.environ.get("YPOD_MQTT_PASS", ""))
    ap.add_argument("--host", default="auto",
                    help="IP/nome da ponte ESP32 (auto = descobre sozinho)")
    ap.add_argument("--bridge-port", type=int, default=BRIDGE_TCP_PORT)
    ap.add_argument("--port", default="COM5", help="porta serial, no modo USB")
    ap.add_argument("--baud", type=int, default=9600)
    ap.add_argument("--http-port", type=int, default=8000)
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--gravacao", type=int, default=60, metavar="SEG",
                    help="intervalo de gravacao no banco, em segundos (padrao 60)."
                         " A tela continua em tempo real. Use 0 para gravar"
                         " cada leitura, sem media.")
    ap.add_argument("--influx-url", default=os.environ.get("YPOD_INFLUX_URL", ""),
                    help="ex: http://servidor:8086 (vazio = nao envia)")
    ap.add_argument("--influx-token", default=os.environ.get("YPOD_INFLUX_TOKEN", ""),
                    help="token do InfluxDB 2.x")
    ap.add_argument("--influx-org", default=os.environ.get("YPOD_INFLUX_ORG", ""))
    ap.add_argument("--influx-bucket", default=os.environ.get("YPOD_INFLUX_BUCKET", ""),
                    help="bucket (InfluxDB 2.x)")
    ap.add_argument("--influx-db", default=os.environ.get("YPOD_INFLUX_DB", ""),
                    help="database (InfluxDB 1.x), no lugar de --influx-bucket")
    ap.add_argument("--influx-user", default=os.environ.get("YPOD_INFLUX_USER", ""))
    ap.add_argument("--influx-pass", default=os.environ.get("YPOD_INFLUX_PASS", ""))
    ap.add_argument("--influx-measurement", default="ypod")
    ap.add_argument("--nuvem-url", default=os.environ.get("YPOD_NUVEM_URL", ""),
                    help="Worker da Cloudflare, ex: https://ypod-api.conta.workers.dev"
                         " (vazio = nao envia)")
    ap.add_argument("--nuvem-token", default=os.environ.get("YPOD_NUVEM_TOKEN", ""),
                    help="o INGEST_TOKEN do Worker (melhor pela variavel"
                         " de ambiente YPOD_NUVEM_TOKEN)")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--echo", action="store_true", help="imprime as linhas cruas")
    ap.add_argument("--list", action="store_true", help="lista as portas seriais")
    args = ap.parse_args()

    if args.list:
        if list_ports is None:
            print("pyserial nao instalado")
            return
        for p in list_ports.comports():
            print("%-8s %s" % (p.device, p.description))
        return

    fetch_chartjs()
    DB = Db(args.db)
    DB.import_csv_files()
    r = DB.range()
    if r["rows"]:
        print("[db] %s leituras de %s ate %s" % (
            format(r["rows"], ","),
            datetime.fromtimestamp(r["first"]).strftime("%d/%m %H:%M"),
            datetime.fromtimestamp(r["last"]).strftime("%d/%m %H:%M")))

    global ACUM
    if args.gravacao > 0:
        ACUM = AcumuladorPorMinuto(args.gravacao)
        print("[db] gravando a media a cada %ds (a tela segue em tempo real)"
              % args.gravacao)
    else:
        print("[db] gravando cada leitura, sem media")

    global INFLUX
    if args.influx_url:
        if not (args.influx_bucket or args.influx_db):
            print("com --influx-url, informe --influx-bucket (2.x) ou --influx-db (1.x)")
            return
        INFLUX = InfluxWriter(args.influx_url, args.influx_token, args.influx_org,
                              args.influx_bucket, args.influx_db, args.influx_user,
                              args.influx_pass, args.influx_measurement)
        INFLUX.start()
        pend = DB.conn().execute(
            "SELECT COUNT(*) FROM readings WHERE id > ?",
            (int(DB.get_state("influx_id") or 0),)).fetchone()[0]
        print("[influx] %s (%s) - %s leituras pendentes"
              % (args.influx_url,
                 "bucket " + args.influx_bucket if args.influx_bucket
                 else "db " + args.influx_db, format(pend, ",")))

    global NUVEM
    if args.nuvem_url:
        if not args.nuvem_token:
            print("com --nuvem-url, informe --nuvem-token (ou YPOD_NUVEM_TOKEN)")
            return
        if args.gravacao == 0:
            # uma linha por segundo estoura a cota gratis do D1 (100 mil/dia)
            print("[nuvem] AVISO: --gravacao 0 manda ~86 mil linhas/dia para o D1;"
                  " o plano gratis aceita 100 mil escritas/dia")
        NUVEM = NuvemWriter(args.nuvem_url, args.nuvem_token)
        NUVEM.start()
        pend = DB.conn().execute(
            "SELECT COUNT(*) FROM readings WHERE id > ?",
            (int(DB.get_state("nuvem_id") or 0),)).fetchone()[0]
        print("[nuvem] %s - %s leituras pendentes" % (args.nuvem_url, format(pend, ",")))

    source = args.source
    if source == "auto":
        if args.broker:
            source = "mqtt"
        else:
            found = discover_bridge(timeout=4.0)
            source = "net" if found else "serial"
            if found:
                args.host, args.bridge_port = found
            else:
                print("[setup] nenhuma ponte na rede, usando o USB (%s)" % args.port)
    if source == "mqtt" and not args.broker:
        print("faltou --broker (ex: --broker mqtt://192.168.0.10:1883)")
        return

    if source == "mqtt":
        worker = threading.Thread(
            target=mqtt_loop,
            args=(args.broker, args.topic, args.mqtt_user, args.mqtt_pass,
                  args.echo),
            daemon=True)
    elif source == "net":
        worker = threading.Thread(target=tcp_loop,
                                  args=(args.host, args.bridge_port, args.echo),
                                  daemon=True)
    else:
        worker = threading.Thread(target=serial_loop,
                                  args=(args.port, args.baud, args.echo),
                                  daemon=True)
    worker.start()

    server = ThreadingHTTPServer(("0.0.0.0", args.http_port), Handler)
    url = "http://127.0.0.1:%d/" % args.http_port
    print("\n  YPOD  ->  %s   (Ctrl+C para parar)\n" % url)
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nencerrando.")


if __name__ == "__main__":
    main()
