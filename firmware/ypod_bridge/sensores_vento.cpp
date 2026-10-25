/*
 * Sensores de vento: direcao (AS5600) e velocidade (tacometro)
 * ------------------------------------------------------------
 * Estes dois sensores sao do ESP, nao do pod. As leituras entram na mesma
 * linha de dados, acrescentadas no fim -- o servidor le os campos por posicao,
 * entao campo novo no final e natural para ele.
 *
 * DIRECAO - AS5600 (I2C, endereco 0x36)
 *   Encoder magnetico de 12 bits: devolve a posicao absoluta do ima (0-4095)
 *   com resolucao de 0,088 grau. Ele nao sabe onde fica o norte; isso depende
 *   de como a biruta foi presa no mastro. Por isso existe um offset, gravado
 *   na memoria nao-volatil: aponte a biruta para o norte e chame
 *   vento_definir_norte() (ha um botao na pagina de status).
 *
 * VELOCIDADE - tacometro optico
 *   Um disco com furos passa por um sensor optico e gera pulsos. Contamos com
 *   o periferico PCNT, por hardware: ele nao perde pulso nem quando a CPU esta
 *   ocupada com o USB host e o Wi-Fi. Por interrupcao de software perderia.
 *
 *   rpm = 60 * pulsos / (furos * segundos)
 *
 *   Converter rpm em m/s exige calibrar contra um anemometro de referencia.
 *   Ate la publicamos a rotacao crua: um fator inventado faria o numero
 *   parecer uma velocidade sem ser uma.
 */
#include "ponte.h"

#if VENTO_LIGADO

#include <Wire.h>
#include <Preferences.h>
#include "driver/pulse_cnt.h"

static const uint8_t AS5600 = 0x36;
static const uint8_t REG_RAW_ANGLE = 0x0C;   // 0x0C alto, 0x0D baixo
static const uint8_t REG_STATUS    = 0x0B;   // bits do ima

static pcnt_unit_handle_t s_pcnt = nullptr;
static pcnt_channel_handle_t s_canal = nullptr;

static uint16_t s_angulo_cru = 0;      // 0-4095, como o sensor entrega
static int      s_offset = 0;          // quanto subtrair para o norte dar zero

// Calibracao do tacometro. Os valores de ponte.h sao so o ponto de partida: o
// que vale e o que estiver na NVS, ajustado na pagina de configuracao. Calibrar
// contra um anemometro de referencia exige tentar um fator, medir, corrigir --
// e recompilar e regravar a cada tentativa tornaria isso insuportavel.
static int   s_furos = TACO_FUROS;
static float s_k     = TACO_K;
static bool     s_ima_ok = false;      // o AS5600 ve um ima?
static bool     s_i2c_ok = false;      // o AS5600 respondeu alguma vez?
static float    s_rpm = 0.0f;
static uint32_t s_ultima_leitura = 0;
static uint32_t s_t_anterior = 0;
static int      s_pulsos_anterior = 0;

// --------------------------------------------------------------------------
static bool ler16(uint8_t reg, uint16_t *out) {
  Wire.beginTransmission(AS5600);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom((int)AS5600, 2) != 2) return false;
  uint8_t hi = Wire.read(), lo = Wire.read();
  *out = ((uint16_t)hi << 8 | lo) & 0x0FFF;
  return true;
}

static bool ler8(uint8_t reg, uint8_t *out) {
  Wire.beginTransmission(AS5600);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom((int)AS5600, 1) != 1) return false;
  *out = Wire.read();
  return true;
}

// --------------------------------------------------------------------------
void vento_setup() {
  Preferences p;
  p.begin("ypod", true);
  s_offset = p.getInt("norte", 0);
  s_furos  = p.getInt("furos", TACO_FUROS);
  s_k      = p.getFloat("tacok", TACO_K);
  p.end();
  if (s_furos < 1) s_furos = TACO_FUROS;     // divisor zero mataria a conta

  Wire.begin(PINO_SDA, PINO_SCL, 100000);   // 100 kHz: cabo de ate 50 cm
  uint16_t teste;
  s_i2c_ok = ler16(REG_RAW_ANGLE, &teste);
  Serial.printf("[vento] AS5600 em 0x36: %s | norte gravado em %d\n",
                s_i2c_ok ? "respondeu" : "NAO respondeu (confira SDA/SCL/3V3)",
                s_offset);

  // Contador de pulsos por hardware
  pcnt_unit_config_t uc = {};
  uc.high_limit = 30000;
  uc.low_limit = -1;
  if (pcnt_new_unit(&uc, &s_pcnt) != ESP_OK) {
    Serial.println("[vento] nao consegui criar o contador PCNT");
    return;
  }
  // O filtro descarta pulsos mais curtos que 1 us, que sao ruido do cabo --
  // sem ele, uma interferencia vira rotacao fantasma.
  pcnt_glitch_filter_config_t filtro = {};
  filtro.max_glitch_ns = 1000;
  pcnt_unit_set_glitch_filter(s_pcnt, &filtro);

  pcnt_chan_config_t cc = {};
  cc.edge_gpio_num = PINO_TACOMETRO;
  cc.level_gpio_num = -1;
  if (pcnt_new_channel(s_pcnt, &cc, &s_canal) != ESP_OK) {
    Serial.println("[vento] nao consegui criar o canal PCNT");
    return;
  }
  pcnt_channel_set_edge_action(s_canal, PCNT_CHANNEL_EDGE_ACTION_INCREASE,
                               PCNT_CHANNEL_EDGE_ACTION_HOLD);
  pcnt_unit_enable(s_pcnt);
  pcnt_unit_clear_count(s_pcnt);
  pcnt_unit_start(s_pcnt);
  s_t_anterior = millis();
  Serial.printf("[vento] tacometro no GPIO%d, %d furos por volta, k=%.4f%s\n",
                PINO_TACOMETRO, s_furos, s_k,
                s_k > 0 ? " (m/s)" : " (publica rpm cru)");
}

