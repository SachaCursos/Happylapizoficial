"""
Fetch Meta Ads data directly from Marketing API.
Account: act_449499746278703 (Cuenta publicitaria happylapiz)
"""

import os
import requests
import pandas as pd
from datetime import date

META_ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN", "")
META_AD_ACCOUNT_ID = os.getenv("META_AD_ACCOUNT_ID", "act_449499746278703")
GRAPH_URL = "https://graph.facebook.com/v20.0"


def _get(endpoint: str, params: dict) -> dict:
    params["access_token"] = META_ACCESS_TOKEN
    resp = requests.get(f"{GRAPH_URL}/{endpoint}", params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_meta_ads(anio: int, mes: int) -> dict:
    """
    Fetch campaigns and daily spend from Meta Marketing API for given month.
    Returns dict compatible with parse_meta_json output.
    """
    if not META_ACCESS_TOKEN:
        return _empty()

    date_from = f"{anio}-{mes:02d}-01"
    last_day = (date(anio, mes % 12 + 1, 1) if mes < 12 else date(anio + 1, 1, 1))
    from datetime import timedelta
    date_to = (last_day - timedelta(days=1)).strftime("%Y-%m-%d")

    time_range = f"{{'since':'{date_from}','until':'{date_to}'}}"

    try:
        # Daily spend
        daily_data = _get(f"{META_AD_ACCOUNT_ID}/insights", {
            "fields": "date_start,spend,impressions,clicks,ctr,cpm,cpc",
            "time_increment": "1",
            "time_range": time_range,
            "limit": 90,
        })
        rows_daily = daily_data.get("data", [])
        df_daily = pd.DataFrame([{
            "dia": r["date_start"],
            "gasto_clp": float(r.get("spend", 0)),
            "impresiones": int(r.get("impressions", 0)),
            "clics": int(r.get("clicks", 0)),
            "ctr": float(r.get("ctr", 0)),
            "cpm": float(r.get("cpm", 0)),
            "cpc": float(r.get("cpc", 0)),
        } for r in rows_daily])
        if not df_daily.empty:
            df_daily["dia"] = pd.to_datetime(df_daily["dia"])

        # Campaigns
        camp_data = _get(f"{META_AD_ACCOUNT_ID}/insights", {
            "fields": "campaign_id,campaign_name,spend,impressions,clicks,actions,action_values",
            "time_range": time_range,
            "level": "campaign",
            "limit": 100,
        })
        rows_camp = camp_data.get("data", [])
        campanas = []
        for r in rows_camp:
            gasto = float(r.get("spend", 0))
            actions = {a["action_type"]: float(a["value"]) for a in r.get("actions", [])}
            action_vals = {a["action_type"]: float(a["value"]) for a in r.get("action_values", [])}
            compras = actions.get("purchase", 0)
            ingresos = action_vals.get("purchase", 0)
            roas = ingresos / gasto if gasto > 0 else 0
            campanas.append({
                "id": r.get("campaign_id", ""),
                "nombre": r.get("campaign_name", ""),
                "gasto_clp": gasto,
                "impresiones": int(r.get("impressions", 0)),
                "clics": int(r.get("clicks", 0)),
                "compras": compras,
                "ingresos_clp": ingresos,
                "roas": round(roas, 2),
            })
        df_campanas = pd.DataFrame(campanas) if campanas else pd.DataFrame(
            columns=["id", "nombre", "gasto_clp", "impresiones", "clics", "compras", "ingresos_clp", "roas"]
        )

        return {
            "meta": {"cuenta": META_AD_ACCOUNT_ID, "periodo": f"{date_from}/{date_to}"},
            "gasto_diario": df_daily,
            "campanas": df_campanas,
            "adsets": pd.DataFrame(),
        }

    except Exception as e:
        print(f"[meta_api] Error: {e}")
        return _empty()


def get_meta_gasto_mes(anio: int, mes: int) -> float:
    """Total spend for the month in CLP."""
    data = fetch_meta_ads(anio, mes)
    df = data.get("gasto_diario", pd.DataFrame())
    if df.empty:
        return 0.0
    return float(df["gasto_clp"].sum())


def _empty() -> dict:
    return {
        "meta": {},
        "gasto_diario": pd.DataFrame(columns=["dia", "gasto_clp", "impresiones", "clics", "ctr", "cpm", "cpc"]),
        "campanas": pd.DataFrame(columns=["id", "nombre", "gasto_clp", "impresiones", "clics", "compras", "ingresos_clp", "roas"]),
        "adsets": pd.DataFrame(),
    }
