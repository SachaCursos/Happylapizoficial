"""
Process Meta Ads JSON uploaded by Sacha from meta_ads_happylapiz.html.
Account ID: 449499746278703
gasto_clp is already in CLP — do NOT apply currency conversion.
"""

import json
import os
from pathlib import Path
from typing import Any

import pandas as pd


UPLOADS_DIR = Path("uploads")


def parse_meta_json(json_path: str | Path) -> dict[str, pd.DataFrame]:
    """
    Parse a Meta Ads JSON file exported by Sacha.

    Expected structure:
      meta{}
      gasto_diario[{dia, gasto_clp, impresiones, clics, ctr, cpm, cpc}]
      campanas[{id, nombre, gasto_clp, impresiones, clics, compras, ingresos_clp, roas}]
      adsets[{id, nombre, campana, producto, gasto_clp, ...}]

    Returns dict with keys: 'meta', 'gasto_diario', 'campanas', 'adsets'
    """
    with open(json_path, "r", encoding="utf-8") as f:
        raw: dict[str, Any] = json.load(f)

    result: dict[str, Any] = {}

    result["meta"] = raw.get("meta", {})

    gasto = raw.get("gasto_diario", [])
    df_gasto = pd.DataFrame(gasto) if gasto else pd.DataFrame(
        columns=["dia", "gasto_clp", "impresiones", "clics", "ctr", "cpm", "cpc"]
    )
    if not df_gasto.empty:
        df_gasto["dia"] = pd.to_datetime(df_gasto["dia"])
        df_gasto["gasto_clp"] = pd.to_numeric(df_gasto["gasto_clp"], errors="coerce").fillna(0)
    result["gasto_diario"] = df_gasto

    campanas = raw.get("campanas", [])
    df_campanas = pd.DataFrame(campanas) if campanas else pd.DataFrame(
        columns=["id", "nombre", "gasto_clp", "impresiones", "clics", "compras", "ingresos_clp", "roas"]
    )
    if not df_campanas.empty:
        for col in ["gasto_clp", "impresiones", "clics", "compras", "ingresos_clp", "roas"]:
            if col in df_campanas.columns:
                df_campanas[col] = pd.to_numeric(df_campanas[col], errors="coerce").fillna(0)
    result["campanas"] = df_campanas

    adsets = raw.get("adsets", [])
    df_adsets = pd.DataFrame(adsets) if adsets else pd.DataFrame(
        columns=["id", "nombre", "campana", "producto", "gasto_clp"]
    )
    if not df_adsets.empty:
        if "gasto_clp" in df_adsets.columns:
            df_adsets["gasto_clp"] = pd.to_numeric(df_adsets["gasto_clp"], errors="coerce").fillna(0)
    result["adsets"] = df_adsets

    return result


def match_campana_producto(nombre_campana: str, keywords_df: pd.DataFrame) -> str:
    """
    Match a campaign name to a product using marketing_keywords table.
    Supports both ';' and ',' as keyword separators.
    Returns product name or 'Otros'.
    """
    nombre_lower = nombre_campana.lower()
    for _, row in keywords_df.sort_values("prioridad", ascending=False).iterrows():
        raw = str(row["palabras"])
        # support both semicolon and comma separators
        palabras = [p for sep in (";", ",") for p in raw.split(sep)]
        for palabra in palabras:
            kw = palabra.strip().lower()
            if kw and kw in nombre_lower:
                return row["producto"]
    return "Otros"


def get_latest_meta_json() -> Path | None:
    """Return the most recently uploaded Meta Ads JSON file."""
    jsons = sorted(UPLOADS_DIR.glob("meta_ads_*.json"), reverse=True)
    return jsons[0] if jsons else None
