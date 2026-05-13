import json
import os
import sys
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
META_FILE = os.path.join(BASE_DIR, "meta_ads_data.json")
PL_FILE = os.path.join(BASE_DIR, "pl_data.json")
OUTPUT_FILE = os.path.join(BASE_DIR, "report_output.txt")

ROAS_MINIMO = 3.5
ROAS_PAUSAR = 1.5


# ── Formato ───────────────────────────────────────────────────────────────────

def clp(amount):
    """Formatea como $1.234.567 CLP (puntos como miles, sin decimales)."""
    return f"${int(amount):,} CLP".replace(",", ".")


def num(n):
    """Formatea número con puntos como miles."""
    return f"{int(n):,}".replace(",", ".")


def roas_badge(roas):
    return "🟢" if roas >= ROAS_MINIMO else "🔴"


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ── Generación ────────────────────────────────────────────────────────────────

def pct_badge(pct):
    if pct >= 15:
        return "🟢"
    if pct >= 5:
        return "🟡"
    return "🔴"


def generate_report():
    if not os.path.exists(META_FILE):
        print(f"Error: {META_FILE} no encontrado. Ejecuta fetch_meta_ads.py primero.")
        sys.exit(1)

    meta_data = load_json(META_FILE)
    pl_data = load_json(PL_FILE) if os.path.exists(PL_FILE) else None

    info = meta_data["meta"]
    resumen = meta_data["resumen"]
    gasto_diario = meta_data["gasto_diario"]
    campanas = meta_data["campanas"]

    mode = info["modo"]
    desde = info["periodo"]["desde"]
    hasta = info["periodo"]["hasta"]
    modo_label = "SEMANAL" if mode == "weekly" else "MENSUAL"
    periodo_label = f"{'semana' if mode == 'weekly' else 'período'}"
    fecha_gen = datetime.now().strftime("%Y-%m-%d %H:%M")

    gasto_total = resumen["gasto_total"]
    ingresos_total = resumen["ingresos_pixel_total"]
    roas_global = resumen["roas_global"]

    SEP = "═" * 43
    SEP_THIN = "─" * 35

    L = []   # líneas del reporte

    # ── Encabezado ──
    L += [
        SEP,
        f"HAPPY LÁPIZ — REPORTE {modo_label}",
        f"Período: {desde} al {hasta}",
        f"Generado: {fecha_gen}",
        SEP,
        "",
    ]

    # ── Resumen Meta Ads ──
    alerta_roas = "  ← ALERTA" if roas_global < ROAS_MINIMO else ""
    L += [
        "📊 RESUMEN META ADS",
        SEP_THIN,
        f"Gasto total:        {clp(gasto_total)}",
        f"Ingresos pixel:     {clp(ingresos_total)}",
        f"ROAS global:        {roas_global}x  {roas_badge(roas_global)}{alerta_roas}",
        f"Compras pixel:      {resumen['compras_pixel']}",
        f"Impresiones:        {num(resumen['impresiones'])}",
        f"CTR promedio:       {resumen['ctr_promedio']:.2f}%",
        f"Último día c/gasto: {resumen['ultimo_dia_con_gasto'] or 'N/D'}",
        "",
    ]

    # ── Rendimiento por producto ──
    L += ["📦 RENDIMIENTO POR PRODUCTO", SEP_THIN]
    por_producto = {}
    for c in campanas:
        prod = c["producto"]
        if prod not in por_producto:
            por_producto[prod] = {"gasto": 0.0, "ingresos": 0.0, "compras": 0}
        por_producto[prod]["gasto"] += c["gasto_clp"]
        por_producto[prod]["ingresos"] += c["ingresos_pixel_clp"]
        por_producto[prod]["compras"] += c["compras_pixel"]

    for prod, d in sorted(por_producto.items(), key=lambda x: -x[1]["gasto"]):
        if d["gasto"] == 0:
            continue
        prod_roas = round(d["ingresos"] / d["gasto"], 2) if d["gasto"] > 0 else 0.0
        L += [
            prod,
            f"  Gasto:     {clp(d['gasto'])}",
            f"  Ingresos:  {clp(d['ingresos'])}",
            f"  ROAS:      {prod_roas}x  {roas_badge(prod_roas)}",
            f"  Compras:   {d['compras']}",
            "",
        ]

    # ── Gasto diario ──
    L += ["📅 GASTO DIARIO", SEP_THIN]
    col_fecha = 12
    col_gasto = 14
    col_impr = 14
    col_clics = 8
    col_ctr = 7
    header = (
        f"{'Fecha':<{col_fecha}}"
        f"{'Gasto':>{col_gasto}}"
        f"{'Impresiones':>{col_impr}}"
        f"{'Clics':>{col_clics}}"
        f"{'CTR':>{col_ctr}}"
    )
    L.append(header)
    L.append("-" * len(header))
    dias_con_gasto = [d for d in gasto_diario if d["gasto_clp"] > 0]
    if not dias_con_gasto:
        L.append("Sin datos de gasto en el período")
    for d in dias_con_gasto:
        gasto_fmt = f"${int(d['gasto_clp']):,}".replace(",", ".")
        impr_fmt = num(d["impresiones"])
        clics_fmt = num(d["clics"])
        ctr_fmt = f"{d['ctr']:.2f}%"
        L.append(
            f"{d['dia']:<{col_fecha}}"
            f"{gasto_fmt:>{col_gasto}}"
            f"{impr_fmt:>{col_impr}}"
            f"{clics_fmt:>{col_clics}}"
            f"{ctr_fmt:>{col_ctr}}"
        )
    L.append("")

    # ── Campañas activas ──
    L += ["📋 CAMPAÑAS ACTIVAS", SEP_THIN]
    cam_col_nombre = 36
    cam_col_gasto = 14
    cam_col_roas = 7
    cam_col_compras = 9
    cam_header = (
        f"{'Campaña':<{cam_col_nombre}}"
        f"{'Gasto':>{cam_col_gasto}}"
        f"{'ROAS':>{cam_col_roas}}"
        f"{'Compras':>{cam_col_compras}}"
    )
    L.append(cam_header)
    L.append("-" * len(cam_header))
    for c in sorted(campanas, key=lambda x: -x["gasto_clp"]):
        nombre = c["nombre"][:cam_col_nombre - 1] if len(c["nombre"]) >= cam_col_nombre else c["nombre"]
        gasto_fmt = f"${int(c['gasto_clp']):,}".replace(",", ".")
        roas_fmt = f"{c['roas']:.2f}x"
        L.append(
            f"{nombre:<{cam_col_nombre}}"
            f"{gasto_fmt:>{cam_col_gasto}}"
            f"{roas_fmt:>{cam_col_roas}}"
            f"{c['compras_pixel']:>{cam_col_compras}}"
        )
    L.append("")

    # ── P&L completo ──
    if pl_data:
        ing = pl_data["ingresos"]
        cv = pl_data["costo_ventas"]
        mb = pl_data["margen_bruto"]
        gv = pl_data["gastos_variables"]
        mc = pl_data["margen_contribucion"]
        mkt = pl_data["marketing"]
        cf = pl_data["costos_fijos"]
        ut = pl_data["utilidad_operacional"]
        met = pl_data["metricas"]

        fijos_label = "Costos fijos (mensual)" if mode == "monthly" else "Costos fijos (prorrateado)"

        L += [
            "💰 P&L OPERACIONAL",
            SEP_THIN,
            f"{'Ventas brutas:':<28} {clp(ing['ventas_brutas_clp']):>14}",
            f"{'  − IVA (19%):':<28} {clp(ing['iva_clp']):>14}",
            f"{'Ventas netas:':<28} {clp(ing['ventas_netas_clp']):>14}",
            f"{'  − COGS:':<28} {clp(cv['cogs_clp']):>14}",
            f"{'Margen bruto:':<28} {clp(mb['clp']):>14}  ({mb['pct']}%)  {pct_badge(mb['pct'])}",
            SEP_THIN,
            f"{'  − Comisión Shopify (1%):':<28} {clp(gv['comision_shopify_clp']):>14}",
            f"{'  − Comisión MercadoPago:':<28} {clp(gv['comision_mercadopago_clp']):>14}",
            f"{'  − Envíos BluExpress:':<28} {clp(gv['envios_blueexpress_clp']):>14}",
            f"{'  − Fulfillment:':<28} {clp(gv['fulfillment_clp']):>14}",
            f"{'Margen contribución:':<28} {clp(mc['clp']):>14}  ({mc['pct']}%)  {pct_badge(mc['pct'])}",
            SEP_THIN,
            f"{'  − Meta Ads:':<28} {clp(mkt['meta_ads_clp']):>14}",
            f"{'  − {fijos_label}:':<28} {clp(cf['clp']):>14}",
            f"{'Utilidad operacional:':<28} {clp(ut['clp']):>14}  ({ut['pct']}%)  {pct_badge(ut['pct'])}",
            SEP_THIN,
            f"{'ROAS Meta:':<28} {mkt['meta_roas']}x  {roas_badge(mkt['meta_roas'])}",
            f"{'ROAS efectivo:':<28} {met['roas_efectivo']}x  {pct_badge(met['roas_efectivo'] * 10 - 5)}",
            f"{'Pedidos:':<28} {met['cantidad_pedidos']}",
            f"{'Ticket prom. neto:':<28} {clp(met['ticket_promedio_neto'])}",
            "",
        ]

        # Advertencias de datos incompletos
        skus_sin_costo = pl_data["meta"].get("skus_sin_costo", [])
        pedidos_promedio = pl_data["meta"].get("pedidos_con_tarifa_promedio", 0)
        if skus_sin_costo:
            L.append(f"⚠️  SKUs sin costo en products.json: {', '.join(skus_sin_costo)}")
        if pedidos_promedio:
            L.append(f"⚠️  {pedidos_promedio} pedidos usaron tarifa promedio BluExpress (comuna no encontrada)")
        if skus_sin_costo or pedidos_promedio:
            L.append("")

    # ── Alertas ──
    L += ["⚠️  ALERTAS META ADS", SEP_THIN]
    alertas = []
    if roas_global < ROAS_MINIMO:
        alertas.append(f"🔴 ROAS global {roas_global}x bajo el umbral mínimo ({ROAS_MINIMO}x)")
    for c in sorted(campanas, key=lambda x: x["roas"]):
        if c["gasto_clp"] > 0 and c["roas"] < ROAS_MINIMO:
            accion = "pausar" if c["roas"] < ROAS_PAUSAR else "revisar"
            nombre_corto = c["nombre"][:45]
            alertas.append(f"🔴 Campaña '{nombre_corto}' con ROAS {c['roas']}x — {accion}")

    if alertas:
        L += alertas
    else:
        L.append(f"✅ Sin alertas esta {periodo_label}")

    # ── Pie ──
    L += [
        "",
        SEP_THIN,
        "Fuente: Meta Ads API · Cuenta act_449499746278703",
        "Datos pixel pueden diferir de ventas Shopify reales",
        SEP,
    ]

    report_text = "\n".join(L)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(report_text)

    print(f"✅ report_output.txt guardado ({len(L)} líneas)")
    return report_text


if __name__ == "__main__":
    generate_report()
