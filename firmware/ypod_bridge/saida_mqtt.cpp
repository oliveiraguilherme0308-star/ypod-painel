/*
 * Saida MQTT - publica cada leitura num broker
 * --------------------------------------------
 * Por que MQTT e nao so o TCP direto: o TCP de hoje exige que o PC alcance a
 * ponte, o que so vale dentro da mesma rede. No MQTT a ponte faz conexao de
 * SAIDA para o broker, entao um servidor em qualquer lugar pode assinar sem
 * ninguem precisar abrir porta no roteador da escola.
 *
 * O que importa aqui e nao perder leitura:
 *
 *   - Publica com QoS 1. O broker confirma cada mensagem (PUBACK), e so entao
 *     a ponte considera aquela linha entregue. Com QoS 0 (o que a lib
 *     PubSubClient dos tutoriais faz) nao ha confirmacao: Wi-Fi oscilou, o dado
 *     sumiu e ninguem fica sabendo.
 *   - QoS 1 e "pelo menos uma vez", entao duplica de proposito. Cada mensagem
 *     leva boot+seq e o servidor descarta repetido pelo indice unico.
 *   - Caiu a conexao? O ponteiro volta para a ultima confirmada e a ponte
 *     republica dali, direto do mesmo buffer circular que o TCP usa.
 *
 * O campo "age" e a idade da linha em ms no instante do envio, e nao um
 * horario. Assim o servidor calcula a hora certa (agora - age) sem os dois
 * relogios precisarem estar sincronizados - a ponte nem tem relogio.
 *
 * Usa o cliente MQTT que ja vem no core do ESP32 (esp-mqtt, do ESP-IDF). Nao
 * precisa instalar biblioteca nenhuma.
 */
#include "ponte.h"

#if MQTT_LIGADO

#include <WiFi.h>
#include "mqtt_client.h"

static esp_mqtt_client_handle_t s_cli = nullptr;
static bool     s_conectado = false;
static uint32_t s_publicadas = 0, s_confirmadas = 0;
static char     s_topico_raw[96];
static char     s_topico_status[96];

// Linhas publicadas esperando o PUBACK do broker.
struct Pendente {
  int      msg_id;
  uint32_t seq;
  bool     ok;
};
static const int MAX_VOO = 16;      // quantas podem estar em voo ao mesmo tempo
static Pendente s_voo[MAX_VOO];
static int      s_n_voo = 0;

static uint32_t s_enviado    = 0;   // ultima linha entregue ao cliente MQTT
static uint32_t s_confirmado = 0;   // ultima linha confirmada pelo broker

// --------------------------------------------------------------------------
static void marcar_confirmada(int msg_id) {
  for (int i = 0; i < s_n_voo; i++)
    if (s_voo[i].msg_id == msg_id) { s_voo[i].ok = true; break; }
  // avanca enquanto as mais antigas ja confirmaram (tem que ser em ordem)
  int i = 0;
  while (i < s_n_voo && s_voo[i].ok) {
    s_confirmado = s_voo[i].seq;
    s_confirmadas++;
    i++;
  }
  if (i > 0) {
    memmove(s_voo, s_voo + i, (s_n_voo - i) * sizeof(Pendente));
    s_n_voo -= i;
  }
}

static void evento_mqtt(void *arg, esp_event_base_t base, int32_t id, void *data) {
  esp_mqtt_event_handle_t e = (esp_mqtt_event_handle_t)data;
  switch ((esp_mqtt_event_id_t)id) {
    case MQTT_EVENT_CONNECTED:
      s_conectado = true;
      Serial.println("[mqtt] conectado ao broker");
      esp_mqtt_client_publish(s_cli, s_topico_status, "online", 6, 1, 1);
      // o que nao foi confirmado volta para a fila
      s_enviado = s_confirmado;
      s_n_voo = 0;
      break;
    case MQTT_EVENT_DISCONNECTED:
      s_conectado = false;
      Serial.println("[mqtt] caiu, o cliente tenta reconectar sozinho");
      break;
    case MQTT_EVENT_PUBLISHED:
      marcar_confirmada(e->msg_id);
      break;
    case MQTT_EVENT_ERROR:
      Serial.println("[mqtt] erro de transporte");
      break;
    default:
      break;
  }
}

