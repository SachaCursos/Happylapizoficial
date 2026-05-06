"""
MercadoLibre P&L calculator.

Key rules:
- ventas_netas_ml = SUM(total_de_la_venta WHERE detalle LIKE '%Cargo por venta%') / 1.19
- FULL = numero_de_paquete filled; FLEX = numero_de_paquete empty
- Ecourrier Flex: 3200 * COUNT(pedidos FLEX)
- Product Ads alert if > 15% ventas_netas_ml
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from src.calc.cogs import get_cogs_neto

ECOURRIER_FLEX_TARIFA = 3200
FULFILLMENT_FLEX_TARIFA = 1000


def calcular_pnl_ml(engine: Engine, anio: int, mes: int) -> dict:
    """
    Calculate ML P&L for a given month.
    """
    query = text("""
        SELECT *
        FROM ml_facturacion
        WHERE anio = :anio AND mes = :mes
    """)
    df = pd.read_sql(query, engine, params={"anio": anio, "mes": mes})

    if df.empty:
        return _empty_pnl(anio, mes)

    # Ventas: rows with 'Cargo por venta' or 'Anulacion del cargo por venta'
    mask_ventas = df["detalle"].str.contains("Cargo por venta|Anulacion del cargo por venta", na=False, case=False)
    df_ventas = df[mask_ventas]
    ventas_brutas = float(df_ventas["total_de_la_venta"].sum())
    ventas_netas = ventas_brutas / 1.19

    # Comision ML
    comision_ml = float(df_ventas["valor_del_cargo"].sum())

    # Envios ML
    mask_envios = df["detalle"].str.contains("envio|envíos|flete", na=False, case=False)
    envios_ml = float(df[mask_envios]["valor_del_cargo"].sum())

    # Product Ads
    mask_ads = df["detalle"].str.contains("campana de publicidad|product ads", na=False, case=False)
    product_ads = float(df[mask_ads]["valor_del_cargo"].sum())

    # Storage Full
    query_full = text("""
        SELECT COALESCE(SUM(ABS(valor_del_cargo::numeric)), 0) as storage
        FROM ml_cargos_full
        WHERE anio = :anio AND mes = :mes
    """)
    try:
        storage_full = float(engine.execute(query_full, {"anio": anio, "mes": mes}).scalar() or 0)
    except Exception:
        storage_full = 0.0

    # Colecta Full
    mask_colecta = df["detalle"].str.contains("colecta", na=False, case=False)
    colecta_full = float(df[mask_colecta]["valor_del_cargo"].sum())

    # Mantenimiento Mi Pagina
    mask_mant = df["detalle"].str.contains("mantenimiento", na=False, case=False)
    mantenimiento = float(df[mask_mant]["valor_del_cargo"].sum())

    # FULL vs FLEX classification
    df_ventas = df_ventas.copy()
    df_ventas["despacho"] = df_ventas["numero_de_paquete"].apply(
        lambda x: "FULL" if str(x).strip() else "FLEX"
    )
    pedidos_flex = int((df_ventas["despacho"] == "FLEX").sum())
    pedidos_full = int((df_ventas["despacho"] == "FULL").sum())

    ecourrier_flex = pedidos_flex * ECOURRIER_FLEX_TARIFA
    fulfillment_flex = pedidos_flex * FULFILLMENT_FLEX_TARIFA

    # COGS (sum product units sold)
    cogs_total = 0.0
    if "cantidad_vendida" in df_ventas.columns and "codigo_ml" in df_ventas.columns:
        for _, row in df_ventas.iterrows():
            sku = str(row.get("codigo_ml", "")).strip()
            qty = float(row.get("cantidad_vendida", 0) or 0)
            cogs_total += get_cogs_neto(sku) * qty

    # Incumplimiento Full (operational alert)
    mask_incump = df["detalle"].str.contains("incumplimiento", na=False, case=False)
    incumplimiento = float(df[mask_incump]["valor_del_cargo"].sum())

    margen_bruto = (
        ventas_netas
        - cogs_total
        - abs(comision_ml)
        - abs(envios_ml)
        - ecourrier_flex
        - product_ads
        - storage_full
        - abs(colecta_full)
        - fulfillment_flex
        - abs(mantenimiento)
    )

    margen_pct = (margen_bruto / ventas_netas * 100) if ventas_netas > 0 else 0
    pct_product_ads = (product_ads / ventas_netas * 100) if ventas_netas > 0 else 0

    return {
        "anio": anio,
        "mes": mes,
        "ventas_brutas": round(ventas_brutas),
        "ventas_netas": round(ventas_netas),
        "pedidos_full": pedidos_full,
        "pedidos_flex": pedidos_flex,
        "cogs": round(cogs_total),
        "comision_ml": round(abs(comision_ml)),
        "envios_ml": round(abs(envios_ml)),
        "ecourrier_flex": round(ecourrier_flex),
        "product_ads": round(product_ads),
        "storage_full": round(storage_full),
        "colecta_full": round(abs(colecta_full)),
        "fulfillment_flex": round(fulfillment_flex),
        "mantenimiento": round(abs(mantenimiento)),
        "incumplimiento_full": round(incumplimiento),
        "margen_bruto": round(margen_bruto),
        "margen_pct": round(margen_pct, 1),
        "pct_product_ads": round(pct_product_ads, 1),
    }


def _empty_pnl(anio: int, mes: int) -> dict:
    keys = [
        "ventas_brutas", "ventas_netas", "pedidos_full", "pedidos_flex",
        "cogs", "comision_ml", "envios_ml", "ecourrier_flex", "product_ads",
        "storage_full", "colecta_full", "fulfillment_flex", "mantenimiento",
        "incumplimiento_full", "margen_bruto", "margen_pct", "pct_product_ads",
    ]
    return {"anio": anio, "mes": mes, **{k: 0 for k in keys}}
