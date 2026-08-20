"""Cliente HTTP para os dados públicos do WhoScored."""

from __future__ import annotations

import hashlib
import json
import re
import time
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.whoscored.com"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/json,application/xhtml+xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": f"{BASE_URL}/",
}

STATUS_PARTIDA = {
    1: "Agendada",
    2: "1º tempo",
    3: "Intervalo",
    4: "2º tempo",
    5: "Prorrogação",
    6: "Encerrada",
    7: "Pênaltis",
    8: "Adiada",
    9: "Cancelada",
}


def _extrair_bloco(texto: str, chave: str) -> str | None:
    padroes = [
        rf"""['"]{re.escape(chave)}['"]\s*[:=]\s*""",
        rf"""\b{re.escape(chave)}\s*[:=]\s*""",
    ]
    inicio = -1
    for padrao in padroes:
        achado = re.search(padrao, texto)
        if achado:
            inicio = achado.end()
            break
    if inicio < 0:
        return None
    while inicio < len(texto) and texto[inicio] in " \t\r\n":
        inicio += 1
    if inicio >= len(texto) or texto[inicio] not in "{[":
        return None

    profundidade = 0
    em_string: str | None = None
    escapado = False
    for indice in range(inicio, len(texto)):
        caractere = texto[indice]
        if em_string:
            if escapado:
                escapado = False
            elif caractere == "\\":
                escapado = True
            elif caractere == em_string:
                em_string = None
            continue
        if caractere in "'\"":
            em_string = caractere
            continue
        if caractere in "{[":
            profundidade += 1
        elif caractere in "}]":
            profundidade -= 1
            if profundidade == 0:
                return texto[inicio : indice + 1]
    return None


def _js_para_python(bruto: str) -> Any:
    if not bruto:
        raise ValueError("Bloco JS vazio")
    try:
        return json.loads(bruto)
    except json.JSONDecodeError:
        pass

    convertido = re.sub(
        r"\(new Date\((\d+),\s*(\d+),\s*(\d+)\)\)\.toString\(\)",
        lambda m: f'"{int(m.group(1))}-{int(m.group(2)) + 1:02d}-{int(m.group(3)):02d}"',
        bruto,
    )

    def _trocar_aspas(match: re.Match[str]) -> str:
        interior = match.group(1).replace("\\'", "'").replace('"', '\\"')
        return f'"{interior}"'

    convertido = re.sub(r"'([^'\\]*(?:\\.[^'\\]*)*)'", _trocar_aspas, convertido)
    convertido = re.sub(r"([{\[,]\s*)([A-Za-z_][\w]*)\s*:", r'\1"\2":', convertido)
    convertido = re.sub(r"\bundefined\b", "null", convertido)
    convertido = re.sub(r"\bNaN\b", "null", convertido)
    while re.search(r",\s*,", convertido):
        convertido = re.sub(r",\s*,", ",null,", convertido)
    convertido = re.sub(r",\s*([}\]])", r"\1", convertido)
    return json.loads(convertido)


def _ler_js(html: str, chave: str) -> Any | None:
    bloco = _extrair_bloco(html, chave)
    if not bloco:
        return None
    try:
        return _js_para_python(bloco)
    except (json.JSONDecodeError, ValueError):
        return None


def extrair_id_partida(texto: str) -> int | None:
    if not texto:
        return None
    texto = texto.strip()
    if texto.isdigit():
        return int(texto)
    achado = re.search(r"/matches/(\d+)", texto, flags=re.I)
    if achado:
        return int(achado.group(1))
    return None


def _slug_de_url(url: str) -> str:
    partes = [p for p in url.strip("/").split("/") if p]
    return partes[-1] if partes else ""


