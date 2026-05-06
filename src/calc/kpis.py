"""
KPI thresholds and alert generation for Happy Lapiz dashboard.
"""

from __future__ import annotations

ROAS_MINIMO = 3.5
PCT_META_MAX = 28.0
MARGEN_BRUTO_MIN = 30.0
PCT_PRODUCT_ADS_ML_MAX = 15.0

PRODUCT_CLASSIFICATION = {
    "ESCALAR": lambda margen_pct, volumen: margen_pct > 65 and volumen,
    "OK": lambda margen_pct, volumen: margen_pct > 50,
    "REVISAR": lambda margen_pct, volumen: margen_pct < 50,
    "PAUSAR": lambda margen_pct, volumen: margen_pct < 0,
}


def check_alerts(pnl_shopify: dict, pnl_ml: dict) -> list[dict]:
    """
    Generate alert list based on P&L data.
    Returns list of {nivel, mensaje, campo} dicts.
    """
    alerts = []

    roas = pnl_shopify.get("roas", 0)
    if roas > 0 and roas < ROAS_MINIMO:
        alerts.append({
            "nivel": "ROJA",
            "mensaje": f"ROAS bajo umbral mínimo ({roas:.2f}x < {ROAS_MINIMO}x)",
            "campo": "roas",
        })

    pct_meta = pnl_shopify.get("pct_meta_ventas", 0)
    if pct_meta > PCT_META_MAX:
        alerts.append({
            "nivel": "ROJA",
            "mensaje": f"Gasto publicitario excesivo ({pct_meta:.1f}% > {PCT_META_MAX}%)",
            "campo": "pct_meta_ventas",
        })

    margen_pct = pnl_shopify.get("margen_pct", 100)
    if margen_pct < MARGEN_BRUTO_MIN:
        alerts.append({
            "nivel": "ROJA",
            "mensaje": f"Margen bruto bajo umbral ({margen_pct:.1f}% < {MARGEN_BRUTO_MIN}%)",
            "campo": "margen_pct",
        })

    pct_ads_ml = pnl_ml.get("pct_product_ads", 0)
    if pct_ads_ml > PCT_PRODUCT_ADS_ML_MAX:
        alerts.append({
            "nivel": "ROJA",
            "mensaje": f"Publicidad ML elevada ({pct_ads_ml:.1f}% > {PCT_PRODUCT_ADS_ML_MAX}%)",
            "campo": "pct_product_ads",
        })

    if pnl_ml.get("incumplimiento_full", 0) > 0:
        alerts.append({
            "nivel": "ROJA",
            "mensaje": "Multa operativa ML detectada (incumplimiento Full)",
            "campo": "incumplimiento_full",
        })

    resultado = pnl_shopify.get("resultado_operativo", 0)
    if resultado < 0:
        alerts.append({
            "nivel": "ROJA",
            "mensaje": f"Resultado operativo negativo: {resultado:,.0f} CLP",
            "campo": "resultado_operativo",
        })

    return alerts


def classify_product(margen_pct: float, volumen_relevante: bool = True) -> str:
    if margen_pct < 0:
        return "PAUSAR"
    if margen_pct > 65 and volumen_relevante:
        return "ESCALAR"
    if margen_pct > 50:
        return "OK"
    return "REVISAR"


def target_diario_neto() -> float:
    """Minimum daily neto target to cover fixed costs."""
    from src.calc.costos_fijos import TOTAL_CF_MENSUAL
    return TOTAL_CF_MENSUAL / 30


BREAK_EVEN_MENSUAL_NETO = 7_000_000
TARGET_RENTABILIDAD_NETO = 9_000_000
ROAS_TARGET = 4.5
