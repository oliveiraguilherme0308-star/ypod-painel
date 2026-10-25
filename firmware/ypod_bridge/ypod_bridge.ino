/*
 * YPOD Bridge - ESP32-S3
 * ----------------------
 * Pega as linhas que o YPOD cospe na serial e entrega pela rede Wi-Fi para o
 * mini servidor rodando no PC (ypod_monitor.py).
 *
 *   YPOD --(USB)--> ESP32-S3 --(Wi-Fi)--> PC (grava e mostra)
 *
 * A ponte nao interpreta nada: repassa a linha crua, numerada. Quem entende de
 * sensor e o PC. Assim mudar o firmware do pod nao exige reflash daqui.
 *
 * Como o PC acha a ponte, sem voce descobrir IP:
 *   - beacon UDP broadcast na porta 3334 (funciona mesmo sem Bonjour)
 *   - mDNS: ypod-bridge.local
 *
 * Se o PC ficar fora do ar, as linhas esperam num buffer circular aqui e sao
 * reenviadas quando ele volta: cada linha tem um numero e o PC diz de qual
 * continuar, entao nao se perde nem se repete.
 *
 * ---------------------------------------------------------------------------
 * ARQUIVOS DESTE SKETCH
 *   ponte.h        configuracao (rede, pinos, de onde vem o dado)
 *   fonte_usb.ino  le o pod pela porta USB do ESP32  <- padrao
 *   fonte_uart.ino le o pod por um fio no pino TX    <- alternativa
 *   ypod_bridge.ino (este) rede, buffer e pagina de status
 *
 * A ligacao esta comentada no topo do arquivo da fonte que voce escolher, em
 * ponte.h. Leia antes de montar.
 *
 * ---------------------------------------------------------------------------
 * COMO GRAVAR
 * Arduino IDE -> Gerenciador de placas -> "esp32" (Espressif).
 * Preencha WIFI_SSID / WIFI_PASS logo abaixo.
 *
 * Ferramentas, para o modulo ESP32-S3-N16R8 (16 MB flash, 8 MB PSRAM OPI):
 *     Placa .............. ESP32S3 Dev Module
 *     USB CDC On Boot .... Disabled
 *     USB Mode ........... Hardware CDC and JTAG
 *     Flash Size ......... 16MB (128Mb)
 *     PSRAM .............. OPI PSRAM
 *     Partition Scheme ... 16M Flash (3MB APP/9.9MB FATFS)
 * Modulo diferente (N8, N8R2...)? Ajuste Flash Size e PSRAM conforme o que
 * estiver escrito no blindado - marcar OPI PSRAM onde nao tem impede o boot.
 *
 * Grave pelo conector "COM" e deixe o "USB" livre para o pod.
 * Depois abra http://ypod-bridge.local para ver o status.
 */

#include "ponte.h"
#include <WiFi.h>
#include <WiFiUdp.h>
#include <ESPmDNS.h>
#include <WebServer.h>
#include <esp_random.h>
#include <esp_task_wdt.h>
#if OTA_LIGADO
#include <ArduinoOTA.h>
#endif

// --------------------------------------------------------------------------
// REDE - padrao de fabrica, opcional
// --------------------------------------------------------------------------
// Deixe vazio. O que vale e o que esta gravado na NVS, escolhido no portal de
// configuracao -- e com estes campos vazios a ponte vai direto para o portal na
// primeira vez, em vez de perder 30 s tentando uma rede que nao existe mais.
//
// Nao ponha a senha de uma rede real aqui: este arquivo esta num repositorio
// publico.
const char *WIFI_SSID = "";
const char *WIFI_PASS = "";
const char *HOSTNAME  = "ypod-bridge";   // vira ypod-bridge.local

const uint32_t BEACON_EVERY_MS = 3000;
const uint32_t PING_EVERY_MS   = 15000;

// --------------------------------------------------------------------------
// Buffer circular de linhas numeradas
// --------------------------------------------------------------------------
// Guarda, junto com a linha, o millis de QUANDO ELA CHEGOU. E isso que permite
// ao PC remontar o horario certo do que ficou represado: sem esse carimbo, uma
// hora de backlog chegaria toda com o horario do reenvio.
struct Ring {
  char     *buf = nullptr;
  uint32_t *when = nullptr;
  size_t    cap = 0;        // quantas linhas cabem
  size_t    len = 0;        // quantas estao guardadas
  size_t    head = 0;       // posicao da proxima escrita
  uint32_t  firstSeq = 1;   // numero da linha mais antiga guardada

