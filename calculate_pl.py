import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")

META_FILE = os.path.join(BASE_DIR, "meta_ads_data.json")
SHOPIFY_FILE = os.path.join(BASE_DIR, "shopify_orders_data.json")
OUTPUT_FILE = os.path.join(BASE_DIR, "pl_data.json")

BLUEEXPRESS_TARIFF_FILE = os.path.join(DATA_DIR, "blueexpress_tariff.json")
PRODUCTS_FILE = os.path.join(DATA_DIR, "products.json")
COMMISSIONS_FILE = os.path.join(DATA_DIR, "commissions.json")
SHIPPING_FILE = os.path.join(DATA_DIR, "shipping_rates.json")
FIXED_COSTS_FILE = os.path.join(DATA_DIR, "fixed_costs.json")

SEMANAS_POR_MES = 4.33


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ── BluExpress ────────────────────────────────────────────────────────────────

def load_blueexpress():
    if not os.path.exists(BLUEEXPRESS_TARIFF_FILE):
        return None
    return load_json(BLUEEXPRESS_TARIFF_FILE)


def normalize_comuna(name):
    """Normaliza nombre de comuna para buscar en tarifario."""
    import unicodedata
    s = name.upper().strip()
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s


def lookup_blueexpress_rate(tariff, comuna_name, peso_kg):
    """Retorna tarifa neta CLP para comuna y peso, o None si no se puede."""
    if tariff is None:
        return None

    comunas = tariff.get("comunas", {})
    key = normalize_comuna(comuna_name)

    # Try exact match first, then partial
    entry = comunas.get(key)
    if entry is None:
        for k, v in comunas.items():
            if k.startswith(key) or key.startswith(k):
                entry = v
                break

    if entry is None:
        return None

    tramos = tariff.get("tramos_kg", [])
    tarifas = entry.get("tarifas_neto", [])

    if not tramos or not tarifas:
        return None

    # Find band index: first tramo >= peso_kg
    idx = len(tramos) - 1
    for i, tramo_max in enumerate(tramos):
        if peso_kg <= tramo_max:
            idx = i
            break

    rate = tarifas[idx] if idx < len(tarifas) else None
    return rate


# ── Dimensiones / peso por pedido ─────────────────────────────────────────────

def get_package_for_order(items, products_by_sku, shipping_cfg):
    """
    Calcula dimensiones y peso total del paquete de un pedido.
    Lógica: ancho=suma, alto=max, largo=max (igual que packs).
    Para pedidos con múltiples items, aplica la misma lógica iterativamente.
    """
    total_ancho = 0.0
    total_alto = 0.0
    total_largo = 0.0
    total_peso_g = 0.0
    skus_sin_datos = []

    for item in items:
        sku = item.get("sku", "")
        qty = int(item.get("cantidad", 1))
        prod = products_by_sku.get(sku)

        if prod is None:
            skus_sin_datos.append(sku)
            continue

        dims = prod.get("dimensiones_cm", {})
        peso_g = prod.get("peso_gramos", 0) or 0
        ancho = dims.get("ancho", 0) or 0
        alto = dims.get("alto", 0) or 0
        largo = dims.get("largo", 0) or 0

        for _ in range(qty):
            total_ancho += ancho
            total_alto = max(total_alto, alto)
            total_largo = max(total_largo, largo)
            total_peso_g += peso_g

    factor = shipping_cfg.get("factor_volumetrico", 4000)
    peso_real_kg = total_peso_g / 1000
    peso_vol_kg = (total_ancho * total_alto * total_largo) / factor
    peso_cobrado_kg = max(peso_real_kg, peso_vol_kg)

    return {
        "ancho": total_ancho,
        "alto": total_alto,
        "largo": total_largo,
        "peso_real_kg": round(peso_real_kg, 3),
        "peso_volumetrico_kg": round(peso_vol_kg, 3),
        "peso_cobrado_kg": round(peso_cobrado_kg, 3),
        "skus_sin_datos": skus_sin_datos,
    }


# ── COGS ──────────────────────────────────────────────────────────────────────

def calc_cogs(items, products_by_sku):
    cogs = 0.0
    sin_costo = []
    for item in items:
        sku = item.get("sku", "")
        qty = int(item.get("cantidad", 1))
        prod = products_by_sku.get(sku)
        if prod:
            cogs += prod.get("costo_neto", 0) * qty
        else:
            sin_costo.append(sku)
    return round(cogs, 2), sin_costo


# ── Fulfillment ───────────────────────────────────────────────────────────────

