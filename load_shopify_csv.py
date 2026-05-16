"""
load_shopify_csv.py
Carga el CSV histórico de ventas Shopify (2021-2026) a PostgreSQL.

Uso:
    DATABASE_URL=postgresql://... python load_shopify_csv.py <ruta_csv>

Tablas cargadas (con TRUNCATE + reload limpio):
    shopify_ventas_historico  — filas 2021-2024 (formato legado P&L)
    shopify_ventas_2025       — filas 2025+     (formato legado P&L)
    shopify_pedidos           — 1 fila por pedido, total agregado
    shopify_lineas_pedido     — 1 fila por línea de producto
"""

import os
import sys
import csv
import logging
from collections import defaultdict
from sqlalchemy import create_engine, text

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "")
if not DATABASE_URL:
    sys.exit("ERROR: falta variable de entorno DATABASE_URL")

if len(sys.argv) < 2:
    sys.exit(f"Uso: python {sys.argv[0]} <ruta_del_csv>")

CSV_PATH = sys.argv[1]

# ---------------------------------------------------------------------------
# DDL (crea tablas si no existen)
# ---------------------------------------------------------------------------

DDL = """
CREATE TABLE IF NOT EXISTS shopify_ventas_historico (
    id                                   SERIAL PRIMARY KEY,
    nombre_del_producto                  TEXT,
    precio_de_la_variante_de_producto    NUMERIC,
    dia                                  DATE,
    nombre_del_cliente                   TEXT,
    ciudad_del_envio                     TEXT,
    ventas_totales                       NUMERIC
);

CREATE TABLE IF NOT EXISTS shopify_ventas_2025 (
    id                                   SERIAL PRIMARY KEY,
    titulo_del_producto                  TEXT,
    precio_de_la_variante_de_producto    NUMERIC,
    dia                                  DATE,
    nombre_del_cliente                   TEXT,
    ciudad_del_envio                     TEXT,
    id_de_pedido                         TEXT,
    ventas_totales                       NUMERIC,
    anio                                 INTEGER
);

CREATE TABLE IF NOT EXISTS shopify_pedidos (
    shopify_id          TEXT PRIMARY KEY,
    order_number        TEXT,
    created_at          DATE,
    customer_email      TEXT,
    ciudad_envio        TEXT,
    total_precio        NUMERIC,
    ventas_netas        NUMERIC,
    moneda              TEXT DEFAULT 'CLP',
    synced_at           TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS shopify_lineas_pedido (
    shopify_id          TEXT PRIMARY KEY,
    order_id            TEXT,
    product_id          TEXT,
    variant_id          TEXT,
    titulo              TEXT,
    titulo_variante     TEXT,
    sku                 TEXT,
    cantidad            INTEGER,
    precio              NUMERIC,
    total_descuento     NUMERIC DEFAULT 0,
    total_linea         NUMERIC
);
"""

# ---------------------------------------------------------------------------
# Parseo del CSV
# ---------------------------------------------------------------------------

def parse_csv(path: str):
    """
    Lee el CSV y devuelve dos estructuras:
      rows       — lista de dicts con todos los campos (para las tablas legado)
      orders_agg — dict order_id → {fecha, cliente, ciudad, total}
      line_items — lista de dicts solo para filas con producto
    """
    rows = []
    orders_agg: dict = defaultdict(lambda: {"fecha": None, "cliente": "", "ciudad": "", "total": 0.0, "lineas": 0})
    line_items = []

    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, start=1):
            product  = (row.get("Product title") or "").strip()
            price_s  = (row.get("Product variant price") or "").strip()
            day      = (row.get("Day") or "").strip()
            customer = (row.get("Customer name") or "").strip()
            city     = (row.get("Shipping city") or "").strip()
            order_id = (row.get("Order ID") or "").strip()
            total_s  = (row.get("Total sales") or "").strip()

            if not day or not order_id:
                continue

            price = float(price_s) if price_s else None
            total = float(total_s) if total_s else 0.0
            year  = int(day[:4])

            rows.append({
                "product":  product or None,
                "price":    price,
                "day":      day,
                "customer": customer,
                "city":     city,
                "order_id": order_id,
                "total":    total,
                "year":     year,
            })

            # Agrega al pedido (total real = suma de todas las líneas del pedido)
            agg = orders_agg[order_id]
            agg["total"] += total
            agg["lineas"] += 1
            if agg["fecha"] is None:
                agg["fecha"]   = day
                agg["cliente"] = customer
                agg["ciudad"]  = city

            # Líneas de producto (excluye filas de envío/fee sin título)
            if product:
                line_items.append({
                    "id":       f"{order_id}-{i}",
                    "order_id": order_id,
                    "titulo":   product,
                    "precio":   price or 0.0,
                    "total":    total,
                })

    return rows, orders_agg, line_items


# ---------------------------------------------------------------------------
# Carga a PostgreSQL
# ---------------------------------------------------------------------------

BATCH = 500   # filas por INSERT batch