  bool begin(size_t capacity, bool psram) {
    buf  = (char *)(psram ? ps_malloc(capacity * LINHA_MAX) : malloc(capacity * LINHA_MAX));
    when = (uint32_t *)(psram ? ps_malloc(capacity * sizeof(uint32_t))
                              : malloc(capacity * sizeof(uint32_t)));
    if (!buf || !when) {
      free(buf); free(when);
      buf = nullptr; when = nullptr;
      return false;
    }
    cap = capacity;
    return true;
  }

  void push(const char *line, uint32_t seq, uint32_t ms) {
    char *slot = buf + head * LINHA_MAX;
    strncpy(slot, line, LINHA_MAX - 1);
    slot[LINHA_MAX - 1] = '\0';
    when[head] = ms;
    head = (head + 1) % cap;
    if (len < cap) {
      len++;
      if (len == 1) firstSeq = seq;
    } else {
      firstSeq++;   // sobrescreveu a mais antiga
    }
  }

  // Linha de numero seq, ou nullptr se ja saiu do buffer.
  const char *at(uint32_t seq, uint32_t *ms) const {
    if (len == 0 || seq < firstSeq || seq >= firstSeq + len) return nullptr;
    size_t oldest = (head + cap - len) % cap;
    size_t idx = (oldest + (seq - firstSeq)) % cap;
    if (ms) *ms = when[idx];
    return buf + idx * LINHA_MAX;
  }
};

#if COMPACTAR_BUFFER
// --------------------------------------------------------------------------
// Anel das medias de minuto
// --------------------------------------------------------------------------
// Diferente do anel de cima: ali os numeros sao consecutivos e o indice sai de
// uma subtracao. Aqui cada entrada ocupa o numero da ULTIMA linha do minuto
// que ela resume, entao os numeros sao crescentes mas esparsos -- entre uma
// entrada e a seguinte ha um vao de ~27. Por isso a busca e binaria.
struct AnelMinuto {
  char     *buf = nullptr;
  uint32_t *when = nullptr;
  uint32_t *seqs = nullptr;
  size_t    cap = 0, len = 0, head = 0;

  bool begin(size_t capacity, bool psram) {
    buf  = (char *)(psram ? ps_malloc(capacity * LINHA_MAX) : malloc(capacity * LINHA_MAX));
    when = (uint32_t *)(psram ? ps_malloc(capacity * 4) : malloc(capacity * 4));
    seqs = (uint32_t *)(psram ? ps_malloc(capacity * 4) : malloc(capacity * 4));
    if (!buf || !when || !seqs) {
      free(buf); free(when); free(seqs);
      buf = nullptr; when = nullptr; seqs = nullptr;
      return false;
    }
    cap = capacity;
    return true;
  }

  void push(const char *line, uint32_t seq, uint32_t ms) {
    if (!cap) return;
    char *slot = buf + head * LINHA_MAX;
    strncpy(slot, line, LINHA_MAX - 1);
    slot[LINHA_MAX - 1] = '\0';
    when[head] = ms;
    seqs[head] = seq;
    head = (head + 1) % cap;
    if (len < cap) len++;
  }

  size_t posicao(size_t i) const { return (head + cap - len + i) % cap; }

  uint32_t primeiraSeq() const { return len ? seqs[posicao(0)] : 0; }

  const char *at(uint32_t seq, uint32_t *ms) const {
    if (!len) return nullptr;
    size_t lo = 0, hi = len;            // procura o i com seqs[i] == seq
    while (lo < hi) {
      size_t mid = (lo + hi) / 2;
      uint32_t s = seqs[posicao(mid)];
      if (s == seq) {
        size_t idx = posicao(mid);
        if (ms) *ms = when[idx];
        return buf + idx * LINHA_MAX;
      }
      if (s < seq) lo = mid + 1; else hi = mid;
    }
    return nullptr;
  }
};
#endif

// --------------------------------------------------------------------------
// Estado
// --------------------------------------------------------------------------
Ring       ring;
#if COMPACTAR_BUFFER
AnelMinuto anelMin;
#endif
WiFiServer tcpServer(TCP_PORT);
WiFiUDP    udp;
WebServer  http(HTTP_PORT);