class WhoScoredClient:
    def __init__(self, cache_dir: Path | None = None, intervalo: float = 0.45) -> None:
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.intervalo = intervalo
        self._ultima_chamada = 0.0
        raiz = Path(__file__).resolve().parent
        self.cache_dir = cache_dir or (raiz / "cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _aguardar(self) -> None:
        decorrido = time.time() - self._ultima_chamada
        if decorrido < self.intervalo:
            time.sleep(self.intervalo - decorrido)

    def _caminho_cache(self, url: str) -> Path:
        nome = hashlib.sha1(url.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{nome}.txt"

    def obter(
        self,
        caminho: str,
        *,
        usar_cache: bool = False,
        params: dict[str, Any] | None = None,
        timeout: int = 35,
    ) -> tuple[int, str, str]:
        url = caminho if caminho.startswith("http") else urljoin(BASE_URL + "/", caminho.lstrip("/"))
        if params:
            consulta = "&".join(f"{k}={quote(str(v))}" for k, v in params.items())
            url = f"{url}{'&' if '?' in url else '?'}{consulta}"

        arquivo = self._caminho_cache(url)
        if usar_cache and arquivo.exists():
            return 200, arquivo.read_text(encoding="utf-8", errors="replace"), url

        self._aguardar()
        resposta = self.session.get(url, timeout=timeout)
        self._ultima_chamada = time.time()
        if "charset" not in (resposta.headers.get("content-type") or "").lower():
            resposta.encoding = resposta.apparent_encoding or "utf-8"
        corpo = resposta.text
        if usar_cache and resposta.status_code == 200 and len(corpo) > 200:
            arquivo.write_text(corpo, encoding="utf-8")
        return resposta.status_code, corpo, resposta.url

    def obter_json(self, caminho: str, **kwargs: Any) -> tuple[int, Any, str]:
        status, corpo, url = self.obter(caminho, **kwargs)
        dados: Any = None
        if status == 200:
            try:
                dados = json.loads(corpo)
            except json.JSONDecodeError:
                dados = None
        return status, dados, url

    def validar_conexoes(self) -> list[dict[str, Any]]:
        testes = [
            ("Página inicial", "/", False),
            ("Busca (Search)", "/Search/?t=Vasco", False),
            ("Jogos ao vivo", "/livescores/data", True),
            (
                "Calendário Brasileirão 2025",
                "/tournaments/24121/data/?d=202512",
                True,
            ),
            (
                "Partida Atlético-MG x Vasco",
                "/Matches/1890238/Live",
                False,
            ),
            (
                "Jogos do Vasco",
                "/Teams/1226/Fixtures/Brazil-Vasco-da-Gama",
                False,
            ),
        ]
        resultados: list[dict[str, Any]] = []
        for nome, caminho, esperar_json in testes:
            inicio = time.time()
            detalhe = ""
            ok = False
            status = 0
            try:
                if esperar_json:
                    status, dados, url_final = self.obter_json(caminho)
                    ok = status == 200 and isinstance(dados, dict)
                    if ok:
                        n_jogos = sum(
                            len(t.get("matches") or [])
                            for t in dados.get("tournaments") or []
                        )
                        detalhe = f"{n_jogos} jogos no JSON"
                    else:
                        detalhe = "resposta não é JSON válido"
                else:
                    status, html, url_final = self.obter(caminho)
                    if status != 200:
                        detalhe = f"HTTP {status}"
                    elif nome.startswith("Partida"):
                        centro = _ler_js(html, "matchCentreData")
                        ok = isinstance(centro, dict) and "events" in centro
                        detalhe = (
                            f"{len(centro.get('events', []))} eventos"
                            if ok
                            else "matchCentreData ausente"
                        )
                    elif nome.startswith("Busca"):
                        ok = "search-result" in html
                        detalhe = "resultados HTML" if ok else "sem bloco de busca"
                    elif nome.startswith("Jogos do"):
                        jogos = _ler_js(html, "fixtureMatches")
                        ok = isinstance(jogos, list) and len(jogos) > 0
                        detalhe = f"{len(jogos) if isinstance(jogos, list) else 0} jogos"
                    else:
                        ligas = _ler_js(html, "allRegions")
                        ok = isinstance(ligas, list) and len(ligas) > 0
                        detalhe = f"{len(ligas) if isinstance(ligas, list) else 0} regiões"
            except Exception as exc:  # noqa: BLE001
                url_final = caminho
                detalhe = str(exc)
            resultados.append(
                {
                    "conexao": nome,
                    "ok": ok,
                    "http": status,
                    "detalhe": detalhe,
                    "ms": int((time.time() - inicio) * 1000),
                    "url": url_final if "url_final" in locals() else caminho,
                }
            )
        return resultados

    def listar_ligas(self, usar_cache: bool = True) -> list[dict[str, Any]]:
        status, html, _ = self.obter("/", usar_cache=usar_cache)
        if status != 200:
            raise RuntimeError(f"Falha ao carregar ligas (HTTP {status})")
        regioes = _ler_js(html, "allRegions") or []
        ligas: list[dict[str, Any]] = []
        for regiao in regioes:
            for torneio in regiao.get("tournaments") or []:
                url = unescape(str(torneio.get("url") or ""))
                ids = re.search(r"/regions/(\d+)/tournaments/(\d+)", url, flags=re.I)
                ligas.append(
                    {
                        "regiao_id": int(ids.group(1)) if ids else regiao.get("id"),
                        "regiao": regiao.get("name"),
                        "torneio_id": int(ids.group(2)) if ids else torneio.get("id"),
                        "torneio": torneio.get("name"),
                        "url": url,
                        "rotulo": f"{regiao.get('name')} — {torneio.get('name')}",
                    }
                )
        ligas.sort(key=lambda x: (str(x["regiao"]), str(x["torneio"])))
        return ligas

    def buscar(self, termo: str) -> dict[str, list[dict[str, Any]]]:
        termo = termo.strip()
        if not termo:
            return {"times": [], "jogadores": [], "torneios": []}
        status, html, _ = self.obter(f"/Search/?t={quote(termo)}")
        if status != 200:
            raise RuntimeError(f"Busca falhou (HTTP {status})")
        soup = BeautifulSoup(html, "html.parser")
        saida: dict[str, list[dict[str, Any]]] = {"times": [], "jogadores": [], "torneios": []}
        mapa = {"Teams:": "times", "Players:": "jogadores", "Tournaments:": "torneios"}
        bloco = soup.select_one("div.search-result")
        if not bloco:
            return saida
        secao_atual = None
        for filho in bloco.children:
            nome_tag = getattr(filho, "name", None)
            if nome_tag == "h2":
                secao_atual = mapa.get(filho.get_text(strip=True))
                continue
            if nome_tag != "table" or not secao_atual:
                continue
            for link in filho.select("a[href]"):
                href = link.get("href") or ""
                nome = link.get_text(strip=True)
                item: dict[str, Any] = {"nome": nome, "url": href}
                time_id = re.search(r"/teams/(\d+)/", href, flags=re.I)
                jog_id = re.search(r"/players/(\d+)/", href, flags=re.I)
                tor_id = re.search(r"/tournaments/(\d+)", href, flags=re.I)
                if time_id:
                    item["id"] = int(time_id.group(1))
                    item["slug"] = _slug_de_url(href)
                if jog_id:
                    item["id"] = int(jog_id.group(1))
                if tor_id:
                    item["id"] = int(tor_id.group(1))
                ids_reg = re.search(r"/regions/(\d+)/tournaments/(\d+)", href, flags=re.I)
                if ids_reg:
                    item["regiao_id"] = int(ids_reg.group(1))
                    item["torneio_id"] = int(ids_reg.group(2))
                saida[secao_atual].append(item)
        return saida

    def listar_temporadas(self, regiao_id: int, torneio_id: int) -> list[dict[str, Any]]:
        caminho = f"/Regions/{regiao_id}/Tournaments/{torneio_id}"
        status, html, _ = self.obter(caminho, usar_cache=True)
        if status != 200:
            raise RuntimeError(f"Falha ao listar temporadas (HTTP {status})")
        soup = BeautifulSoup(html, "html.parser")
        seletor = soup.select_one("select[id*='seasons']")
        temporadas: list[dict[str, Any]] = []
        if seletor:
            for opcao in seletor.select("option"):
                valor = unescape(opcao.get("value") or "")
                temporada_id = re.search(r"/Seasons/(\d+)", valor, flags=re.I)
                if not temporada_id:
                    continue
                temporadas.append(
                    {
                        "temporada": opcao.get_text(strip=True),
                        "temporada_id": int(temporada_id.group(1)),
                        "url": valor,
                    }
                )
        stage_id, meses = self._calendario_da_pagina(html)
        if temporadas:
            temporadas[0]["stage_id"] = stage_id
            temporadas[0]["meses"] = meses
        return temporadas

    def _calendario_da_pagina(self, html: str) -> tuple[int | None, list[str]]:
        stage_id = None
        achado = re.search(r"/stages/(\d+)/", html, flags=re.I)
        if achado:
            stage_id = int(achado.group(1))
        calendario = _extrair_bloco(html, "wsCalendar") or ""
        meses: list[str] = []
        datas = re.findall(r"new Date\((\d+),\s*(\d+)", calendario)
        if len(datas) >= 2:
            ano, mes = int(datas[0][0]), int(datas[0][1])
            ano_fim, mes_fim = int(datas[1][0]), int(datas[1][1])
            while (ano, mes) <= (ano_fim, mes_fim):
                meses.append(f"{ano}{mes + 1:02d}")
                mes += 1
                if mes > 11:
                    mes = 0
                    ano += 1
        if not meses:
            for ano_txt, bloco_meses in re.findall(r"(\d{4})\s*:\s*\{(.+?)\}(?=\s*,\s*\d{4}\s*:|\s*\})", calendario, flags=re.S):
                for mes_js in re.findall(r"(\d{1,2})\s*:\s*\{", bloco_meses):
                    meses.append(f"{ano_txt}{int(mes_js) + 1:02d}")
        meses = sorted(set(meses))
        return stage_id, meses

    def resolver_stage(
        self, regiao_id: int, torneio_id: int, temporada_id: int
    ) -> tuple[int | None, list[str]]:
        caminho = f"/Regions/{regiao_id}/Tournaments/{torneio_id}/Seasons/{temporada_id}"
        status, html, _ = self.obter(caminho, usar_cache=True)
        if status != 200:
            raise RuntimeError(f"Falha ao abrir temporada (HTTP {status})")
        stage_id, meses = self._calendario_da_pagina(html)
        if not stage_id:
            soup = BeautifulSoup(html, "html.parser")
            for link in soup.select("a[href*='/stages/']"):
                achado = re.search(r"/stages/(\d+)/", link.get("href") or "", flags=re.I)
                if achado:
                    stage_id = int(achado.group(1))
                    break
        return stage_id, meses

    def listar_jogos_mes(self, stage_id: int, ano_mes: str) -> list[dict[str, Any]]:
        status, dados, _ = self.obter_json(
            f"/tournaments/{stage_id}/data/",
            params={"d": ano_mes},
        )
        if status != 200 or not isinstance(dados, dict):
            return []
        jogos: list[dict[str, Any]] = []
        for torneio in dados.get("tournaments") or []:
            for bruto in torneio.get("matches") or []:
                jogos.append(self._normalizar_jogo_feed(bruto, torneio))
        return jogos

    def listar_jogos_liga(
        self,
        regiao_id: int,
        torneio_id: int,
        temporada_id: int,
        progresso=None,
    ) -> list[dict[str, Any]]:
        stage_id, meses = self.resolver_stage(regiao_id, torneio_id, temporada_id)
        if not stage_id:
            raise RuntimeError("Não foi possível identificar o stage da temporada")
        if not meses:
            from datetime import datetime, timezone

            agora = datetime.now(timezone.utc)
            ano, mes = agora.year, agora.month
            gerados: list[str] = []
            for _ in range(18):
                gerados.append(f"{ano}{mes:02d}")
                mes -= 1
                if mes == 0:
                    mes = 12
                    ano -= 1
            meses = list(reversed(gerados))
        vistos: set[int] = set()
        jogos: list[dict[str, Any]] = []
        total = max(len(meses), 1)
        for indice, ano_mes in enumerate(meses, start=1):
            if progresso is not None:
                progresso.progress(indice / total, text=f"Mês {ano_mes} ({indice}/{total})")
            for jogo in self.listar_jogos_mes(stage_id, ano_mes):
                if jogo["id"] in vistos:
                    continue
                vistos.add(jogo["id"])
                jogos.append(jogo)
        jogos.sort(key=lambda j: j.get("inicio") or "")
        return jogos

    def listar_livescores(self, data: str | None = None) -> list[dict[str, Any]]:
        params = {"d": data} if data else None
        status, dados, _ = self.obter_json("/livescores/data", params=params)
        if status != 200 or not isinstance(dados, dict):
            raise RuntimeError(f"Falha ao carregar jogos do dia (HTTP {status})")
        jogos: list[dict[str, Any]] = []
        for torneio in dados.get("tournaments") or []:
            for bruto in torneio.get("matches") or []:
                jogos.append(self._normalizar_jogo_feed(bruto, torneio))
        jogos.sort(key=lambda j: j.get("inicio") or "")
        return jogos

    def listar_jogos_time(self, time_id: int, slug: str | None = None) -> list[dict[str, Any]]:
        slug = slug or str(time_id)
        candidatos = [
            f"/Teams/{time_id}/Fixtures/{slug}",
            f"/Teams/{time_id}/Show/{slug}",
            f"/Teams/{time_id}/Fixtures",
        ]
        bruto = None
        for caminho in candidatos:
            status, html, _ = self.obter(caminho)
            if status != 200:
                continue
            bruto = _ler_js(html, "fixtureMatches")
            if bruto:
                break
        if not isinstance(bruto, list):
            return []
        if bruto and isinstance(bruto[0], int) and len(bruto) > 1 and isinstance(bruto[1], list):
            bruto = bruto[1]
        jogos: list[dict[str, Any]] = []
        for linha in bruto:
            if isinstance(linha, list) and linha and isinstance(linha[0], list):
                for item in linha:
                    normalizado = self._normalizar_jogo_array(item)
                    if normalizado:
                        jogos.append(normalizado)
            else:
                normalizado = self._normalizar_jogo_array(linha)
                if normalizado:
                    jogos.append(normalizado)
        return jogos

    def obter_partida(self, match_id: int) -> dict[str, Any]:
        status, html, url_final = self.obter(f"/Matches/{match_id}/Live")
        if status != 200:
            raise RuntimeError(f"Falha ao abrir a partida {match_id} (HTTP {status})")

        centro = _ler_js(html, "matchCentreData")
        if isinstance(centro, dict) and centro.get("events"):
            return {
                "match_id": match_id,
                "url": url_final,
                "nivel": "completo",
                "matchCentreData": centro,
                "eventTypes": _ler_js(html, "matchCentreEventTypeJson") or {},
                "formacoes": _ler_js(html, "formationIdNameMappings") or {},
            }

        basico = _ler_js(html, "initialMatchDataForScrappers")
        if isinstance(basico, list) and basico and isinstance(basico[0], list):
            return {
                "match_id": match_id,
                "url": url_final,
                "nivel": "basico",
                "resumoBruto": basico[0],
            }

        raise RuntimeError(
            "O WhoScored ainda não publicou dados desta partida — normalmente porque "
            "o jogo não foi disputado. Escolha um jogo já encerrado."
        )

    @staticmethod
    def _normalizar_jogo_feed(bruto: dict[str, Any], torneio: dict[str, Any]) -> dict[str, Any]:
        home_score = bruto.get("homeScore")
        away_score = bruto.get("awayScore")
        if isinstance(home_score, dict):
            home_score = home_score.get("home") or home_score.get("value")
        if isinstance(away_score, dict):
            away_score = away_score.get("away") or away_score.get("value")
        match_id = int(bruto["id"])
        casa = bruto.get("homeTeamName") or ""
        fora = bruto.get("awayTeamName") or ""
        return {
            "id": match_id,
            "inicio": bruto.get("startTimeUtc") or bruto.get("startTime"),
            "status_codigo": bruto.get("status"),
            "status": STATUS_PARTIDA.get(bruto.get("status"), bruto.get("elapsed") or str(bruto.get("status"))),
            "elapsed": bruto.get("elapsed"),
            "casa": casa,
            "fora": fora,
            "casa_id": bruto.get("homeTeamId"),
            "fora_id": bruto.get("awayTeamId"),
            "gols_casa": home_score,
            "gols_fora": away_score,
            "competicao": torneio.get("tournamentName"),
            "temporada": torneio.get("seasonName"),
            "regiao": torneio.get("regionName"),
            "stage_id": torneio.get("stageId") or bruto.get("stageId"),
            "opta": bool(bruto.get("matchIsOpta")),
            "encerrado": bruto.get("status") in (6, 7),
            "url": f"{BASE_URL}/Matches/{match_id}/Live",
            "rotulo": f"{casa} x {fora}",
        }

    @staticmethod
    def _normalizar_jogo_array(linha: Any) -> dict[str, Any] | None:
        if not isinstance(linha, list) or len(linha) < 16:
            return None
        match_id = int(linha[0])
        casa = str(linha[5])
        fora = str(linha[8])
        placar = str(linha[10]) if len(linha) > 10 else ""
        gols_casa = gols_fora = None
        partes = re.findall(r"\d+", placar)
        if len(partes) >= 2:
            gols_casa, gols_fora = int(partes[0]), int(partes[1])
        data = str(linha[2])
        hora = str(linha[3]) if len(linha) > 3 else ""
        inicio = None
        if re.match(r"\d{2}-\d{2}-\d{2}", data):
            dia, mes, ano = data.split("-")
            ano_full = f"20{ano}" if len(ano) == 2 else ano
            inicio = f"{ano_full}-{mes}-{dia}T{hora or '00:00'}"
        return {
            "id": match_id,
            "inicio": inicio,
            "status_codigo": None,
            "status": str(linha[14]) if len(linha) > 14 else "",
            "elapsed": str(linha[14]) if len(linha) > 14 else "",
            "casa": casa,
            "fora": fora,
            "casa_id": linha[4],
            "fora_id": linha[7],
            "gols_casa": gols_casa,
            "gols_fora": gols_fora,
            "competicao": str(linha[16]) if len(linha) > 16 else "",
            "temporada": str(linha[15]) if len(linha) > 15 else "",
            "regiao": str(linha[29]) if len(linha) > 29 else "",
            "stage_id": linha[21] if len(linha) > 21 else None,
            "opta": bool(linha[26]) if len(linha) > 26 else False,
            "encerrado": str(linha[14]).upper() in {"FT", "AET", "PEN"} if len(linha) > 14 else False,
            "url": f"{BASE_URL}/Matches/{match_id}/Live",
            "rotulo": f"{casa} x {fora}",
        }


if __name__ == "__main__":
    cliente = WhoScoredClient()
    print("Validando conexões WhoScored...")
    for item in cliente.validar_conexoes():
        marca = "OK" if item["ok"] else "FALHA"
        print(f"[{marca}] {item['conexao']}  HTTP {item['http']}  {item['ms']}ms  {item['detalhe']}")
