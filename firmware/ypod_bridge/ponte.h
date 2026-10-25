/*
 * YPOD Bridge - configuracao e declaracoes comuns
 * ----------------------------------------------
 * Mexa aqui: rede, pinos e de onde vem o dado do pod.
 */
#pragma once
#include <Arduino.h>

// --------------------------------------------------------------------------
// 1. DE ONDE VEM O DADO DO POD
// --------------------------------------------------------------------------
//   FONTE_USB_HOST = o cabo USB do pod entra no ESP32 (porta "USB" da placa).
//                    O ESP32 vira host USB e le a mesma porta serial que o PC
//                    enxerga hoje. Funciona sem tocar no firmware do pod.
//   FONTE_UART     = fio no pino TX (D1) do pod. So funciona se o firmware do
//                    pod escrever em Serial1 -- no UNO R4 a USB NAO passa por
//                    D0/D1, entao com Serial.print() o D1 fica mudo.
#define FONTE_USB_HOST 1
#define FONTE_UART     2

#define FONTE_DO_POD   FONTE_USB_HOST

// --------------------------------------------------------------------------
// 2. REDE
// --------------------------------------------------------------------------
extern const char *WIFI_SSID;
extern const char *WIFI_PASS;
extern const char *HOSTNAME;

const uint16_t TCP_PORT    = 3333;   // stream de dados para o PC
const uint16_t BEACON_PORT = 3334;   // anuncio UDP (descoberta)
const uint16_t HTTP_PORT   = 80;     // pagina de status

// --------------------------------------------------------------------------
// 2b. MQTT (opcional) - publica cada leitura num broker
// --------------------------------------------------------------------------
// Deixe MQTT_LIGADO em 0 para usar so o TCP direto. Com 1, a ponte publica
// alem do TCP: os dois convivem, e o TCP continua servindo o painel local
// mesmo se o broker estiver fora do ar.
//
// MQTT_BROKER aceita "mqtt://host:1883" ou "mqtts://host:8883" (com TLS).
// Vazio = nao publica. Tudo que muda ao trocar de broker esta nestas 4 linhas.
#define MQTT_LIGADO 1

#define MQTT_BROKER       ""              // ex: "mqtt://192.168.0.10:1883"
#define MQTT_TOPICO_BASE  "ypod/pod1"     // vira ypod/pod1/raw e ypod/pod1/status
#define MQTT_USUARIO      ""              // vazio = broker sem autenticacao
#define MQTT_SENHA        ""

// --------------------------------------------------------------------------
// 2b-bis. Rede configuravel, LED e atualizacao pelo ar
// --------------------------------------------------------------------------
// As credenciais abaixo sao so o PADRAO DE FABRICA. Na primeira vez a ponte
// tenta com elas; se nao conseguir, abre uma rede propria chamada AP_SETUP_SSID
// e serve uma pagina onde voce escolhe a rede e digita a senha. O que voce
// escolher fica gravado na memoria nao-volatil e passa a valer, sem cabo e sem
// regravar.
//
// Para esquecer a rede salva e voltar ao portal: segure o botao BOOT (GPIO0)
// durante os primeiros 3 segundos depois de ligar.
#define AP_SETUP_SSID     "ypod-setup"

// Senha do portal: PADRAO DE FABRICA, como a que vem no adesivo de um roteador.
// Fica documentada de proposito, para o grupo conseguir configurar a ponte sem
// depender de ninguem. Quem quiser trocar, troca na pagina de configuracao
// (http://ypod-bridge.local/config) -- a senha escolhida fica na NVS e passa a
// valer no lugar desta, sem recompilar.
// Minimo 8 caracteres; "" = rede aberta.
#define AP_SETUP_SENHA    "ypod1234"
#define WIFI_TENTAR_S     30            // quanto esperar antes de abrir o portal