def calc_fulfillment(peso_g, shipping_cfg):
    tramos = shipping_cfg.get("tarifas_fulfillment", [])
    for t in tramos:
        if t["desde"] <= peso_g <= t["hasta"]:
            return t["precio_neto"]
    return shipping_cfg.get("fulfillment_por_pedido_neto", 1000)


# ── Costos fijos prorrateados ─────────────────────────────────────────────────

def get_fixed_costs(fixed_cfg, mode):
    total_mensual = fixed_cfg.get("total_mensual_neto", 0)
    if mode == "weekly":
        semanas = fixed_cfg.get("semanas_por_mes", SEMANAS_POR_MES)
        return round(total_mensual / semanas, 0)
    return float(total_mensual)


# ── Main ──────────────────────────────────────────────────────────────────────

def calculate_pl():
    for path, label in [
        (META_FILE, "meta_ads_data.json"),
        (SHOPIFY_FILE, "shopify_orders_data.json"),
        (PRODUCTS_FILE, "products.json"),
        (COMMISSIONS_FILE, "commissions.json"),
        (SHIPPING_FILE, "shipping_rates.json"),
        (FIXED_COSTS_FILE, "fixed_costs.json"),
    ]:
        if not os.path.exists(path):
            print(f"Error: {label} no encontrado.", file=sys.stderr)
            sys.exit(1)

    meta_data = load_json(META_FILE)
    shopify_data = load_json(SHOPIFY_FILE)
    products_list = load_json(PRODUCTS_FILE)
    commissions = load_json(COMMISSIONS_FILE)
    shipping_cfg = load_json(SHIPPING_FILE)
    fixed_cfg = load_json(FIXED_COSTS_FILE)
    blueexpress = load_blueexpress()

    mode = meta_data["meta"]["modo"]
    desde = meta_data["meta"]["periodo"]["desde"]
    hasta = meta_data["meta"]["periodo"]["hasta"]

    products_by_sku = {p["sku"]: p for p in products_list}

    # ── Shopify revenue ──
    shopify_totals = shopify_data["totales"]
    ventas_brutas = shopify_totals["ventas_brutas_clp"]
    ventas_netas = shopify_totals["ventas_netas_clp"]
    iva_total = round(ventas_brutas - ventas_netas, 2)

    # ── Comisiones ──
    comision_shopify = round(ventas_brutas * commissions["shopify"]["comision_pct"], 2)
    comision_mp = round(ventas_brutas * commissions["mercado_pago"]["tasa_efectiva_pct"], 2)

    # ── Por-pedido: COGS + envío BluExpress + fulfillment ──
    cogs_total = 0.0
    envios_total = 0.0
    fulfillment_total = 0.0
    envios_promedio_usados = 0
    skus_sin_costo_global = set()

    blueexpress_fallback = shipping_cfg.get("costo_promedio_real_neto", 3969)
    blueexpress_diff = shipping_cfg.get("tarifario_home_delivery", {}).get(
        "diferencia_factura_vs_tarifa", {}
    ).get("diff_promedio_clp", 0)

    detalle_pedidos = []

    for pedido in shopify_data["pedidos"]:
        # Skip refunded-only orders from cost calculations
        if pedido["estado_pago"] == "voided":
            continue

        items = pedido["items"]
        cogs, sin_costo = calc_cogs(items, products_by_sku)
        skus_sin_costo_global.update(sin_costo)

        pkg = get_package_for_order(items, products_by_sku, shipping_cfg)
        fulfillment = calc_fulfillment(pkg["peso_real_kg"] * 1000, shipping_cfg)

        # BluExpress cost
        comuna = pedido.get("comuna_destino", "")
        envio_rate = lookup_blueexpress_rate(blueexpress, comuna, pkg["peso_cobrado_kg"])

        if envio_rate is not None:
            # Apply invoice adjustment (real invoices ~121 CLP above tariff)
            envio_costo = envio_rate + blueexpress_diff
        else:
            envio_costo = blueexpress_fallback
            envios_promedio_usados += 1

        cogs_total += cogs
        envios_total += envio_costo
        fulfillment_total += fulfillment

        detalle_pedidos.append({
            "id": pedido["nombre"],
            "fecha": pedido["fecha"],
            "venta_bruta": pedido["venta_bruta_clp"],
            "cogs": cogs,
            "envio_costo": envio_costo,
            "envio_comuna": comuna,
            "envio_peso_cobrado_kg": pkg["peso_cobrado_kg"],
            "fulfillment": fulfillment,
            "envio_tarifa_usada": "blueexpress" if envio_rate is not None else "promedio",
        })

    cogs_total = round(cogs_total, 2)
    envios_total = round(envios_total, 2)
    fulfillment_total = round(fulfillment_total, 2)

    # ── Meta Ads ──
    meta_gasto = meta_data["resumen"]["gasto_total"]
    meta_roas = meta_data["resumen"]["roas_global"]

    # ── Costos fijos ──
    costos_fijos = get_fixed_costs(fixed_cfg, mode)

    # ── P&L waterfall ──
    margen_bruto = round(ventas_netas - cogs_total, 2)
    margen_bruto_pct = round(margen_bruto / ventas_netas * 100, 1) if ventas_netas else 0

    gastos_variables = round(comision_shopify + comision_mp + envios_total + fulfillment_total, 2)
    margen_contribucion = round(margen_bruto - gastos_variables, 2)
    margen_contribucion_pct = round(margen_contribucion / ventas_netas * 100, 1) if ventas_netas else 0

    utilidad_operacional = round(margen_contribucion - meta_gasto - costos_fijos, 2)
    utilidad_pct = round(utilidad_operacional / ventas_netas * 100, 1) if ventas_netas else 0

    # ROAS efectivo: ventas_netas / (meta + variables + fijos)
    total_costos = round(cogs_total + gastos_variables + meta_gasto + costos_fijos, 2)
    roas_efectivo = round(ventas_netas / total_costos, 2) if total_costos else 0

    pl = {
        "meta": {
            "modo": mode,
            "periodo": {"desde": desde, "hasta": hasta},
            "blueexpress_tarifario_disponible": blueexpress is not None,
            "pedidos_con_tarifa_promedio": envios_promedio_usados,
            "skus_sin_costo": list(skus_sin_costo_global),
        },
        "ingresos": {
            "ventas_brutas_clp": ventas_brutas,
            "iva_clp": iva_total,
            "ventas_netas_clp": ventas_netas,
        },
        "costo_ventas": {
            "cogs_clp": cogs_total,
        },
        "margen_bruto": {
            "clp": margen_bruto,
            "pct": margen_bruto_pct,
        },
        "gastos_variables": {
            "comision_shopify_clp": comision_shopify,
            "comision_mercadopago_clp": comision_mp,
            "envios_blueexpress_clp": envios_total,
            "fulfillment_clp": fulfillment_total,
            "total_clp": gastos_variables,
        },
        "margen_contribucion": {
            "clp": margen_contribucion,
            "pct": margen_contribucion_pct,
        },
        "marketing": {
            "meta_ads_clp": meta_gasto,
            "meta_roas": meta_roas,
        },
        "costos_fijos": {
            "clp": costos_fijos,
            "base": "mensual" if mode == "monthly" else "prorrateado_semanal",
        },
        "utilidad_operacional": {
            "clp": utilidad_operacional,
            "pct": utilidad_pct,
        },
        "metricas": {
            "roas_meta": meta_roas,
            "roas_efectivo": roas_efectivo,
            "cantidad_pedidos": shopify_totals["cantidad_pedidos"],
            "ticket_promedio_neto": round(
                ventas_netas / shopify_totals["cantidad_pedidos"], 0
            ) if shopify_totals["cantidad_pedidos"] else 0,
            "costo_promedio_por_pedido": round(
                total_costos / shopify_totals["cantidad_pedidos"], 0
            ) if shopify_totals["cantidad_pedidos"] else 0,
        },
        "detalle_pedidos": detalle_pedidos,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(pl, f, ensure_ascii=False, indent=2)

    u = utilidad_operacional
    signo = "+" if u >= 0 else ""
    print(f"✅ pl_data.json guardado")
    print(f"   Ventas netas:        ${ventas_netas:>12,.0f} CLP".replace(",", "."))
    print(f"   Margen bruto:        ${margen_bruto:>12,.0f} CLP ({margen_bruto_pct}%)".replace(",", "."))
    print(f"   Margen contribución: ${margen_contribucion:>12,.0f} CLP ({margen_contribucion_pct}%)".replace(",", "."))
    print(f"   Meta Ads:           -${meta_gasto:>12,.0f} CLP".replace(",", "."))
    print(f"   Costos fijos:       -${costos_fijos:>12,.0f} CLP".replace(",", "."))
    print(f"   Utilidad operac.:  {signo}${u:>12,.0f} CLP ({utilidad_pct}%)".replace(",", "."))
    print(f"   ROAS efectivo:       {roas_efectivo}x")


if __name__ == "__main__":
    calculate_pl()