// --------------------------------------------------------------------------
void vento_loop() {
  uint32_t agora = millis();
  if (agora - s_ultima_leitura < VENTO_INTERVALO) return;
  s_ultima_leitura = agora;

  // --- direcao ---
  uint16_t ang;
  if (ler16(REG_RAW_ANGLE, &ang)) {
    s_angulo_cru = ang;
    s_i2c_ok = true;
    uint8_t st;
    // bit 5 (MD) = ima detectado; sem ima a leitura nao significa nada
    if (ler8(REG_STATUS, &st)) s_ima_ok = (st & 0x20) != 0;
  } else {
    s_i2c_ok = false;
    s_ima_ok = false;
  }

  // --- velocidade ---
  if (s_pcnt) {
    int pulsos = 0;
    pcnt_unit_get_count(s_pcnt, &pulsos);
    int delta = pulsos - s_pulsos_anterior;
    if (delta < 0) delta += 30001;         // o contador deu a volta
    uint32_t dt = agora - s_t_anterior;
    if (dt > 0 && s_furos > 0) {
      s_rpm = (60000.0f * delta) / ((float)s_furos * dt);
    }
    s_pulsos_anterior = pulsos;
    s_t_anterior = agora;
  }
}

// --------------------------------------------------------------------------
bool vento_valido() {
  return s_i2c_ok && s_ima_ok;
}

float vento_direcao() {
  int passos = (int)s_angulo_cru - s_offset;
  while (passos < 0) passos += 4096;
  passos %= 4096;
  return passos * (360.0f / 4096.0f);
}

float vento_velocidade() {
  return s_k > 0.0f ? s_rpm * s_k : s_rpm;
}

uint16_t vento_angulo_cru() { return s_angulo_cru; }
int   vento_offset() { return s_offset; }
int   vento_furos()  { return s_furos; }
float vento_k()      { return s_k; }
float vento_rpm()    { return s_rpm; }

void vento_definir_norte() {
  s_offset = s_angulo_cru;
  Preferences p;
  p.begin("ypod", false);
  p.putInt("norte", s_offset);
  p.end();
  Serial.printf("[vento] norte definido no angulo cru %d\n", s_offset);
}

// Guarda a calibracao do tacometro. furos < 1 e recusado: viraria divisao por
// zero. k = 0 e valido e significa "publique rpm cru", que e o estado honesto
// enquanto ninguem comparou com um anemometro de referencia.
bool vento_definir_calibracao(int furos, float k) {
  if (furos < 1 || k < 0.0f) return false;
  s_furos = furos;
  s_k = k;
  Preferences p;
  p.begin("ypod", false);
  p.putInt("furos", s_furos);
  p.putFloat("tacok", s_k);
  p.end();
  Serial.printf("[vento] calibracao: %d furos, k=%.4f\n", s_furos, s_k);
  return true;
}

void vento_status(char *out, size_t n) {
  if (!s_i2c_ok) {
    snprintf(out, n, "AS5600 nao responde");
    return;
  }
  if (!s_ima_ok) {
    snprintf(out, n, "sem ima detectado (entreferro 0,5-3 mm) · %s %.0f",
             s_k > 0 ? "m/s" : "rpm", vento_velocidade());
    return;
  }
  snprintf(out, n, "%.1f° · %.1f %s · cru %u, norte %d",
           vento_direcao(), vento_velocidade(), s_k > 0 ? "m/s" : "rpm",
           s_angulo_cru, s_offset);
}

#else   // sensores de vento desligados

void vento_setup() {}
void vento_loop() {}
bool vento_valido() { return false; }
float vento_direcao() { return 0; }
float vento_velocidade() { return 0; }
uint16_t vento_angulo_cru() { return 0; }
void vento_definir_norte() {}
int vento_offset() { return 0; }
int vento_furos() { return 0; }
float vento_k() { return 0; }
float vento_rpm() { return 0; }
bool vento_definir_calibracao(int, float) { return false; }
void vento_status(char *out, size_t n) { snprintf(out, n, "desligado"); }

#endif