// Se a rede cair DEPOIS de a ponte ja estar rodando, ela nao fica tentando em
// silencio para sempre: passado WIFI_PERDIDO_S sem conseguir voltar, o portal
// abre sozinho, para voce poder apontar a ponte para outra rede sem cabo.
//
// E o portal nao fica aberto eternamente: passado PORTAL_TIMEOUT_S sem ninguem
// configurar nada, ele fecha e a ponte volta a tentar a rede salva. Isso cobre
// o caso comum de a rede ter so caido e voltado -- sem esse revezamento, uma
// queda de cinco minutos deixaria a ponte presa no portal ate alguem ir la.
//
// A coleta do pod continua rodando em todos esses estados. Zero desliga.
#define WIFI_PERDIDO_S    300           // 5 min sem rede -> abre o portal
#define PORTAL_TIMEOUT_S  300           // 5 min de portal -> tenta a rede de novo
#define PINO_BOTAO_RESET  0             // BOOT na maioria das placas

// LED RGB da placa: diz o estado sem precisar de cabo nem rede.
//   azul piscando  procurando Wi-Fi        verde pulsando  recebendo do pod
//   roxo fixo      portal de configuracao  amarelo         na rede, pod sumiu
//   vermelho       falha                   
// Em placas sem LED RGB, deixe LED_LIGADO 0.
#define LED_LIGADO        1
#define LED_BRILHO        12            // 0-255; baixo ja e bem visivel

// Atualizacao de firmware pela rede (Arduino IDE -> Porta -> rede).
#define OTA_LIGADO        1

// A senha do OTA NAO mora aqui: quem a souber e estiver na mesma rede grava o
// firmware que quiser na placa, entao ela nao pode ir para um repositorio
// publico. Ela vem de segredos.h, que esta no .gitignore.
#if __has_include("segredos.h")
  #include "segredos.h"
#else
  #error "Falta o arquivo segredos.h. Copie segredos.exemplo.h para segredos.h (na pasta firmware/ypod_bridge) e preencha a senha do OTA."
#endif

// --------------------------------------------------------------------------
// 2c. Watchdog - a ponte se reinicia quando trava
// --------------------------------------------------------------------------
// Existe porque uma ponte muda e sem ninguem por perto e pior que uma ponte
// que reinicia: ela fica horas sem coletar e ninguem percebe. Dois niveis:
//
//  - o watchdog do chip, que reinicia se o loop principal travar de vez;
//  - os de aplicacao abaixo, que reiniciam quando algo parou de funcionar
//    embora o programa continue rodando (o pod sumiu, o Wi-Fi nao volta).
//
// Em qualquer caso o buffer e perdido no reinicio, mas o pod continua medindo
// e o servidor so vera um buraco de alguns segundos. Zero desliga a checagem.
#define WDT_LOOP_S        30    // trava do loop principal
#define WDT_SEM_POD_S     600   // 10 min sem linha nenhuma do pod
#define WDT_SEM_WIFI_S    900   // 15 min sem conseguir voltar para a rede

// --------------------------------------------------------------------------
// 2d. Energia
// --------------------------------------------------------------------------
// A ponte precisa estar acordada o tempo todo: ela e o host USB do pod, e um
// host que dorme derruba o dispositivo. Entao sono profundo esta fora -- o que
// da para fazer e baixar o consumo do radio e da CPU.
//
// ATENCAO: os dois ajustes abaixo QUEBRAM O USB HOST. Ja foi testado em
// hardware: com CPU_MHZ 160 e WIFI_ECONOMIA 1, o pod deixa de ser enumerado --
// a ponte fica em "esperando o pod" para sempre, embora compile e conecte no
// Wi-Fi normalmente. A enumeracao USB depende de temporizacao, e tanto baixar
// o clock quanto por o radio para dormir a atrapalham.
//
// Mexer neles economiza cerca de 0,3 W e custa a leitura do pod. Nao vale.
// Se for tentar de novo, teste o USB depois -- nao basta compilar e ver o
// Wi-Fi conectar.
//
//   WIFI_ECONOMIA 1  radio cochila entre beacons. QUEBRA o USB host.
//   CPU_MHZ          240 e o unico valor com USB host confiavel.
#define WIFI_ECONOMIA     0
#define CPU_MHZ           240

// --------------------------------------------------------------------------
// 2e. Sensores de vento (ligados no proprio ESP, nao no pod)
// --------------------------------------------------------------------------
// Direcao: AS5600, encoder magnetico de 12 bits, por I2C (endereco fixo 0x36).
// Velocidade: tacometro optico, pulsos contados pelo periferico PCNT.
//
// Os dois sao alimentados com 3,3 V -- nunca 5 V: os sinais sairiam em 5 V e o
// ESP32-S3 aceita no maximo 3,6 V num pino de entrada.
#define VENTO_LIGADO      1

