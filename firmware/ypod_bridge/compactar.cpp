/*
 * Compactacao do buffer: as linhas de um minuto viram uma media
 * ------------------------------------------------------------
 * O pod manda 20 a 30 linhas por minuto (medido; uma a cada 2 ou 3 s). O PC,
 * ao receber, junta o minuto inteiro,
 * tira a media e grava so ela -- as outras 26 sao descartadas. Guardar as 27 no
 * buffer e proteger com zelo um detalhe que sera jogado fora na chegada.
 *
 * Entao: as linhas recentes ficam cruas (uma queda curta volta com fidelidade
 * total) e as antigas viram uma media por minuto. Mesma memoria, autonomia de
 * ~12 horas para ~13 dias.
 *
 * A media sai no MESMO FORMATO da linha do pod -- mesma quantidade de campos,
 * valores nas mesmas posicoes -- porque o servidor le por posicao e, desde
 * 24/10, recusa linha com numero de campos errado. Uma linha compactada mal
 * formada seria descartada em silencio do outro lado.
 *
 * DIRECAO DO VENTO: media aritmetica de angulo mente. Entre 350 e 10 graus ela
 * da 180, a direcao oposta, e o erro aparece justamente com vento de norte.
 * Somamos os vetores unitarios e tiramos atan2 da soma -- a mesma conta que o
 * servidor faz.
 */
#include "ponte.h"

#if COMPACTAR_BUFFER

#include <math.h>

struct CampoMedia { uint8_t idx; uint8_t circular; };
static const CampoMedia CAMPOS[N_CAMPOS_MEDIA] = CAMPOS_MEDIA;

// Acumulador do minuto corrente.
static double   s_soma[N_CAMPOS_MEDIA];     // soma (ou soma dos senos)
static double   s_soma2[N_CAMPOS_MEDIA];    // soma dos cossenos, so circulares
static uint16_t s_n[N_CAMPOS_MEDIA];
static char     s_pod_time[24];
static char     s_pod_id[16];
static char     s_firmware[20];
static uint32_t s_minuto = 0;               // qual minuto esta aberto
static uint32_t s_ms_primeiro = 0;          // millis da primeira linha do balde
static uint32_t s_ultimo_seq = 0;           // numero da ultima linha do balde
static bool     s_aberto = false;

// --------------------------------------------------------------------------
// Copia o campo n da linha (separada por virgula) para out. Devolve false se
// o campo nao existe.
static bool campo(const char *linha, uint8_t n, char *out, size_t tam) {
  const char *p = linha;
  for (uint8_t i = 0; i < n; i++) {
    p = strchr(p, ',');
    if (!p) return false;
    p++;
  }
  const char *fim = strchr(p, ',');
  size_t len = fim ? (size_t)(fim - p) : strlen(p);
  if (len >= tam) len = tam - 1;
  memcpy(out, p, len);
  out[len] = '\0';
  return true;
}

// Quantos campos a linha tem (a virgula final conta como um campo vazio).
uint8_t compactar_contar_campos(const char *linha) {
  uint8_t n = 1;
  for (const char *p = linha; *p; p++) if (*p == ',') n++;
  return n;
}

static void zerar() {
  for (int i = 0; i < N_CAMPOS_MEDIA; i++) {
    s_soma[i] = s_soma2[i] = 0.0;
    s_n[i] = 0;
  }
  s_pod_time[0] = s_pod_id[0] = s_firmware[0] = '\0';
  s_aberto = false;
}

// --------------------------------------------------------------------------
// Junta uma linha ao minuto corrente. Devolve true se a linha foi aceita.
bool compactar_somar(const char *linha, uint32_t ms, uint32_t seq) {
  // Linha fora do formato nao entra na media: ela deslocaria todas as colunas
  // e a media sairia de campos trocados -- o mesmo erro que o servidor passou
  // a recusar. Melhor perder uma linha do que contaminar o minuto.
  uint8_t n = compactar_contar_campos(linha);
  if (n < CAMPOS_NA_LINHA) return false;

  uint32_t minuto = ms / 60000UL;
  if (!s_aberto) {
    zerar();
    s_minuto = minuto;
    s_ms_primeiro = ms;
    s_aberto = true;
  }

  char buf[24];
  for (int i = 0; i < N_CAMPOS_MEDIA; i++) {
    if (!campo(linha, CAMPOS[i].idx, buf, sizeof(buf))) continue;
    if (buf[0] == '\0') continue;                  // campo vazio: sem leitura
    char *fim = nullptr;
    double v = strtod(buf, &fim);
    if (fim == buf) continue;                      // nao era numero
    if (CAMPOS[i].circular) {
      double r = v * M_PI / 180.0;
      s_soma[i]  += sin(r);
      s_soma2[i] += cos(r);
    } else {
      s_soma[i] += v;
    }
    s_n[i]++;
  }
  campo(linha, IDX_POD_TIME, s_pod_time, sizeof(s_pod_time));
  campo(linha, IDX_POD_ID,   s_pod_id,   sizeof(s_pod_id));
  campo(linha, IDX_FIRMWARE, s_firmware, sizeof(s_firmware));
  s_ultimo_seq = seq;
  return true;
}