def load_legado(conn, rows):
    """Carga shopify_ventas_historico (pre-2025) y shopify_ventas_2025."""
    log.info("Limpiando tablas legado…")
    conn.execute(text("TRUNCATE shopify_ventas_historico RESTART IDENTITY"))
    conn.execute(text("TRUNCATE shopify_ventas_2025 RESTART IDENTITY"))

    hist = [r for r in rows if r["year"] < 2025]
    new_ = [r for r in rows if r["year"] >= 2025]

    log.info(f"  shopify_ventas_historico: {len(hist):,} filas")
    for i in range(0, len(hist), BATCH):
        batch = hist[i:i + BATCH]
        conn.execute(
            text("""
                INSERT INTO shopify_ventas_historico
                    (nombre_del_producto, precio_de_la_variante_de_producto,
                     dia, nombre_del_cliente, ciudad_del_envio, ventas_totales)
                VALUES
                    (:prod, :precio, :dia, :cliente, :ciudad, :total)
            """),
            [{"prod": r["product"], "precio": r["price"],
              "dia": r["day"], "cliente": r["customer"],
              "ciudad": r["city"], "total": r["total"]} for r in batch],
        )

    log.info(f"  shopify_ventas_2025: {len(new_):,} filas")
    for i in range(0, len(new_), BATCH):
        batch = new_[i:i + BATCH]
        conn.execute(
            text("""
                INSERT INTO shopify_ventas_2025
                    (titulo_del_producto, precio_de_la_variante_de_producto,
                     dia, nombre_del_cliente, ciudad_del_envio,
                     id_de_pedido, ventas_totales, anio)
                VALUES
                    (:prod, :precio, :dia, :cliente, :ciudad,
                     :order_id, :total, :year)
            """),
            [{"prod": r["product"], "precio": r["price"],
              "dia": r["day"], "cliente": r["customer"],
              "ciudad": r["city"], "order_id": r["order_id"],
              "total": r["total"], "year": r["year"]} for r in batch],
        )


def load_pedidos(conn, orders_agg):
    log.info(f"  shopify_pedidos: {len(orders_agg):,} pedidos únicos")
    conn.execute(text("TRUNCATE shopify_pedidos CASCADE"))

    items = list(orders_agg.items())
    for i in range(0, len(items), BATCH):
        batch = items[i:i + BATCH]
        conn.execute(
            text("""
                INSERT INTO shopify_pedidos
                    (shopify_id, order_number, created_at,
                     ciudad_envio, total_precio, ventas_netas, moneda)
                VALUES
                    (:id, :num, :fecha,
                     :ciudad, :total, :neto, 'CLP')
                ON CONFLICT (shopify_id) DO NOTHING
            """),
            [{"id": oid, "num": oid, "fecha": agg["fecha"],
              "ciudad": agg["ciudad"],
              "total": round(agg["total"], 2),
              "neto":  round(agg["total"] / 1.19, 2)}
             for oid, agg in batch],
        )


def load_lineas(conn, line_items):
    log.info(f"  shopify_lineas_pedido: {len(line_items):,} líneas de producto")
    conn.execute(text("TRUNCATE shopify_lineas_pedido"))

    for i in range(0, len(line_items), BATCH):
        batch = line_items[i:i + BATCH]
        conn.execute(
            text("""
                INSERT INTO shopify_lineas_pedido
                    (shopify_id, order_id, titulo, precio, total_linea)
                VALUES
                    (:id, :oid, :titulo, :precio, :total)
                ON CONFLICT (shopify_id) DO NOTHING
            """),
            [{"id": li["id"], "oid": li["order_id"],
              "titulo": li["titulo"], "precio": li["precio"],
              "total": li["total"]} for li in batch],
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    log.info(f"Leyendo {CSV_PATH}…")
    rows, orders_agg, line_items = parse_csv(CSV_PATH)
    log.info(f"  {len(rows):,} filas | {len(orders_agg):,} pedidos | {len(line_items):,} líneas de producto")
    log.info(f"  Rango: {min(r['day'] for r in rows)} → {max(r['day'] for r in rows)}")

    engine = create_engine(DATABASE_URL)

    log.info("\nCreando tablas si no existen…")
    with engine.begin() as conn:
        for stmt in DDL.split(";"):
            s = stmt.strip()
            if s:
                conn.execute(text(s))

    log.info("\nCargando datos…")
    with engine.begin() as conn:
        load_legado(conn, rows)

    with engine.begin() as conn:
        load_pedidos(conn, orders_agg)

    with engine.begin() as conn:
        load_lineas(conn, line_items)

    log.info("\n✓ Carga completa.")
    log.info(f"  shopify_ventas_historico + shopify_ventas_2025: {len(rows):,} filas")
    log.info(f"  shopify_pedidos:           {len(orders_agg):,} pedidos")
    log.info(f"  shopify_lineas_pedido:     {len(line_items):,} líneas")


if __name__ == "__main__":
    main()