struct Peer {
  WiFiClient cli;
  uint32_t   nextSeq = 0;   // proxima linha a enviar (0 = ainda nao sincronizou)
  bool       live = false;
  uint32_t   lastSent = 0;
};
Peer peers[MAX_CLIENTS];

uint32_t bootId   = 0;
uint32_t wifiCaiuEm = 0;      // desde quando estamos fora da rede (0 = estamos nela)
const char *motivoReinicio = "";
uint32_t seqCount = 0;        // linhas recebidas do pod desde o boot
uint32_t lastLineMs = 0;
uint32_t lastBeaconMs = 0;
char     lastLine[LINHA_MAX] = "";

// --------------------------------------------------------------------------
// Acesso ao buffer para quem publica (TCP aqui dentro, MQTT em saida_mqtt.cpp)
// --------------------------------------------------------------------------
uint32_t ring_ultima_seq() { return seqCount; }

// Procura primeiro no detalhe recente; se ja saiu dali, tenta a media do
// minuto. Quem consome (TCP e MQTT) ja trata "nao existe mais" pulando o
// numero, entao os numeros intermediarios de um minuto compactado somem sem
// precisar de nenhuma mudanca no protocolo.
const char *ring_linha(uint32_t seq, uint32_t *ms) {
  const char *l = ring.at(seq, ms);
#if COMPACTAR_BUFFER
  if (!l) l = anelMin.at(seq, ms);
#endif
  return l;
}

// Numero da linha mais antiga que ainda da para entregar.
uint32_t ring_primeira_seq() {
#if COMPACTAR_BUFFER
  uint32_t m = anelMin.primeiraSeq();
  if (m && (ring.len == 0 || m < ring.firstSeq)) return m;
#endif
  return ring.firstSeq;
}

// --------------------------------------------------------------------------
// A fonte (USB ou UART) chama isto a cada linha inteira do pod
// --------------------------------------------------------------------------
void nova_linha(const char *linha_pod) {
  // Os dados de vento vao no fim da linha do pod. O servidor le os campos por
  // posicao, entao campo novo no final nao atrapalha quem ja existia -- e a
  // linha continua sendo uma so, com a mesma hora para todos os sensores.
  char linha[LINHA_MAX];
#if VENTO_LIGADO
  if (vento_valido()) {
    snprintf(linha, sizeof(linha), "%s%.1f,%.1f", linha_pod,
             vento_velocidade(), vento_direcao());
  } else {
    // sem ima ou sem I2C: deixa os campos vazios em vez de inventar zero
    snprintf(linha, sizeof(linha), "%s,", linha_pod);
  }
#else
  snprintf(linha, sizeof(linha), "%s", linha_pod);
#endif

  uint32_t agora = millis();
  seqCount++;
  ring.push(linha, seqCount, agora);

#if COMPACTAR_BUFFER
  // O minuto corrente fechou? Entao a media dele vira uma entrada no anel de
  // minutos, ocupando o numero da ultima linha que entrou naquele balde.
  // Fecha ANTES de somar a linha nova, que ja pertence ao minuto seguinte.
  if (compactar_fechou(agora)) {
    uint32_t seq_m = compactar_seq();
    uint32_t ms_m  = compactar_ms_meio();
    char media[LINHA_MAX];
    if (compactar_linha(media, sizeof(media)) > 0) {
      anelMin.push(media, seq_m, ms_m);
    }
  }
  compactar_somar(linha, agora, seqCount);
#endif

  strncpy(lastLine, linha, LINHA_MAX - 1);
  lastLine[LINHA_MAX - 1] = '\0';
  lastLineMs = agora;
}

// --------------------------------------------------------------------------
// Pagina de status
// --------------------------------------------------------------------------
String humanUptime(uint32_t ms) {
  uint32_t s = ms / 1000;
  char out[32];
  snprintf(out, sizeof(out), "%lud %02lu:%02lu:%02lu",
           (unsigned long)(s / 86400), (unsigned long)((s / 3600) % 24),
           (unsigned long)((s / 60) % 60), (unsigned long)(s % 60));
  return String(out);
}

