"""
Monta a pasta site/ (o painel publico) a partir do painel local.

    python tools/gerar_site.py https://ypod-api.ypod-sm.workers.dev
    wrangler deploy -c wrangler.site.jsonc

O painel e um arquivo so (static/dashboard.html), usado pelo servidor local e
pelo site publico. A unica diferenca e de onde ele busca os dados: aqui a URL
do Worker vai na meta tag "ypod-api". Rode de novo sempre que mudar o painel.

Gera:
    site/index.html
    site/static/chart.umd.min.js
    site/.nojekyll          (o Pages serve os arquivos como estao)

So site/ e publicado -- o resto do projeto tem senhas de fabrica da ponte
(ponte.h) e o banco local.
"""
import os
import re
import shutil
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ORIGEM = os.path.join(RAIZ, "static", "dashboard.html")
CHART = os.path.join(RAIZ, "static", "chart.umd.min.js")
DESTINO = os.path.join(RAIZ, "site")

# O site publico consulta o Worker; cada consulta conta na cota gratis da
# Cloudflare (100 mil/dia). O PC so manda um retrato novo a cada 15 s, entao
# perguntar mais rapido que isso nao traz nada.
REFRESH_MS = 15000


def main():
    if len(sys.argv) != 2 or not sys.argv[1].startswith("https://"):
        print(__doc__)
        sys.exit(1)
    api = sys.argv[1].rstrip("/")

    with open(ORIGEM, encoding="utf-8") as fh:
        html = fh.read()

    html, n1 = re.subn(r'<meta name="ypod-api" content="[^"]*">',
                       '<meta name="ypod-api" content="%s">' % api, html)
    html, n2 = re.subn(r'<meta name="ypod-refresh" content="[^"]*">',
                       '<meta name="ypod-refresh" content="%d">' % REFRESH_MS, html)
    if n1 != 1 or n2 != 1:
        sys.exit("nao achei as meta tags ypod-api/ypod-refresh em dashboard.html")

    os.makedirs(os.path.join(DESTINO, "static"), exist_ok=True)
    with open(os.path.join(DESTINO, "index.html"), "w", encoding="utf-8",
              newline="\n") as fh:
        fh.write(html)
    shutil.copyfile(CHART, os.path.join(DESTINO, "static", "chart.umd.min.js"))
    open(os.path.join(DESTINO, ".nojekyll"), "w").close()

    print("site/ pronto, lendo de %s" % api)
    print("publique com: wrangler deploy -c wrangler.site.jsonc")


if __name__ == "__main__":
    main()
