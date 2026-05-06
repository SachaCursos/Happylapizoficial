"""
Plain-text email reports for Make.com automation.
Scenarios: 4317154 (daily), 4317155 (weekly), 4317156 (monthly).
"""

from __future__ import annotations

from datetime import date, timedelta

from src.calc.costos_fijos import TOTAL_CF_MENSUAL, TOTAL_CF_SEMANAL
from src.calc.kpis import check_alerts, BREAK_EVEN_MENSUAL_NETO, TARGET_RENTABILIDAD_NETO


def _fmt_clp(value: float) -> str:
    return f"${value:,.0f}"


def _semaforo(value: float, umbral: float, inverso: bool = False) -> str:
    ok = value >= umbral if not inverso else value <= umbral
    return "✓" if ok else "✗"


def reporte_diario(pnl_shopify: dict, pnl_ml: dict, fecha: date | None = None) -> str:
    fecha = fecha or date.today()
    alertas = check_alerts(pnl_shopify, pnl_ml)

    lineas = [
        f"HAPPY LAPIZ — REPORTE DIARIO {fecha.strftime('%d/%m/%Y')}",
        "=" * 50,
        "",
        "SHOPIFY HOY",
        f"  Ventas brutas : {_fmt_clp(pnl_shopify.get('ventas_brutas', 0))}",
        f"  Ventas netas  : {_fmt_clp(pnl_shopify.get('ventas_netas', 0))}",
        f"  Pedidos       : {pnl_shopify.get('pedidos', 0)}",
        f"  Meta Ads      : {_fmt_clp(pnl_shopify.get('meta_gasto', 0))}",
        f"  ROAS          : {pnl_shopify.get('roas', 0):.2f}x",
        "",
        "ALERTAS",
    ]
    if alertas:
        for a in alertas:
            lineas.append(f"  [!] {a['mensaje']}")
    else:
        lineas.append("  Sin alertas activas.")

    lineas += [
        "",
        f"Target diario mínimo CF: {_fmt_clp(TOTAL_CF_MENSUAL / 30)}",
    ]

    return "\n".join(lineas)


def reporte_semanal(pnl_shopify: dict, pnl_ml: dict, semana_inicio: date | None = None) -> str:
    semana_inicio = semana_inicio or (date.today() - timedelta(days=7))
    semana_fin = semana_inicio + timedelta(days=6)
    alertas = check_alerts(pnl_shopify, pnl_ml)

    lineas = [
        f"HAPPY LAPIZ — REPORTE SEMANAL {semana_inicio.strftime('%d/%m')} al {semana_fin.strftime('%d/%m/%Y')}",
        "=" * 50,
        "",
        "SHOPIFY SEMANA",
        f"  Ventas netas  : {_fmt_clp(pnl_shopify.get('ventas_netas', 0))}",
        f"  Meta Ads      : {_fmt_clp(pnl_shopify.get('meta_gasto', 0))}",
        f"  ROAS          : {pnl_shopify.get('roas', 0):.2f}x  {_semaforo(pnl_shopify.get('roas', 0), 3.5)}",
        f"  % Meta/Ventas : {pnl_shopify.get('pct_meta_ventas', 0):.1f}%  {_semaforo(pnl_shopify.get('pct_meta_ventas', 0), 28, inverso=True)}",
        f"  Margen bruto  : {pnl_shopify.get('margen_pct', 0):.1f}%  {_semaforo(pnl_shopify.get('margen_pct', 0), 30)}",
        "",
        "MERCADO LIBRE SEMANA",
        f"  Ventas netas  : {_fmt_clp(pnl_ml.get('ventas_netas', 0))}",
        f"  Margen bruto  : {pnl_ml.get('margen_pct', 0):.1f}%",
        f"  Product Ads   : {pnl_ml.get('pct_product_ads', 0):.1f}%  {_semaforo(pnl_ml.get('pct_product_ads', 0), 15, inverso=True)}",
        "",
        "COSTOS FIJOS SEMANA",
        f"  Prorrateo     : {_fmt_clp(TOTAL_CF_SEMANAL)}",
        "",
        "ALERTAS",
    ]
    if alertas:
        for a in alertas:
            lineas.append(f"  [!] {a['mensaje']}")
    else:
        lineas.append("  Sin alertas activas.")

    return "\n".join(lineas)