// Escapa o que o JSON exige. A linha do pod e CSV limpo, mas ruido na serial
// pode trazer qualquer byte -- e uma aspa solta quebraria o payload inteiro.
static size_t json_escapar(const char *in, char *out, size_t max) {
  size_t n = 0;
  for (const char *p = in; *p && n + 7 < max; p++) {
    unsigned char c = (unsigned char)*p;
    if (c == '"' || c == '\\') {
      out[n++] = '\\';
      out[n++] = c;
    } else if (c < 0x20 || c == 0x7F) {
      n += snprintf(out + n, max - n, "\\u%04x", c);
    } else {
      out[n++] = c;
    }
  }
  out[n] = '\0';
  return n;
}

// --------------------------------------------------------------------------
void mqtt_setup() {
  if (MQTT_BROKER[0] == '\0') {
    Serial.println("[mqtt] sem broker configurado, publicacao desligada");
    return;
  }
  snprintf(s_topico_raw, sizeof(s_topico_raw), "%s/raw", MQTT_TOPICO_BASE);
  snprintf(s_topico_status, sizeof(s_topico_status), "%s/status", MQTT_TOPICO_BASE);

  esp_mqtt_client_config_t cfg = {};
  cfg.broker.address.uri        = MQTT_BROKER;
  cfg.credentials.client_id     = HOSTNAME;
  if (MQTT_USUARIO[0]) {
    cfg.credentials.username                 = MQTT_USUARIO;
    cfg.credentials.authentication.password  = MQTT_SENHA;
  }
  // Last Will: se a ponte sumir sem avisar, o broker publica isto por ela.
  cfg.session.last_will.topic   = s_topico_status;
  cfg.session.last_will.msg     = "offline";
  cfg.session.last_will.msg_len = 7;
  cfg.session.last_will.qos     = 1;
  cfg.session.last_will.retain  = 1;
  cfg.session.keepalive         = 30;
  cfg.buffer.size               = 1024;
  cfg.network.reconnect_timeout_ms = 4000;

  s_cli = esp_mqtt_client_init(&cfg);
  if (!s_cli) {
    Serial.println("[mqtt] nao consegui criar o cliente");
    return;
  }
  esp_mqtt_client_register_event(s_cli, MQTT_EVENT_ANY, evento_mqtt, nullptr);
  esp_mqtt_client_start(s_cli);
  Serial.printf("[mqtt] publicando em %s (broker %s)\n", s_topico_raw, MQTT_BROKER);
}

// Chamada do loop(): manda o que ainda nao foi para o broker.
void mqtt_loop() {
  if (!s_cli || !s_conectado) return;

  char linha[LINHA_MAX * 2 + 8];
  char payload[LINHA_MAX * 2 + 128];

  while (s_n_voo < MAX_VOO && s_enviado < ring_ultima_seq()) {
    uint32_t prox = s_enviado + 1;
    uint32_t ms = 0;
    const char *crua = ring_linha(prox, &ms);
    if (!crua) {                       // ja saiu do buffer, nao da para recuperar
      s_enviado = prox;
      s_confirmado = prox;
      continue;
    }
    json_escapar(crua, linha, sizeof(linha));
    int n = snprintf(payload, sizeof(payload),
                     "{\"boot\":\"%08lx\",\"seq\":%lu,\"age\":%lu,\"line\":\"%s\"}",
                     (unsigned long)bootId, (unsigned long)prox,
                     (unsigned long)(millis() - ms), linha);
    int msg_id = esp_mqtt_client_publish(s_cli, s_topico_raw, payload, n, 1, 0);
    if (msg_id < 0) break;             // fila do cliente cheia, tenta depois
    s_voo[s_n_voo].msg_id = msg_id;
    s_voo[s_n_voo].seq    = prox;
    s_voo[s_n_voo].ok     = false;
    s_n_voo++;
    s_enviado = prox;
    s_publicadas++;
  }
}

void mqtt_status(char *out, size_t n) {
  if (!s_cli) {
    snprintf(out, n, "desligado");
    return;
  }
  snprintf(out, n, "%s - %lu enviadas, %lu confirmadas, %d em voo",
           s_conectado ? "conectado" : "reconectando",
           (unsigned long)s_publicadas, (unsigned long)s_confirmadas, s_n_voo);
}

#else   // MQTT desligado em ponte.h

void mqtt_setup() {}
void mqtt_loop() {}
void mqtt_status(char *out, size_t n) { snprintf(out, n, "desligado"); }

#endif
