"""
Shopify P&L calculator.

Key rules:
- ventas_netas = ventas_brutas / 1.19
- COGS already neto from cogs.py table
- BluExpress: payment in month N+1 = cost of month N (base devengada)
- Comision MP: 3.4391% * ventas_brutas
- Comision Shopify: 1% * ventas_brutas
- Fulfillment (Pymespace): pedidos * tarifa from costos_fijos.py
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from src.calc.cogs import get_cogs_neto
from src.calc.costos_fijos import TOTAL_CF_MENSUAL, pymespace_costo

COMISION_MP_EFECTIVA = 0.034391   # 2.89% + IVA
COMISION_SHOPIFY = 0.01


def _get_shopify_ventas_historico(engine: Engine, anio: int, mes: int) -> pd.DataFrame:
    query = text("""
        SELECT nombre_del_producto, precio_de_la_variante_de_producto, dia,
               ventas_totales
        FROM shopify_ventas_historico
        WHERE EXTRACT(YEAR FROM dia) = :anio AND EXTRACT(MONTH FROM dia) = :mes
    """)
    return pd.read_sql(query, engine, params={"anio": anio, "mes": mes})


def _get_shopify_ventas_2025(engine: Engine, anio: int, mes: int) -> pd.DataFrame:
    query = text("""
        SELECT titulo_del_producto, precio_de_la_variante_de_producto, dia,
               id_de_pedido, ventas_totales
        FROM shopify_ventas_2025
        WHERE anio = :anio AND EXTRACT(MONTH FROM dia) = :mes
    """)
    return pd.read_sql(query, engine, params={"anio": anio, "mes": mes})


def _get_meta_gasto(engine: Engine, anio: int, mes: int) -> float:
    query = text("""
        SELECT COALESCE(SUM(importe_gastado_clp), 0) as gasto
        FROM meta_ads_diario
        WHERE anio = :anio AND EXTRACT(MONTH FROM dia) = :mes
    """)
    result = engine.execute(query, {"anio": anio, "mes": mes})
    row = result.fetchone()
    return float(row["gasto"]) if row else 0.0


def calcular_pnl_shopify(
    engine: Engine,
    anio: int,
    mes: int,
    blueexpress_neto: float = 0.0,
    meta_gasto_override: float | None = None,
    incluir_fijos: bool = True,
) -> dict:
    """
    Calculate Shopify P&L for a given month.

    Args:
        blueexpress_neto: devengado cost (from NEXT month's payment / 1.19)
        meta_gasto_override: use this value instead of DB if provided (e.g. from JSON)
        incluir_fijos: whether to subtract fixed costs

    Returns:
        dict with all P&L line items
    """
    if anio < 2025:
        df = _get_shopify_ventas_historico(engine, anio, mes)
        ventas_brutas = float(df["ventas_totales"].sum())
        pedidos = 0  # no order IDs available pre-2025
    elif anio == 2025:
        df = _get_shopify_ventas_2025(engine, anio, mes)
        df_dedup = df.drop_duplicates(subset=["id_de_pedido"])
        ventas_brutas = float(df_dedup["ventas_totales"].sum())
        pedidos = int(df_dedup["id_de_pedido"].nunique())
    else:
        ventas_brutas = 0.0
        pedidos = 0

    ventas_netas = ventas_brutas / 1.19

    meta_gasto = meta_gasto_override if meta_gasto_override is not None else _get_meta_gasto(engine, anio, mes)

    fulfillment = pymespace_costo(pedidos) if pedidos > 0 else 0.0
    comision_mp = ventas_brutas * COMISION_MP_EFECTIVA
    comision_shopify = ventas_brutas * COMISION_SHOPIFY

    margen_bruto = (
        ventas_netas
        - blueexpress_neto
        - meta_gasto
        - fulfillment
        - comision_mp
        - comision_shopify
    )

    resultado_operativo = margen_bruto - (TOTAL_CF_MENSUAL if incluir_fijos else 0)

    pct_meta = (meta_gasto / ventas_netas * 100) if ventas_netas > 0 else 0
    roas = (ventas_netas / meta_gasto) if meta_gasto > 0 else 0
    margen_pct = (margen_bruto / ventas_netas * 100) if ventas_netas > 0 else 0

    return {
        "anio": anio,
        "mes": mes,
        "ventas_brutas": round(ventas_brutas),
        "ventas_netas": round(ventas_netas),
        "pedidos": pedidos,
        "ticket_promedio": round(ventas_netas / pedidos) if pedidos > 0 else 0,
        "meta_gasto": round(meta_gasto),
        "blueexpress_neto": round(blueexpress_neto),
        "fulfillment": round(fulfillment),
        "comision_mp": round(comision_mp),
        "comision_shopify": round(comision_shopify),
        "margen_bruto": round(margen_bruto),
        "margen_pct": round(margen_pct, 1),
        "costos_fijos": round(TOTAL_CF_MENSUAL) if incluir_fijos else 0,
        "resultado_operativo": round(resultado_operativo),
        "roas": round(roas, 2),
        "pct_meta_ventas": round(pct_meta, 1),
    }