def reporte_mensual(pnl_shopify: dict, pnl_ml: dict, anio: int, mes: int) -> str:
    MESES = ["", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
              "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
    nombre_mes = MESES[mes] if 1 <= mes <= 12 else str(mes)
    alertas = check_alerts(pnl_shopify, pnl_ml)

    ventas_netas_total = (
        pnl_shopify.get("ventas_netas", 0) + pnl_ml.get("ventas_netas", 0)
    )
    resultado = pnl_shopify.get("resultado_operativo", 0)

    lineas = [
        f"HAPPY LAPIZ — CIERRE MENSUAL {nombre_mes} {anio}",
        "=" * 50,
        "",
        "SHOPIFY",
        f"  Ventas brutas    : {_fmt_clp(pnl_shopify.get('ventas_brutas', 0))}",
        f"  Ventas netas     : {_fmt_clp(pnl_shopify.get('ventas_netas', 0))}",
        f"  Pedidos          : {pnl_shopify.get('pedidos', 0)}",
        f"  Ticket promedio  : {_fmt_clp(pnl_shopify.get('ticket_promedio', 0))}",
        f"  Meta Ads         : {_fmt_clp(pnl_shopify.get('meta_gasto', 0))}",
        f"  ROAS             : {pnl_shopify.get('roas', 0):.2f}x",
        f"  BluExpress       : {_fmt_clp(pnl_shopify.get('blueexpress_neto', 0))}",
        f"  Fulfillment      : {_fmt_clp(pnl_shopify.get('fulfillment', 0))}",
        f"  Comision MP      : {_fmt_clp(pnl_shopify.get('comision_mp', 0))}",
        f"  Comision Shopify : {_fmt_clp(pnl_shopify.get('comision_shopify', 0))}",
        f"  Margen bruto     : {_fmt_clp(pnl_shopify.get('margen_bruto', 0))} ({pnl_shopify.get('margen_pct', 0):.1f}%)",
        f"  Costos fijos     : {_fmt_clp(pnl_shopify.get('costos_fijos', 0))}",
        f"  RESULTADO        : {_fmt_clp(resultado)}",
        "",
        "MERCADO LIBRE",
        f"  Ventas netas     : {_fmt_clp(pnl_ml.get('ventas_netas', 0))}",
        f"  Pedidos FULL     : {pnl_ml.get('pedidos_full', 0)}",
        f"  Pedidos FLEX     : {pnl_ml.get('pedidos_flex', 0)}",
        f"  Comision ML      : {_fmt_clp(pnl_ml.get('comision_ml', 0))}",
        f"  Product Ads      : {_fmt_clp(pnl_ml.get('product_ads', 0))} ({pnl_ml.get('pct_product_ads', 0):.1f}%)",
        f"  Margen bruto     : {_fmt_clp(pnl_ml.get('margen_bruto', 0))} ({pnl_ml.get('margen_pct', 0):.1f}%)",
        "",
        "CONSOLIDADO",
        f"  Ventas netas total : {_fmt_clp(ventas_netas_total)}",
        f"  Break-even         : {_fmt_clp(BREAK_EVEN_MENSUAL_NETO)}",
        f"  Target rentab.     : {_fmt_clp(TARGET_RENTABILIDAD_NETO)}",
        "",
        "ALERTAS",
    ]
    if alertas:
        for a in alertas:
            lineas.append(f"  [!] {a['mensaje']}")
    else:
        lineas.append("  Sin alertas activas.")

    return "\n".join(lineas)