#define PINO_SDA          8     // AS5600
#define PINO_SCL          9
#define PINO_TACOMETRO    5     // D0 do tacometro, contado por hardware (PCNT)

// Furos do disco do tacometro: rpm = 60 * pulsos / (N * segundos).
// CONFIRA no seu disco -- com o numero errado a velocidade sai proporcional,
// mas com a escala toda errada.
#define TACO_FUROS        12    // conferido no disco

// Conversao de rotacao para velocidade do vento: v = TACO_K * rpm (m/s).
// 0 = nao converter, publica a rotacao crua em rpm. Deixe 0 ate calibrar
// contra um anemometro de referencia; converter com um fator inventado so
// esconde que o dado ainda nao vale.
#define TACO_K            0.0f

// Com que frequencia ler os sensores (ms). O vento muda mais rapido que o ar,
// entao lemos a parte mais densa e entregamos o valor ja medio.
#define VENTO_INTERVALO   1000

// --------------------------------------------------------------------------
// 3. UART (so usada se FONTE_DO_POD = FONTE_UART)
// --------------------------------------------------------------------------
const int  YPOD_RX_PIN = 18;         // entra o TX do pod, via divisor 10k/20k
const int  YPOD_TX_PIN = 17;         // nao usado; a UART so pede um pino
const long YPOD_BAUD   = 9600;

// --------------------------------------------------------------------------
// 3b. Compactacao do buffer
// --------------------------------------------------------------------------
// O pod manda ~27 linhas por minuto. O PC, ao receber, junta tudo do minuto,
// tira a media e grava SO a media -- as outras 26 sao descartadas. Guardar as
// 27 no buffer e, portanto, proteger com todo cuidado um detalhe que sera
// jogado fora na chegada.
//
// Com a compactacao, o buffer passa a guardar:
//
//   as ultimas DETALHE_LINHAS linhas .... uma a uma, como hoje
//   antes disso ......................... uma media por minuto
//
// Na mesma memoria, a autonomia sai de ~12 horas para ~13 dias. Uma queda
// curta (PC reiniciou, Wi-Fi piscou) volta com fidelidade total; uma queda
// longa volta com o que o PC guardaria de qualquer jeito.
//
// Zero desliga e o buffer volta a ser so linha crua.
#define COMPACTAR_BUFFER  1
#define DETALHE_LINHAS    1600    // ~1 h de linhas cruas antes de compactar

// Indices dos campos NA LINHA DO POD, para a ponte conseguir tirar a media.
// ATENCAO: tem que bater com FIELDS em ypod_monitor.py. Sao os dois unicos
// lugares que conhecem estas posicoes, e o testes/contrato_test.py compara os
// dois -- se divergirem, o teste acusa.
#define CAMPOS_NA_LINHA   21      // linha completa do pod (a virgula final conta)
#define IDX_POD_TIME      0
#define IDX_POD_ID        3
#define IDX_FIRMWARE      4
// numericos: {indice, e_circular}
#define CAMPOS_MEDIA { {7,0},{8,0},{10,0},{11,0},{12,0},{14,0},{15,0},{16,0}, \
                       {17,0},{18,0},{19,0},{20,0},{21,1} }
#define N_CAMPOS_MEDIA    13

// --------------------------------------------------------------------------
// 4. Limites
// --------------------------------------------------------------------------
const size_t  LINHA_MAX    = 192;    // maior linha que o pod manda
const size_t  RING_SMALL   = 400;    // linhas no buffer sem PSRAM (~20 min)
// Com PSRAM: 20000 linhas ~ 3,9 MB. Numa placa R8 (8 MB) isso usa menos da
// metade e segura mais de 13 h de dados com o PC fora do ar -- uma noite
// inteira. Se a sua placa tiver menos PSRAM, baixe este numero; se a alocacao
// falhar, a ponte cai sozinha para RING_SMALL.
const size_t  RING_PSRAM   = 20000;
const uint8_t MAX_CLIENTS  = 3;