void handleStatus() {
  uint8_t conn = 0;
  for (auto &p : peers) if (p.cli && p.cli.connected()) conn++;
  uint32_t age = lastLineMs ? (millis() - lastLineMs) / 1000 : 0;
  char fonte[96];
  fonte_status(fonte, sizeof(fonte));

  String h = F("<!doctype html><meta charset=utf-8>"
               "<meta name=viewport content='width=device-width,initial-scale=1'>"
               "<meta http-equiv=refresh content=5>"
               "<title>YPOD Bridge</title><style>"
               "body{font:15px system-ui;background:#1a1a19;color:#fff;margin:0;padding:24px}"
               "h1{font-size:17px;margin:0 0 16px}"
               "table{border-collapse:collapse;font-variant-numeric:tabular-nums}"
               "td{padding:5px 16px 5px 0;border-bottom:1px solid #33332f}"
               "td:first-child{color:#8a8980}"
               "code{background:#232320;padding:2px 6px;border-radius:5px;font-size:13px;"
               "display:inline-block;max-width:92vw;overflow:auto;white-space:nowrap}"
               ".ok{color:#3bbd7e}.bad{color:#e66767}</style><h1>YPOD Bridge</h1><table>");
  h += "<tr><td>status do pod</td><td class='";
  h += (lastLineMs && age < 30) ? "ok'>recebendo" : "bad'>sem dados";
  h += "</td></tr>";
  h += "<tr><td>fonte</td><td>" + String(fonte) + "</td></tr>";
  char vt[96];
  vento_status(vt, sizeof(vt));
  h += "<tr><td>vento</td><td>" + String(vt) + "</td></tr>";
  char mq[96];
  mqtt_status(mq, sizeof(mq));
  h += "<tr><td>MQTT</td><td>" + String(mq) + "</td></tr>";
  h += "<tr><td>ultima linha</td><td>" + (lastLineMs ? "ha " + String(age) + " s" : String("nunca")) + "</td></tr>";
  h += "<tr><td>linhas desde o boot</td><td>" + String(seqCount) + "</td></tr>";
  h += "<tr><td>buffer (detalhe)</td><td>" + String((unsigned)ring.len) + " / "
       + String((unsigned)ring.cap) + " linhas</td></tr>";
#if COMPACTAR_BUFFER
  h += "<tr><td>buffer (minutos)</td><td>" + String((unsigned)anelMin.len) + " / "
       + String((unsigned)anelMin.cap) + " medias · guarda "
       + String((unsigned)(anelMin.cap / 60 / 24)) + " dias</td></tr>";
#endif
  h += "<tr><td>PCs conectados</td><td>" + String(conn) + "</td></tr>";
  h += "<tr><td>IP / porta</td><td>" + WiFi.localIP().toString() + " : " + String(TCP_PORT) + "</td></tr>";
  h += "<tr><td>sinal Wi-Fi</td><td>" + String(WiFi.RSSI()) + " dBm</td></tr>";
  h += "<tr><td>uptime</td><td>" + humanUptime(millis()) + "</td></tr>";
  h += "<tr><td>CPU / radio</td><td>" + String(getCpuFrequencyMhz()) + " MHz · Wi-Fi "
       + String(WIFI_ECONOMIA ? "em economia" : "sempre acordado") + "</td></tr>";
  h += "<tr><td>watchdog</td><td>loop " + String(WDT_LOOP_S) + "s · sem pod "
       + String(WDT_SEM_POD_S) + "s · sem Wi-Fi " + String(WDT_SEM_WIFI_S) + "s</td></tr>";
  h += "<tr><td>RAM livre</td><td>" + String(ESP.getFreeHeap() / 1024) + " KB</td></tr>";
  h += "<tr><td>boot id</td><td>" + String(bootId, HEX) + "</td></tr>";
  h += "</table><p style='color:#8a8980;font-size:13px'>ultima linha recebida:</p><code>";
  h += String(lastLine[0] ? lastLine : "-");
  h += "</code><p style='color:#8a8980;font-size:13px'>Esta pagina e so diagnostico "
       "e nao muda nada. Para definir o norte, calibrar o tacometro, trocar a "
       "senha do portal ou reiniciar: "
       "<a href='/config' style='color:#3bbd7e'>configuracao</a>. "
       "Os graficos ficam no PC, em ypod_monitor.py.</p>";
  http.send(200, "text/html; charset=utf-8", h);
}

