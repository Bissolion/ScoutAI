"""Exportação da partida em JSON, CSV e Excel."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime
from typing import Any

import pandas as pd


def _json_default(valor: Any) -> Any:
    if hasattr(valor, "isoformat"):
        return valor.isoformat()
    if pd.isna(valor):
        return None
    return str(valor)


def nome_arquivo(resumo: dict[str, Any], extensao: str) -> str:
    casa = (resumo.get("casa") or "casa").replace(" ", "_")
    fora = (resumo.get("fora") or "fora").replace(" ", "_")
    match_id = resumo.get("match_id") or "partida"
    return f"{match_id}_{casa}_x_{fora}.{extensao}"


def exportar_json(pacote: dict[str, Any]) -> bytes:
    bruto = pacote.get("bruto") or {}
    centro = bruto.get("matchCentreData") or bruto.get("resumoBruto")
    corpo = {
        "gerado_em": datetime.now().isoformat(timespec="seconds"),
        "nivel": pacote.get("nivel", "completo"),
        "resumo": pacote["resumo"],
        "comparativo": pacote["comparativo"].to_dict(orient="records"),
        "jogadores": pacote["jogadores"].to_dict(orient="records"),
        "escalacao": pacote["escalacao"].to_dict(orient="records"),
        "incidentes": pacote["incidentes"].to_dict(orient="records"),
        "chutes": pacote["chutes"].to_dict(orient="records"),
        "eventos": pacote["eventos"].to_dict(orient="records"),
        "dados_originais": centro,
    }
    return json.dumps(corpo, ensure_ascii=False, indent=2, default=_json_default).encode("utf-8")


def exportar_excel(pacote: dict[str, Any]) -> bytes:
    buffer = io.BytesIO()
    abas = {
        "Resumo": pd.DataFrame([pacote["resumo"]]),
        "Comparativo": pacote["comparativo"],
        "Jogadores": pacote["jogadores"],
        "Escalacao": pacote["escalacao"],
        "Incidentes": pacote["incidentes"],
        "Chutes": pacote["chutes"],
        "Eventos": pacote["eventos"],
    }
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for nome, tabela in abas.items():
            df = tabela.copy()
            if "qualifiers" in df.columns:
                df["qualifiers"] = df["qualifiers"].apply(
                    lambda v: json.dumps(v, ensure_ascii=False) if isinstance(v, dict) else v
                )
            df.to_excel(writer, sheet_name=nome[:31], index=False)
    return buffer.getvalue()


def exportar_csv_zip(pacote: dict[str, Any]) -> bytes:
    buffer = io.BytesIO()
    abas = {
        "resumo": pd.DataFrame([pacote["resumo"]]),
        "comparativo": pacote["comparativo"],
        "jogadores": pacote["jogadores"],
        "escalacao": pacote["escalacao"],
        "incidentes": pacote["incidentes"],
        "chutes": pacote["chutes"],
        "eventos": pacote["eventos"],
    }
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as arquivo:
        for nome, tabela in abas.items():
            df = tabela.copy()
            if "qualifiers" in df.columns:
                df["qualifiers"] = df["qualifiers"].apply(
                    lambda v: json.dumps(v, ensure_ascii=False) if isinstance(v, dict) else v
                )
            arquivo.writestr(f"{nome}.csv", df.to_csv(index=False).encode("utf-8-sig"))
    return buffer.getvalue()