bool compactar_tem_balde() { return s_aberto; }

uint32_t compactar_minuto() { return s_minuto; }

// Numero da ultima linha que entrou no balde. A entrada compactada ocupa ESSE
// numero no anel: os numeros intermediarios passam a nao existir mais, e tanto
// o TCP quanto o MQTT ja pulam numero ausente sem reclamar.
uint32_t compactar_seq() { return s_ultimo_seq; }

// O balde fecha quando chega linha de outro minuto.
bool compactar_fechou(uint32_t ms) {
  return s_aberto && (ms / 60000UL) != s_minuto;
}

uint32_t compactar_ms_meio() {
  // meio do balde: representa melhor a media do periodo do que a ponta
  return s_ms_primeiro + 30000UL;
}

// --------------------------------------------------------------------------
// Escreve a linha media, no mesmo formato da linha do pod.
size_t compactar_linha(char *out, size_t tam) {
  // comeca com todos os campos vazios
  char campos[CAMPOS_NA_LINHA + 2][16];
  uint8_t total = CAMPOS_NA_LINHA;
  for (int i = 0; i < CAMPOS_NA_LINHA + 2; i++) campos[i][0] = '\0';

  for (int i = 0; i < N_CAMPOS_MEDIA; i++) {
    if (!s_n[i]) continue;
    uint8_t idx = CAMPOS[i].idx;
    if (idx + 1 > total) total = idx + 1;          // vento estica a linha
    if (CAMPOS[i].circular) {
      double sen = s_soma[i] / s_n[i], cos_ = s_soma2[i] / s_n[i];
      if (sen == 0.0 && cos_ == 0.0) continue;     // vetores se cancelaram
      double g = atan2(sen, cos_) * 180.0 / M_PI;
      g = fmod(fmod(g, 360.0) + 360.0, 360.0);
      snprintf(campos[idx], sizeof(campos[0]), "%.1f", g);
    } else {
      snprintf(campos[idx], sizeof(campos[0]), "%.2f", s_soma[i] / s_n[i]);
    }
  }
  strncpy(campos[IDX_POD_TIME], s_pod_time, sizeof(campos[0]) - 1);
  strncpy(campos[IDX_POD_ID],   s_pod_id,   sizeof(campos[0]) - 1);
  strncpy(campos[IDX_FIRMWARE], s_firmware, sizeof(campos[0]) - 1);
  campos[IDX_POD_TIME][sizeof(campos[0]) - 1] = '\0';
  campos[IDX_POD_ID][sizeof(campos[0]) - 1] = '\0';
  campos[IDX_FIRMWARE][sizeof(campos[0]) - 1] = '\0';

  size_t p = 0;
  for (uint8_t i = 0; i < total && p < tam - 2; i++) {
    if (i) out[p++] = ',';
    size_t l = strlen(campos[i]);
    if (p + l >= tam - 2) break;
    memcpy(out + p, campos[i], l);
    p += l;
  }
  out[p] = '\0';
  zerar();
  return p;
}

#else   // compactacao desligada

bool compactar_somar(const char *, uint32_t, uint32_t) { return false; }
bool compactar_tem_balde() { return false; }
bool compactar_fechou(uint32_t) { return false; }
uint32_t compactar_minuto() { return 0; }
uint32_t compactar_seq() { return 0; }
uint32_t compactar_ms_meio() { return 0; }
size_t compactar_linha(char *out, size_t) { out[0] = '\0'; return 0; }
uint8_t compactar_contar_campos(const char *linha) {
  uint8_t n = 1;
  for (const char *p = linha; *p; p++) if (*p == ',') n++;
  return n;
}

#endif