void handleJson() {
  uint8_t conn = 0;
  for (auto &p : peers) if (p.cli && p.cli.connected()) conn++;
  char fonte[96], mq[96];
  fonte_status(fonte, sizeof(fonte));
  mqtt_status(mq, sizeof(mq));
  String j = "{\"boot\":\"" + String(bootId, HEX) + "\",\"seq\":" + String(seqCount) +
             ",\"buffered\":" + String((unsigned)ring.len) +
             ",\"clients\":" + String(conn) +
             ",\"rssi\":" + String(WiFi.RSSI()) +
             ",\"uptime_ms\":" + String(millis()) +
             ",\"last_line_age_ms\":" + String(lastLineMs ? millis() - lastLineMs : 0) +
             ",\"source\":\"" + String(fonte) + "\"" +
             ",\"mqtt\":\"" + String(mq) + "\"" +
             ",\"heap\":" + String(ESP.getFreeHeap()) + "}";
  http.send(200, "application/json", j);
}

// --------------------------------------------------------------------------
// Clientes TCP
// --------------------------------------------------------------------------
void greet(Peer &p) {
  char msg[112];
  snprintf(msg, sizeof(msg), "$HELLO boot=%08lx seq=%lu buf=%lu first=%lu now=%lu\n",
           (unsigned long)bootId, (unsigned long)seqCount,
           (unsigned long)ring.len, (unsigned long)ring_primeira_seq(),
           (unsigned long)millis());
  p.cli.print(msg);
}

void acceptClients() {
  WiFiClient incoming = tcpServer.available();
  if (!incoming) return;
  for (auto &p : peers) {
    if (!p.cli || !p.cli.connected()) {
      p.cli = incoming;
      p.cli.setNoDelay(true);
      p.nextSeq = 0;
      p.live = false;
      p.lastSent = millis();
      greet(p);
      Serial.printf("[tcp] cliente %s conectado\n",
                    p.cli.remoteIP().toString().c_str());
      return;
    }
  }
  incoming.println("$BUSY sem vaga");
  incoming.stop();
}

// Comandos do PC:  SINCE <n>  (continuar do n+1)  |  SINCE -1 (tudo que houver)
void readCommands(Peer &p) {
  while (p.cli.available()) {
    String cmd = p.cli.readStringUntil('\n');
    cmd.trim();
    if (cmd.startsWith("SINCE")) {
      long want = cmd.substring(5).toInt();
      uint32_t piso = ring_primeira_seq() ? ring_primeira_seq() : seqCount + 1;
      p.nextSeq = (want < 0) ? piso : max((uint32_t)(want + 1), piso);
      p.live = false;
      Serial.printf("[tcp] sincronizando a partir de %lu\n", (unsigned long)p.nextSeq);
    } else if (cmd == "PING") {
      p.cli.printf("$PONG now=%lu\n", (unsigned long)millis());
    }
  }
}

void pumpClients() {
  uint32_t now = millis();
  for (auto &p : peers) {
    if (!p.cli) continue;
    if (!p.cli.connected()) {
      Serial.println("[tcp] cliente saiu");
      p.cli.stop();
      p.cli = WiFiClient();
      continue;
    }
    readCommands(p);
    if (p.nextSeq == 0) continue;     // ainda nao pediu SINCE

    // manda o atraso (e depois o ao vivo), ate 60 linhas por volta
    int budget = 60;
    while (p.nextSeq <= seqCount && budget-- > 0) {
      uint32_t ms = 0;
      const char *line = ring_linha(p.nextSeq, &ms);
      if (!line) { p.nextSeq++; continue; }   // caiu do buffer, segue em frente
      // o carimbo e o de quando a linha CHEGOU, nao o deste envio
      p.cli.printf("#%lu\t%lu\t%s\n", (unsigned long)p.nextSeq,
                   (unsigned long)ms, line);
      p.nextSeq++;
      p.lastSent = now;
    }
    if (!p.live && p.nextSeq > seqCount) {
      p.cli.printf("$LIVE now=%lu\n", (unsigned long)millis());
      p.live = true;
    }
    if (now - p.lastSent > PING_EVERY_MS) {   // keepalive
      p.cli.printf("$PING now=%lu\n", (unsigned long)millis());
      p.lastSent = now;
    }
  }
}

