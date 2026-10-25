/*
 * Rede configuravel sem cabo
 * --------------------------
 * As credenciais em ponte.h sao apenas o padrao de fabrica. O que vale e o que
 * estiver gravado na memoria nao-volatil (NVS), escolhido por voce num portal.
 *
 * Como funciona ao ligar:
 *   1. le as credenciais salvas; se nao houver, usa as do codigo
 *   2. tenta conectar por WIFI_TENTAR_S segundos
 *   3. se nao conseguir, abre a rede AP_SETUP_SSID e serve uma pagina onde voce
 *      ve as redes ao alcance, escolhe uma e digita a senha
 *   4. salva e reinicia, ja conectando na rede nova
 *
 * Existe porque trocar de rede nao pode exigir notebook, cabo e regravacao --
 * especialmente com a ponte instalada em algum lugar de dificil acesso.
 *
 * Para esquecer a rede salva: segure o botao BOOT nos primeiros 3 segundos.
 */
#include "ponte.h"
#include <WiFi.h>
#include <WebServer.h>
#include <DNSServer.h>
#include <Preferences.h>

static Preferences prefs;
static DNSServer dns;
static WebServer portal(80);
static bool em_portal = false;
static uint32_t s_portal_abriu = 0;   // millis em que o portal subiu

// A varredura de redes ocupa o radio inteiro e derrubaria quem estivesse
// conectado no portal. Por isso ela acontece UMA vez, antes de o AP subir, e o
// resultado fica guardado aqui.
static String s_redes_html = "";

// --------------------------------------------------------------------------
static String ssid_salvo() {
  prefs.begin("ypod", true);
  String v = prefs.getString("ssid", "");
  prefs.end();
  return v;
}

static String senha_salva() {
  prefs.begin("ypod", true);
  String v = prefs.getString("senha", "");
  prefs.end();
  return v;
}

static void salvar(const String &ssid, const String &senha) {
  prefs.begin("ypod", false);
  prefs.putString("ssid", ssid);
  prefs.putString("senha", senha);
  prefs.end();
}

// Apaga SO as credenciais de rede. Antes isto era um prefs.clear(), que limpa
// o namespace inteiro -- e levava junto a calibracao do norte e a senha do
// portal. Quem segura o BOTAO para trocar de rede nao esta pedindo para
// descalibrar a biruta.
void rede_esquecer() {
  prefs.begin("ypod", false);
  prefs.remove("ssid");
  prefs.remove("senha");
  prefs.end();
  Serial.println("[rede] credenciais de Wi-Fi apagadas");
}

// --------------------------------------------------------------------------
// Senha do portal: a de ponte.h e o padrao de fabrica; o que estiver na NVS
// manda. Existe para a ponte nao ficar para sempre com a senha que esta
// publicada no repositorio.
// --------------------------------------------------------------------------
String rede_senha_ap() {
  prefs.begin("ypod", true);
  String v = prefs.getString("apsenha", "");
  prefs.end();
  return v.length() ? v : String(AP_SETUP_SENHA);
}

bool rede_definir_senha_ap(const String &nova) {
  if (nova.length() && nova.length() < 8) return false;   // regra do WPA2
  prefs.begin("ypod", false);
  if (nova.length()) prefs.putString("apsenha", nova);
  else               prefs.remove("apsenha");             // volta ao padrao
  prefs.end();
  Serial.printf("[rede] senha do portal %s\n",
                nova.length() ? "trocada" : "voltou ao padrao de fabrica");
  return true;
}

bool rede_senha_ap_e_padrao() {
  prefs.begin("ypod", true);
  bool propria = prefs.isKey("apsenha");
  prefs.end();
  return !propria;
}

bool rede_em_portal() { return em_portal; }