// --------------------------------------------------------------------------
// Interface entre o nucleo e a fonte de dados
// --------------------------------------------------------------------------
void fonte_setup();                  // prepara a fonte (USB host ou UART)
void fonte_loop();                   // chamada a cada volta do loop()
void nova_linha(const char *linha);  // a fonte chama isto a cada linha inteira
void fonte_status(char *out, size_t n);   // texto curto para a pagina de status

// Saida MQTT (saida_mqtt.cpp). Vira funcao vazia quando MQTT_LIGADO = 0.
void mqtt_setup();
void mqtt_loop();
void mqtt_status(char *out, size_t n);

// Acesso ao buffer circular, que e de quem publica tanto por TCP quanto por
// MQTT. ring_linha() devolve nullptr quando aquela linha ja foi sobrescrita.
extern uint32_t bootId;
extern uint32_t seqCount;      // linhas recebidas do pod desde o boot
extern uint32_t lastLineMs;    // millis da ultima linha

// Compactacao do buffer (compactar.cpp). Viram funcoes vazias com
// COMPACTAR_BUFFER = 0.
bool     compactar_somar(const char *linha, uint32_t ms, uint32_t seq);
bool     compactar_fechou(uint32_t ms);   // chegou linha de outro minuto?
bool     compactar_tem_balde();
uint32_t compactar_minuto();
uint32_t compactar_seq();                 // ultima linha do balde aberto
uint32_t compactar_ms_meio();             // carimbo: o meio do balde
size_t   compactar_linha(char *out, size_t tam);   // escreve a media e zera
uint8_t  compactar_contar_campos(const char *linha);

// Rede configuravel (config_rede.cpp)
void rede_conectar();
void rede_portal_loop();
bool rede_em_portal();
void rede_esquecer();             // apaga so ssid/senha, nao a calibracao
void rede_vigiar();               // abre/fecha o portal conforme a rede cai e volta
void rede_abrir_portal();         // abre agora, a pedido da pagina de configuracao
String rede_senha_ap();           // a que vale: NVS se houver, senao a de fabrica
bool rede_definir_senha_ap(const String &nova);   // "" volta ao padrao
bool rede_senha_ap_e_padrao();

// LED de status (led_status.cpp)
void led_setup();
void led_loop();
void led_pulso();

// Sensores de vento (sensores_vento.cpp). Viram funcoes vazias com
// VENTO_LIGADO = 0.
void vento_setup();
void vento_loop();
bool vento_valido();              // ha ima no AS5600 e leitura recente?
float vento_direcao();            // graus, 0 = norte definido na calibracao
float vento_velocidade();         // m/s se TACO_K > 0, senao rpm
uint16_t vento_angulo_cru();      // 0-4095, antes do offset (para calibrar)
void vento_definir_norte();       // grava o angulo atual como zero
int vento_offset();               // offset gravado, em passos de 0-4095
int vento_furos();                // furos do disco, como esta valendo
float vento_k();                  // fator rpm->m/s; 0 = publica rpm cru
float vento_rpm();                // rotacao crua, antes do fator
bool vento_definir_calibracao(int furos, float k);   // grava na NVS
void vento_status(char *out, size_t n);

// Pagina de configuracao (pagina_config.cpp). Registra as rotas no servidor
// HTTP principal; chamada uma vez no setup().
void config_rotas();
uint32_t    ring_ultima_seq();
const char *ring_linha(uint32_t seq, uint32_t *ms);

// Montador de linhas: a fonte joga os bytes crus aqui e nao se preocupa com
// onde a linha termina.
class MontadorDeLinha {
 public:
  void byte_recebido(uint8_t c) {
    if (c == '\n' || c == '\r') {
      if (len_ > 0) {
        buf_[len_] = '\0';
        nova_linha(buf_);
        len_ = 0;
      }
    } else if (len_ < LINHA_MAX - 1) {
      buf_[len_++] = (char)c;
    } else {
      len_ = 0;    // linha gigante = lixo
    }
  }
  void bytes_recebidos(const uint8_t *p, size_t n) {
    for (size_t i = 0; i < n; i++) byte_recebido(p[i]);
  }
  void limpar() { len_ = 0; }

 private:
  char   buf_[LINHA_MAX];
  size_t len_ = 0;
};
