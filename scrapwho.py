"""
Extrai o JSON "matchCentreData" das páginas de partida do WhoScored.

No contexto da página, executa o comando:
    require.config.params['args']['matchCentreData']

e salva o resultado retornado em um arquivo .json (um por partida).

Requisitos:
    pip install playwright
    playwright install chromium

Uso:
    # uma partida específica
    python whoscored_matchdata.py --url "https://www.whoscored.com/matches/1955838/live/brazil-brasileirão-2026-gremio-sao-paulo"

    # todas as partidas listadas em brasileirao_2026_partidas.csv
    # (arquivo gerado pelo whoscored_scraper.py)
    python whoscored_matchdata.py
"""

import argparse
import csv
import json
import os
import re
import time

from playwright.sync_api import sync_playwright

INPUT_CSV = "brasileirao_2026_partidas.csv"
OUTPUT_DIR = "match_data"


def parse_match_id(url):
    m = re.search(r"/matches/(\d+)/", url)
    return m.group(1) if m else "unknown"


def aceitar_cookies(page):
    """Tenta fechar banners de cookies comuns (OneTrust etc.)."""
    seletores = [
        "#onetrust-accept-btn-handler",
        "button:has-text('Accept All')",
        "button:has-text('Aceitar')",
        "button:has-text('I Accept')",
    ]
    for sel in seletores:
        try:
            page.locator(sel).click(timeout=3000)
            return
        except Exception:
            pass


def extrair_match_centre_data(page):
    """Executa o comando JS pedido e retorna o objeto (já como dict/list Python)."""
    return page.evaluate("() => require.config.params['args']['matchCentreData']")


def processar_url(page, url, out_dir):
    match_id = parse_match_id(url)
    out_path = os.path.join(out_dir, f"{match_id}.json")

    if os.path.exists(out_path):
        print(f"[{match_id}] já salvo, pulando.")
        return

    print(f"[{match_id}] abrindo {url}")
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_timeout(3000)
    aceitar_cookies(page)
    page.wait_for_timeout(1500)

    try:
        data = extrair_match_centre_data(page)
    except Exception as e:
        print(f"[{match_id}] ERRO ao extrair matchCentreData: {e}")
        return

    if data is None:
        print(f"[{match_id}] matchCentreData veio vazio/None "
              f"(a partida pode não ter dados de match centre disponíveis).")
        return

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"[{match_id}] salvo em {out_path}")


def carregar_urls_do_csv(path):
    urls = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            urls.append(row["url"])
    return urls


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", help="URL de uma partida específica do WhoScored")
    parser.add_argument(
        "--csv", default=INPUT_CSV,
        help="CSV com links de partidas (gerado pelo whoscored_scraper.py)",
    )
    parser.add_argument("--out", default=OUTPUT_DIR, help="Pasta de saída para os JSONs")
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)

    if args.url:
        urls = [args.url]
    else:
        if not os.path.exists(args.csv):
            print(f"Não encontrei {args.csv} e nenhum --url foi passado. "
                  f"Informe --url ou gere o CSV primeiro com whoscored_scraper.py.")
            return
        urls = carregar_urls_do_csv(args.csv)
        print(f"{len(urls)} partidas carregadas de {args.csv}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            )
        )

        for url in urls:
            processar_url(page, url, args.out)
            time.sleep(1.5)  # intervalo entre requisições

        browser.close()

    print("Concluído.")


if __name__ == "__main__":
    main()