// --------------------------------------------------------------------------
// Pagina do portal
// --------------------------------------------------------------------------
static void pagina_inicial() {
  String h = F("<!doctype html><meta charset=utf-8>"
               "<meta name=viewport content='width=device-width,initial-scale=1'>"
               "<title>YPOD - configurar rede</title><style>"
               "body{font:16px system-ui;background:#1a1a19;color:#fff;margin:0;padding:24px;"
               "max-width:520px;margin:0 auto}h1{font-size:19px;margin:0 0 6px}"
               "p{color:#8a8980;font-size:14px;margin:0 0 20px}"
               "label{display:block;font-size:13px;color:#c3c2b7;margin:14px 0 5px}"
               "input,select{width:100%;box-sizing:border-box;padding:11px;font-size:16px;"
               "background:#232320;color:#fff;border:1px solid #33332f;border-radius:8px}"
               "button{width:100%;margin-top:20px;padding:13px;font-size:16px;font-weight:600;"
               "background:#3987e5;color:#fff;border:0;border-radius:8px}"
               "small{color:#8a8980;display:block;margin-top:14px;font-size:12.5px}"
               "</style><h1>Configurar a rede da ponte</h1>"
               "<p>Escolha a rede Wi-Fi que a ponte deve usar. Ela precisa ser de "
               "2,4 GHz.</p><form method=POST action=/salvar>"
               "<label>Rede</label><select name=ssid>");

  h += s_redes_html;
  h += F("</select><label>Senha</label>"
         "<input name=senha type=password placeholder='senha da rede'>"
         "<button type=submit>Salvar e conectar</button></form>"
         "<small>A ponte reinicia e tenta entrar na rede escolhida. Se nao "
         "conseguir, este portal volta a aparecer.</small>");
  portal.send(200, "text/html; charset=utf-8", h);
}

static void pagina_salvar() {
  String ssid = portal.arg("ssid");
  String senha = portal.arg("senha");
  if (ssid.length() == 0) {
    portal.send(400, "text/html; charset=utf-8", "Escolha uma rede.");
    return;
  }
  salvar(ssid, senha);
  String h = "<!doctype html><meta charset=utf-8>"
             "<meta name=viewport content='width=device-width,initial-scale=1'>"
             "<style>body{font:16px system-ui;background:#1a1a19;color:#fff;"
             "padding:40px 24px;text-align:center}</style>"
             "<h2>Salvo</h2><p style='color:#8a8980'>A ponte vai reiniciar e "
             "tentar entrar em <b style='color:#fff'>" + ssid + "</b>.</p>";
  portal.send(200, "text/html; charset=utf-8", h);
  Serial.printf("[rede] salvo: %s - reiniciando\n", ssid.c_str());
  delay(1200);
  ESP.restart();
}

// --------------------------------------------------------------------------
static void varrer_redes() {
  WiFi.mode(WIFI_STA);
  WiFi.disconnect(false, false);
  delay(100);
  int n = WiFi.scanNetworks();
  s_redes_html = "";
  for (int i = 0; i < n && i < 20; i++) {
    s_redes_html += "<option value='" + WiFi.SSID(i) + "'>" + WiFi.SSID(i) +
                    "  (" + String(WiFi.RSSI(i)) + " dBm)" + "</option>";
  }
  if (n <= 0) s_redes_html = "<option value=''>(nenhuma rede encontrada)</option>";
  WiFi.scanDelete();
  Serial.printf("[rede] %d redes encontradas\n", n);
}

static void abrir_portal() {
  em_portal = true;
  s_portal_abriu = millis();
  varrer_redes();

  // AP puro, e nao AP_STA: se a parte cliente continuar tentando reconectar na
  // rede antiga, o radio troca de canal e derruba o celular do portal a cada
  // tentativa. Tem que parar de procurar enquanto o portal estiver no ar.
  WiFi.setAutoReconnect(false);
  WiFi.disconnect(true, false);
  WiFi.mode(WIFI_AP);
  delay(200);
  String senha = rede_senha_ap();
  if (senha.length() >= 8) {
    WiFi.softAP(AP_SETUP_SSID, senha.c_str());
  } else {
    WiFi.softAP(AP_SETUP_SSID);
  }
  IPAddress ip = WiFi.softAPIP();
  dns.start(53, "*", ip);          // qualquer endereco cai no portal
  portal.on("/", pagina_inicial);
  portal.on("/salvar", HTTP_POST, pagina_salvar);
  portal.onNotFound(pagina_inicial);
  portal.begin();

  Serial.println("\n========================================");
  Serial.printf("  Nao consegui entrar em nenhuma rede.\n");
  Serial.printf("  Conecte o celular na rede: %s\n", AP_SETUP_SSID);
  if (senha.length() >= 8)
    Serial.printf("  Senha: %s%s\n", senha.c_str(),
                  rede_senha_ap_e_padrao() ? " (padrao de fabrica)" : "");
  Serial.printf("  Depois abra: http://%s\n", ip.toString().c_str());
  Serial.println("========================================\n");
}

