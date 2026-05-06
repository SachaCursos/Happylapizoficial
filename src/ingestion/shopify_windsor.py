"""
Fetch Shopify sales data from Windsor.ai (2026+).
Values are CLP BRUTO with IVA → divide by 1.19 for neto.
CRITICAL: always deduplicate by order_id before summing revenue.
"""

import os
import requests
import pandas as pd
from datetime import datetime


WINDSOR_API_URL = "https://connectors.windsor.ai/shopify"
WINDSOR_API_KEY = os.getenv("WINDSOR_API_KEY", "")
SHOPIFY_ACCOUNT = "happy-lapiz.myshopify.com"


def fetch_shopify_orders(date_from: str, date_to: str) -> pd.DataFrame:
    """
    Fetch orders from Windsor.ai for a date range.
    Returns deduplicated DataFrame with neto revenue.

    Args:
        date_from: 'YYYY-MM-DD'
        date_to:   'YYYY-MM-DD'
    """
    params = {
        "api_key": WINDSOR_API_KEY,
        "connector": "shopify",
        "account": SHOPIFY_ACCOUNT,
        "date_from": date_from,
        "date_to": date_to,
        "fields": "date,order_id,order_total_price",
    }

    resp = requests.get(WINDSOR_API_URL, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if not data:
        return pd.DataFrame(columns=["date", "order_id", "ventas_brutas", "ventas_netas"])

    df = pd.DataFrame(data)
    df.rename(columns={"order_total_price": "ventas_brutas"}, inplace=True)
    df["ventas_brutas"] = pd.to_numeric(df["ventas_brutas"], errors="coerce").fillna(0)

    # Deduplicate by order_id — Windsor returns one row per line item
    df = df.drop_duplicates(subset=["order_id"])

    df["ventas_netas"] = df["ventas_brutas"] / 1.19
    df["date"] = pd.to_datetime(df["date"])

    return df[["date", "order_id", "ventas_brutas", "ventas_netas"]]


def aggregate_by_month(df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate orders to monthly totals."""
    df = df.copy()
    df["mes"] = df["date"].dt.to_period("M")
    return (
        df.groupby("mes")
        .agg(
            pedidos=("order_id", "nunique"),
            ventas_brutas=("ventas_brutas", "sum"),
            ventas_netas=("ventas_netas", "sum"),
        )
        .reset_index()
    )
