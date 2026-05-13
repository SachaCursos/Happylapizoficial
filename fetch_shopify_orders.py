import json
import os
import sys
from datetime import datetime, date, timedelta
from calendar import monthrange

import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_FILE = os.path.join(BASE_DIR, "shopify_orders_data.json")

SHOPIFY_API_VERSION = "2024-04"


def get_shop_domain():
    domain = os.environ.get("SHOPIFY_SHOP_DOMAIN", "")
    # normalize: strip protocol and trailing slash
    domain = domain.replace("https://", "").replace("http://", "").rstrip("/")
    return domain


def get_period():
    mode = os.environ.get("REPORT_MODE", "weekly")
    today = date.today()

    if mode == "weekly":
        # Last complete Monday–Sunday
        last_sunday = today - timedelta(days=today.weekday() + 1)
        last_monday = last_sunday - timedelta(days=6)
        return str(last_monday), str(last_sunday)

    # monthly: previous full calendar month
    first_this_month = today.replace(day=1)
    last_month_end = first_this_month - timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)
    return str(last_month_start), str(last_month_end)


def shopify_get(domain, token, endpoint, params=None):
    url = f"https://{domain}/admin/api/{SHOPIFY_API_VERSION}/{endpoint}"
    headers = {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}
    resp = requests.get(url, headers=headers, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json(), resp.headers


def fetch_all_orders(domain, token, since_date, until_date):
    """Paginate through all orders in the given date range."""
    orders = []
    params = {
        "status": "any",
        "created_at_min": f"{since_date}T00:00:00-03:00",
        "created_at_max": f"{until_date}T23:59:59-03:00",
        "limit": 250,
        "fields": (
            "id,name,created_at,financial_status,fulfillment_status,"
            "total_price,subtotal_price,total_tax,"
            "line_items,shipping_address,shipping_lines"
        ),
    }

    while True:
        data, headers = shopify_get(domain, token, "orders.json", params)
        batch = data.get("orders", [])
        orders.extend(batch)

        # Follow cursor-based pagination via Link header
        link = headers.get("Link", "")
        next_url = None
        for part in link.split(","):
            part = part.strip()
            if 'rel="next"' in part:
                next_url = part.split(";")[0].strip().lstrip("<").rstrip(">")
                break

        if not next_url:
            break

        # Extract page_info from next_url for next iteration
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(next_url).query)
        params = {"limit": 250, "page_info": qs["page_info"][0]}

    return orders


def process_orders(orders):
    """Summarise orders into the structure calculate_pl.py expects."""
    pedidos = []
    totales = {
        "cantidad_pedidos": 0,
        "ventas_brutas_clp": 0.0,
        "ventas_netas_clp": 0.0,   # brutas / 1.19
        "total_iva_clp": 0.0,
        "total_descuentos_clp": 0.0,
        "pedidos_pagados": 0,
        "pedidos_reembolsados": 0,
    }

    for o in orders:
        financial = o.get("financial_status", "")
        if financial in ("voided",):
            continue

        bruto = float(o.get("total_price", 0))
        tax = float(o.get("total_tax", 0))
        subtotal = float(o.get("subtotal_price", 0))

        neto = round(bruto / 1.19, 2)

        # Shipping address commune
        addr = o.get("shipping_address") or {}
        comuna = (addr.get("city") or "").strip()

        # Line items
        items = []
        for li in o.get("line_items", []):
            items.append({
                "sku": li.get("sku") or "",
                "nombre": li.get("name") or "",
                "cantidad": int(li.get("quantity", 1)),
                "precio_unitario_bruto": float(li.get("price", 0)),
            })

        # Shopify shipping charged to customer
        envio_cobrado = sum(
            float(sl.get("price", 0)) for sl in o.get("shipping_lines", [])
        )

        pedido = {
            "id": o["id"],
            "nombre": o.get("name", ""),
            "fecha": o.get("created_at", "")[:10],
            "estado_pago": financial,
            "estado_fulfillment": o.get("fulfillment_status") or "unfulfilled",
            "venta_bruta_clp": bruto,
            "venta_neta_clp": neto,
            "iva_clp": tax,
            "envio_cobrado_clp": envio_cobrado,
            "comuna_destino": comuna,
            "items": items,
        }
        pedidos.append(pedido)

        totales["cantidad_pedidos"] += 1
        totales["ventas_brutas_clp"] += bruto
        totales["ventas_netas_clp"] += neto
        totales["total_iva_clp"] += tax

        if financial in ("paid", "partially_refunded"):
            totales["pedidos_pagados"] += 1
        if financial in ("refunded", "partially_refunded"):
            totales["pedidos_reembolsados"] += 1

    # Round totals
    for k in ("ventas_brutas_clp", "ventas_netas_clp", "total_iva_clp"):
        totales[k] = round(totales[k], 2)

    return pedidos, totales


def fetch_shopify_orders():
    token = os.environ.get("SHOPIFY_ACCESS_TOKEN")
    domain = get_shop_domain()

    if not token or not domain:
        print("Error: SHOPIFY_ACCESS_TOKEN y SHOPIFY_SHOP_DOMAIN requeridos.", file=sys.stderr)
        sys.exit(1)

    mode = os.environ.get("REPORT_MODE", "weekly")
    desde, hasta = get_period()

    print(f"Shopify Orders — modo={mode}  período={desde} al {hasta}")

    try:
        raw_orders = fetch_all_orders(domain, token, desde, hasta)
    except requests.HTTPError as exc:
        print(f"Error Shopify API: {exc}", file=sys.stderr)
        sys.exit(1)

    pedidos, totales = process_orders(raw_orders)

    output = {
        "meta": {
            "modo": mode,
            "periodo": {"desde": desde, "hasta": hasta},
            "generado": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "pedidos_raw": len(raw_orders),
        },
        "totales": totales,
        "pedidos": pedidos,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"✅ shopify_orders_data.json guardado — {totales['cantidad_pedidos']} pedidos")
    print(f"   Ventas brutas: ${totales['ventas_brutas_clp']:,.0f} CLP")
    print(f"   Ventas netas:  ${totales['ventas_netas_clp']:,.0f} CLP")


if __name__ == "__main__":
    fetch_shopify_orders()
