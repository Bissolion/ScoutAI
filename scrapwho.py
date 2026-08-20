"""
Pipeline completo:
1. Coleta os links de todas as partidas já finalizadas do Brasileirão
   (navegando mês a mês na página de fixtures) e salva em CSV.
2. Abre cada partida — em paralelo, com N navegadores — e extrai o
   matchCentreData via JS, salvando um .json por partida.

Por que a etapa 1 é sequencial e só a etapa 2 é paralela?
- A etapa 1 depende de clicar "mês anterior" e ler o estado resultante
  a cada passo — é sequencial por natureza e é rápida (10-14 páginas).
- A etapa 2 é o gargalo real (uma página por partida, 300+ na
  temporada) — é aí que a concorrência realmente compensa.
- Concorrência alta demais aumenta o risco de acionar o
  Cloudflare/anti-bot do site. Um pool pequeno (3-5 workers) com
  intervalo aleatório entre requisições é o equilíbrio entre
  velocidade e não tomar bloqueio.

Requisitos:
    pip install playwright
    playwright install chromium

Uso:
    python whoscored_pipeline.py
    python whoscored_pipeline.py --workers 5
    python whoscored_pipeline.py --skip-links   # usa o CSV já existente,
                                                  # pula direto pra etapa 2
"""

import argparse
import csv
import json
import os
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

from playwright.sync_api import sync_playwright

FIXTURES_URL = (
    "https://www.whoscored.com/regions/31/tournaments/95/seasons/"
    "10621/stages/24121/fixtures/brazil-brasileirão-2025"
)
OUTPUT_CSV = "brasileirao_2025_partidas.csv"
OUTPUT_DIR = "match_data"
MAX_MONTHS_BACK = 14

print_lock = Lock()


def log(msg):
    with print_lock:
        print(msg)


def novo_user_agent():
    return (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )


def aceitar_cookies(page):
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


def parse_match_id(url):
    m = re.search(r"/matches/(\d+)/", url)
    return m.group(1) if m else "unknown"


# ---------------------------------------------------------------------
# ETAPA 1 — coleta de links (sequencial, por natureza)
# ---------------------------------------------------------------------

def extrair_partidas_finalizadas(page):
    links = page.eval_on_selector_all(
        "a[href*='/matches/'][href*='/live/']",
        "els => els.map(e => e.href)",
    )
    vistos, unicos = set(), []
    for l in links:
        if l not in vistos:
            vistos.add(l)
            unicos.append(l)
    return unicos


def pegar_label_mes(page):
    try:
        loc = page.locator("text=/[A-Za-z]{3} \\d{4}/").first
        return loc.inner_text(timeout=3000).strip()
    except Exception:
        return None


def coletar_links():
    todas_partidas = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page(user_agent=novo_user_agent())

        page.goto(FIXTURES_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        aceitar_cookies(page)
        page.wait_for_timeout(1000)

        for i in range(MAX_MONTHS_BACK):
            page.wait_for_timeout(1500)
            mes_atual = pegar_label_mes(page)
            log(f"[links][{i}] mês exibido: {mes_atual}")

            partidas = extrair_partidas_finalizadas(page)
            log(f"[links][{i}] {len(partidas)} partidas finalizadas encontradas")
            for link in partidas:
                todas_partidas[link] = True

            try:
                prev_btn = page.locator("img[alt='chevron-left']").first
                prev_btn.click(timeout=5000)
            except Exception:
                log("[links] não deu para clicar em 'mês anterior' — fim do histórico.")
                break

            page.wait_for_timeout(800)
            novo_mes = pegar_label_mes(page)
            if novo_mes == mes_atual:
                log("[links] mês não mudou — limite do calendário atingido.")
                break

        browser.close()

    urls = list(todas_partidas.keys())
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["match_id", "url"])
        for url in urls:
            writer.writerow([parse_match_id(url), url])

    log(f"[links] total: {len(urls)} — salvo em {OUTPUT_CSV}")
    return urls


def carregar_urls_do_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return [row["url"] for row in csv.DictReader(f)]


# ---------------------------------------------------------------------
# ETAPA 2 — extração de matchCentreData (paralela, N workers)
# ---------------------------------------------------------------------

def processar_url(page, url, out_dir, tentativas=3):
    match_id = parse_match_id(url)
    out_path = os.path.join(out_dir, f"{match_id}.json")

    if os.path.exists(out_path):
        log(f"[{match_id}] já salvo, pulando.")
        return

    for tentativa in range(1, tentativas + 1):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2500)
            aceitar_cookies(page)
            page.wait_for_timeout(1000)

            data = page.evaluate("() => require.config.params['args']['matchCentreData']")

            if data is None:
                log(f"[{match_id}] matchCentreData vazio (sem dados de match centre).")
                return

            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            log(f"[{match_id}] salvo em {out_path}")
            return

        except Exception as e:
            log(f"[{match_id}] tentativa {tentativa}/{tentativas} falhou: {e}")
            time.sleep(2 * tentativa)  # backoff

    log(f"[{match_id}] desisti após {tentativas} tentativas.")


def worker(urls_chunk, out_dir, worker_id):
    log(f"[worker {worker_id}] iniciando com {len(urls_chunk)} partidas")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page(user_agent=novo_user_agent())

        for url in urls_chunk:
            processar_url(page, url, out_dir)
            time.sleep(random.uniform(1.0, 2.5))  # jitter entre requisições

        browser.close()
    log(f"[worker {worker_id}] finalizado")


def processar_em_paralelo(urls, out_dir, max_workers):
    os.makedirs(out_dir, exist_ok=True)

    # divide as URLs em N pedaços, um por worker (evita fila compartilhada
    # entre múltiplas instâncias síncronas do Playwright)
    chunks = [urls[i::max_workers] for i in range(max_workers)]

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(worker, chunk, out_dir, i)
            for i, chunk in enumerate(chunks)
            if chunk
        ]
        for f in as_completed(futures):
            f.result()  # propaga exceções, se houver


# ---------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=4,
                         help="Nº de navegadores em paralelo na etapa 2 (padrão: 4)")
    parser.add_argument("--skip-links", action="store_true",
                         help="Pula a etapa 1 e usa o CSV já existente")
    parser.add_argument("--out", default=OUTPUT_DIR, help="Pasta de saída para os JSONs")
    args = parser.parse_args()

    if args.skip_links:
        if not os.path.exists(OUTPUT_CSV):
            log(f"{OUTPUT_CSV} não existe. Rode sem --skip-links primeiro.")
            return
        urls = carregar_urls_do_csv(OUTPUT_CSV)
        log(f"[links] {len(urls)} carregadas de {OUTPUT_CSV}")
    else:
        urls = coletar_links()

    processar_em_paralelo(urls, args.out, args.workers)
    log("Pipeline concluído.")


if __name__ == "__main__":
    main()