// --------------------------------------------------------------------------
// Descoberta: beacon UDP broadcast
// --------------------------------------------------------------------------
void sendBeacon() {
  IPAddress bcast = WiFi.localIP();
  bcast[3] = 255;
  char msg[128];
  snprintf(msg, sizeof(msg), "YPOD-BRIDGE host=%s ip=%s port=%u boot=%08lx seq=%lu",
           HOSTNAME, WiFi.localIP().toString().c_str(), TCP_PORT,
           (unsigned long)bootId, (unsigned long)seqCount);
  udp.beginPacket(bcast, BEACON_PORT);
  udp.write((const uint8_t *)msg, strlen(msg));
  udp.endPacket();
}

// --------------------------------------------------------------------------
// Wi-Fi
// --------------------------------------------------------------------------
// A conexao vive em config_rede.cpp: ela usa as credenciais salvas, e abre um
// portal de configuracao se nao conseguir entrar em rede nenhuma.
void connectWifi() {
  rede_conectar();
  if (WiFi.status() == WL_CONNECTED) {
    if (MDNS.begin(HOSTNAME)) MDNS.addService("ypod", "tcp", TCP_PORT);
    udp.begin(BEACON_PORT);
  }
}

#if OTA_LIGADO
// Atualizacao pela rede: na Arduino IDE a placa aparece em Porta > rede.
void ota_setup() {
  ArduinoOTA.setHostname(HOSTNAME);
  if (strlen(OTA_SENHA)) ArduinoOTA.setPassword(OTA_SENHA);
  ArduinoOTA.onStart([]() { Serial.println("\n[ota] recebendo firmware novo"); });
  ArduinoOTA.onEnd([]() { Serial.println("[ota] gravado, reiniciando"); });
  ArduinoOTA.onError([](ota_error_t e) { Serial.printf("[ota] erro %u\n", e); });
  ArduinoOTA.begin();
  Serial.printf("[ota] pronto - IDE > Porta > %s\n", HOSTNAME);
}
#endif

// --------------------------------------------------------------------------
// Watchdog
// --------------------------------------------------------------------------
void watchdog_setup() {
#if WDT_LOOP_S > 0
  // o core ja iniciou o watchdog; aqui so ajustamos o tempo e entramos na lista
  esp_task_wdt_config_t cfg = {
      .timeout_ms = WDT_LOOP_S * 1000,
      .idle_core_mask = 0,
      .trigger_panic = true,
  };
  if (esp_task_wdt_reconfigure(&cfg) != ESP_OK) esp_task_wdt_init(&cfg);
  esp_task_wdt_add(NULL);     // vigia a tarefa do loop()
  Serial.printf("[wdt] loop vigiado, reinicia se travar por %ds\n", WDT_LOOP_S);
#endif
}

// Chamada a cada volta: alimenta o watchdog do chip e checa as condicoes de
// aplicacao. Reiniciar aqui e deliberado -- uma ponte muda nao coleta nada, e
// ninguem esta olhando para perceber.
void watchdog_loop() {
#if WDT_LOOP_S > 0
  esp_task_wdt_reset();
#endif

#if WDT_SEM_POD_S > 0
  // so cobra depois que o pod ja apareceu alguma vez: se ele nunca foi plugado,
  // reiniciar em loop nao ajudaria ninguem
  if (seqCount > 0 && millis() - lastLineMs > (uint32_t)WDT_SEM_POD_S * 1000) {
    motivoReinicio = "sem dados do pod";
  }
#endif

#if WDT_SEM_WIFI_S > 0
  // No portal, estar fora da rede e o estado esperado -- reiniciar ali so
  // fecharia o portal na cara de quem esta configurando.
  if (!rede_em_portal() && wifiCaiuEm &&
      millis() - wifiCaiuEm > (uint32_t)WDT_SEM_WIFI_S * 1000) {
    motivoReinicio = "sem Wi-Fi";
  }
#endif

  if (motivoReinicio[0]) {
    Serial.printf("\n[wdt] reiniciando: %s\n", motivoReinicio);
    Serial.flush();
    delay(100);
    ESP.restart();
  }
}