// Fecha o portal e volta ao modo cliente, tentando a rede salva de novo.
static void fechar_portal() {
  if (!em_portal) return;
  Serial.println("[rede] fechando o portal, tentando a rede de novo");
  portal.stop();
  dns.stop();
  WiFi.softAPdisconnect(true);
  em_portal = false;

  String ssid = ssid_salvo();
  String senha = senha_salva();
  if (ssid.length() == 0) { ssid = WIFI_SSID; senha = WIFI_PASS; }
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(ssid.c_str(), senha.c_str());
}

// Chamada a cada volta do loop: decide quando abrir e quando fechar o portal.
// Sem isto, uma rede que cai depois do boot deixaria a ponte tentando em
// silencio ate o watchdog reiniciar -- e voce sem como apontar ela para outra
// rede, a nao ser indo la com cabo.
void rede_vigiar() {
#if WIFI_PERDIDO_S > 0
  static uint32_t caiu_em = 0;
  uint32_t agora = millis();

  if (em_portal) {
#if PORTAL_TIMEOUT_S > 0
    if (agora - s_portal_abriu > (uint32_t)PORTAL_TIMEOUT_S * 1000) {
      fechar_portal();
      caiu_em = agora;            // da outra janela antes de reabrir
    }
#endif
    return;
  }

  if (WiFi.status() == WL_CONNECTED) {
    caiu_em = 0;
    return;
  }
  if (!caiu_em) caiu_em = agora;
  if (agora - caiu_em > (uint32_t)WIFI_PERDIDO_S * 1000) {
    Serial.printf("[rede] %ds sem rede - abrindo o portal\n", WIFI_PERDIDO_S);
    abrir_portal();
    caiu_em = 0;
  }
#endif
}

// Abre o portal agora, a pedido (botao na pagina de configuracao). Util para
// apontar a ponte para outra rede sem ter que esperar ela cair, e sem o
// caminho fisico de segurar o BOTAO durante o boot.
void rede_abrir_portal() {
  if (!em_portal) abrir_portal();
}

// Chamada do loop enquanto o portal estiver aberto.
void rede_portal_loop() {
  if (!em_portal) return;
  dns.processNextRequest();
  portal.handleClient();
}

// --------------------------------------------------------------------------
// Conecta usando o que estiver salvo; cai no portal se nao der.
// --------------------------------------------------------------------------
void rede_conectar() {
  // botao BOOT segurado ao ligar apaga a rede salva
  pinMode(PINO_BOTAO_RESET, INPUT_PULLUP);
  uint32_t t0 = millis();
  while (digitalRead(PINO_BOTAO_RESET) == LOW && millis() - t0 < 3000) {
    delay(100);
    if (millis() - t0 > 2500) {
      rede_esquecer();
      break;
    }
  }

  String ssid = ssid_salvo();
  String senha = senha_salva();
  bool do_codigo = false;
  if (ssid.length() == 0) {
    ssid = WIFI_SSID;
    senha = WIFI_PASS;
    do_codigo = true;
  }

  WiFi.mode(WIFI_STA);
  WiFi.setHostname(HOSTNAME);
#if WIFI_ECONOMIA
  WiFi.setSleep(WIFI_PS_MIN_MODEM);
#else
  WiFi.setSleep(false);
#endif
  WiFi.setAutoReconnect(true);
  WiFi.begin(ssid.c_str(), senha.c_str());
  Serial.printf("[wifi] conectando em %s%s", ssid.c_str(),
                do_codigo ? " (padrao do codigo)" : " (salvo)");

  t0 = millis();
  while (WiFi.status() != WL_CONNECTED &&
         millis() - t0 < (uint32_t)WIFI_TENTAR_S * 1000) {
    delay(300);
    led_loop();            // pisca enquanto procura: parado != tentando
    Serial.print(".");
  }
  Serial.println();

  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("[wifi] ok, IP %s  (%s.local)\n",
                  WiFi.localIP().toString().c_str(), HOSTNAME);
    // primeira conexao bem-sucedida com as credenciais do codigo: grava, para
    // nao depender mais delas
    if (do_codigo && ssid.length()) salvar(ssid, senha);
    return;
  }
  abrir_portal();
}