// --------------------------------------------------------------------------
void setup() {
  Serial.begin(115200);
  delay(300);
  Serial.println("\n=== YPOD Bridge ===");

  bootId = esp_random();

#if CPU_MHZ > 0 && CPU_MHZ < 240
  setCpuFrequencyMhz(CPU_MHZ);
  Serial.printf("[cpu] %d MHz\n", getCpuFrequencyMhz());
#endif

  // Com compactacao o mesmo total de vagas e dividido: um pedaco guarda linha
  // crua (detalhe recente) e o resto guarda uma media por minuto (memoria
  // longa). Sem PSRAM nao ha espaco para dividir -- fica so o detalhe.
#if COMPACTAR_BUFFER
  if (psramFound()) {
    ring.begin(DETALHE_LINHAS, true);
    anelMin.begin(RING_PSRAM - DETALHE_LINHAS, true);
  }
#else
  if (psramFound()) ring.begin(RING_PSRAM, true);
#endif
  if (!ring.buf && !ring.begin(RING_SMALL, false)) {
    Serial.println("[erro] sem memoria para o buffer");
    while (true) delay(1000);
  }
#if COMPACTAR_BUFFER
  if (anelMin.cap) {
    // A 20-30 linhas por minuto (medido), o detalhe cobre entre 50 e 80
    // minutos. Uso 25 como meio da faixa, e imprimo em MINUTOS: a divisao
    // inteira por 60 daria "~0 h" e pareceria defeito.
    Serial.printf("[buf] %u linhas cruas + %u medias de minuto (~%u min de"
                  " detalhe, ~%u dias no total)\n",
                  (unsigned)ring.cap, (unsigned)anelMin.cap,
                  (unsigned)(ring.cap / 25), (unsigned)(anelMin.cap / 60 / 24));
  } else {
    Serial.printf("[buf] %u linhas, sem compactacao (sem PSRAM)\n", (unsigned)ring.cap);
  }
#else
  Serial.printf("[buf] %u linhas (%s)\n", (unsigned)ring.cap,
                psramFound() ? "PSRAM" : "RAM interna");
#endif

  led_setup();
  vento_setup();
  fonte_setup();
  connectWifi();
#if OTA_LIGADO
  if (WiFi.status() == WL_CONNECTED) ota_setup();
#endif
  watchdog_setup();

  mqtt_setup();

  tcpServer.begin();
  tcpServer.setNoDelay(true);
  // O antigo GET /norte saiu: definir o norte e mudar estado, e fazia isso sem
  // senha e por GET -- bastava alguem na rede abrir o link para descalibrar a
  // biruta. Agora e POST /config/norte, atras da pagina de configuracao.
  http.on("/", handleStatus);
  http.on("/status.json", handleJson);
  config_rotas();
  http.begin();
  Serial.printf("[tcp] stream na porta %u | status em http://%s/\n",
                TCP_PORT, WiFi.localIP().toString().c_str());
}

void loop() {
  // Ler o pod vem primeiro, SEMPRE. Sem rede, a coleta continua e o buffer
  // guarda -- que e exatamente para isso que ele existe. A versao anterior
  // parava de coletar durante o portal, o que anulava o buffer justo na hora
  // em que ele era necessario.
  vento_loop();        // le direcao e velocidade do vento
  fonte_loop();        // le o pod (USB host ou UART) e chama nova_linha()
  rede_vigiar();       // abre o portal se a rede sumir, fecha se ninguem usar

  if (rede_em_portal()) {    // sem rede: so o portal, mas ja coletando
    rede_portal_loop();
    led_loop();
    watchdog_loop();
    return;
  }
  acceptClients();
  pumpClients();
  mqtt_loop();         // publica no broker o que ainda nao foi
  http.handleClient();

  if (WiFi.status() == WL_CONNECTED && millis() - lastBeaconMs > BEACON_EVERY_MS) {
    sendBeacon();
    lastBeaconMs = millis();
  }

  static bool wasUp = true;
  bool up = WiFi.status() == WL_CONNECTED;
  if (up != wasUp) {
    Serial.println(up ? "[wifi] voltou" : "[wifi] caiu");
    if (up) udp.begin(BEACON_PORT);
    wasUp = up;
  }
  wifiCaiuEm = up ? 0 : (wifiCaiuEm ? wifiCaiuEm : millis());

#if OTA_LIGADO
  ArduinoOTA.handle();
#endif
  led_loop();
  watchdog_loop();
}
