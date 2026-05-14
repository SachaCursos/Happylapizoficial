"""
shopify_sync_postgres.py
Sincroniza toda la data de Shopify (pedidos, productos, clientes) a PostgreSQL.
Uso: python shopify_sync_postgres.py

Tablas creadas/actualizadas:
  shopify_productos, shopify_variantes,
  shopify_pedidos, shopify_lineas_pedido,
  shopify_clientes

Requiere env vars:
  DATABASE_URL            — conexión PostgreSQL
  SHOPIFY_ACCESS_TOKEN    — Admin API access token
  SHOPIFY_STORE_URL       — ej. happy-lapiz.myshopify.com  (default)
"""

import os
import logging
import requests
from sqlalchemy import create_engine, text

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "")
SHOPIFY_STORE = os.getenv("SHOPIFY_STORE_URL", "happy-lapiz.myshopify.com")
SHOPIFY_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN", "")
SHOPIFY_API_VERSION = "2024-10"

GRAPHQL_URL = f"https://{SHOPIFY_STORE}/admin/api/{SHOPIFY_API_VERSION}/graphql.json"

if not DATABASE_URL:
    raise ValueError("Falta DATABASE_URL")
if not SHOPIFY_TOKEN:
    raise ValueError("Falta SHOPIFY_ACCESS_TOKEN")


# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

DDL = [
    """
    CREATE TABLE IF NOT EXISTS shopify_productos (
        shopify_id      TEXT PRIMARY KEY,
        titulo          TEXT,
        handle          TEXT,
        estado          TEXT,
        vendor          TEXT,
        tipo            TEXT,
        tags            TEXT,
        inventario_total INTEGER,
        imagen_url      TEXT,
        precio_min      NUMERIC,
        created_at      TIMESTAMPTZ,
        updated_at      TIMESTAMPTZ,
        synced_at       TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS shopify_variantes (
        shopify_id  TEXT PRIMARY KEY,
        product_id  TEXT,
        titulo      TEXT,
        sku         TEXT,
        precio      NUMERIC,
        inventario  INTEGER,
        created_at  TIMESTAMPTZ,
        updated_at  TIMESTAMPTZ
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS shopify_clientes (
        shopify_id       TEXT PRIMARY KEY,
        email            TEXT,
        nombre           TEXT,
        apellido         TEXT,
        telefono         TEXT,
        ciudad           TEXT,
        pais             TEXT,
        pedidos_count    INTEGER,
        total_gastado    NUMERIC,
        acepta_marketing BOOLEAN,
        created_at       TIMESTAMPTZ,
        updated_at       TIMESTAMPTZ,
        synced_at        TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS shopify_pedidos (
        shopify_id           TEXT PRIMARY KEY,
        order_number         TEXT,
        created_at           TIMESTAMPTZ,
        updated_at           TIMESTAMPTZ,
        processed_at         TIMESTAMPTZ,
        estado_financiero    TEXT,
        estado_fulfillment   TEXT,
        customer_id          TEXT,
        customer_email       TEXT,
        ciudad_envio         TEXT,
        pais_envio           TEXT,
        total_precio         NUMERIC,
        subtotal             NUMERIC,
        total_impuestos      NUMERIC,
        total_descuentos     NUMERIC,
        ventas_netas         NUMERIC,
        moneda               TEXT,
        synced_at            TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS shopify_lineas_pedido (
        shopify_id      TEXT PRIMARY KEY,
        order_id        TEXT,
        product_id      TEXT,
        variant_id      TEXT,
        titulo          TEXT,
        titulo_variante TEXT,
        sku             TEXT,
        cantidad        INTEGER,
        precio          NUMERIC,
        total_descuento NUMERIC,
        total_linea     NUMERIC
    )
    """,
]


# ---------------------------------------------------------------------------
# GraphQL helpers
# ---------------------------------------------------------------------------

PRODUCTS_QUERY = """
query Products($cursor: String) {
  products(first: 50, after: $cursor) {
    edges {
      node {
        id
        title
        handle
        status
        vendor
        productType
        tags
        totalInventory
        createdAt
        updatedAt
        featuredMedia {
          preview { image { url } }
        }
        priceRangeV2 { minVariantPrice { amount } }
        variants(first: 50) {
          edges {
            node {
              id
              title
              sku
              price
              inventoryQuantity
              createdAt
              updatedAt
            }
          }
        }
      }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""

ORDERS_QUERY = """
query Orders($cursor: String) {
  orders(first: 50, after: $cursor) {
    edges {
      node {
        id
        name
        createdAt
        updatedAt
        processedAt
        displayFinancialStatus
        displayFulfillmentStatus
        currencyCode
        customer { id email }
        email
        shippingAddress { city country }
        totalPriceSet       { shopMoney { amount } }
        subtotalPriceSet    { shopMoney { amount } }
        totalTaxSet         { shopMoney { amount } }
        totalDiscountsSet   { shopMoney { amount } }
        lineItems(first: 50) {
          edges {
            node {
              id
              title
              variantTitle
              sku
              quantity
              originalUnitPriceSet { shopMoney { amount } }
              totalDiscountSet     { shopMoney { amount } }
              product { id }
              variant { id }
            }
          }
        }
      }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""

CUSTOMERS_QUERY = """
query Customers($cursor: String) {
  customers(first: 50, after: $cursor) {
    edges {
      node {
        id
        email
        firstName
        lastName
        phone
        numberOfOrders
        amountSpent { amount }
        defaultAddress { city country }
        emailMarketingConsent { marketingState }
        createdAt
        updatedAt
      }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""


def gql(query: str, variables: dict | None = None) -> dict:
    headers = {
        "X-Shopify-Access-Token": SHOPIFY_TOKEN,
        "Content-Type": "application/json",
    }
    payload: dict = {"query": query}
    if variables:
        payload["variables"] = variables

    resp = requests.post(GRAPHQL_URL, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if "errors" in data:
        raise RuntimeError(f"GraphQL errors: {data['errors']}")
    return data["data"]


def gid(raw: str | None) -> str | None:
    """Extrae el ID numérico de un gid://shopify/Type/12345."""
    return raw.split("/")[-1] if raw else None


def money(node: dict, key: str) -> float:
    """Lee amount de un MoneyBag { shopMoney { amount } }."""
    try:
        return float(node[key]["shopMoney"]["amount"])
    except (KeyError, TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------------------
# Sync functions
# ---------------------------------------------------------------------------

def sync_products(conn) -> int:
    log.info("→ Productos…")
    cursor = None
    total = 0

    while True:
        data = gql(PRODUCTS_QUERY, {"cursor": cursor})
        edges = data["products"]["edges"]
        page_info = data["products"]["pageInfo"]

        for edge in edges:
            p = edge["node"]
            pid = gid(p["id"])

            img_url = None
            if p.get("featuredMedia") and p["featuredMedia"].get("preview"):
                img_url = p["featuredMedia"]["preview"]["image"]["url"]

            precio_min = None
            if p.get("priceRangeV2"):
                precio_min = float(p["priceRangeV2"]["minVariantPrice"]["amount"])

            conn.execute(text("""
                INSERT INTO shopify_productos
                    (shopify_id, titulo, handle, estado, vendor, tipo, tags,
                     inventario_total, imagen_url, precio_min, created_at, updated_at, synced_at)
                VALUES (:id, :titulo, :handle, :estado, :vendor, :tipo, :tags,
                        :inv, :img, :precio, :cat, :uat, NOW())
                ON CONFLICT (shopify_id) DO UPDATE SET
                    titulo           = EXCLUDED.titulo,
                    handle           = EXCLUDED.handle,
                    estado           = EXCLUDED.estado,
                    inventario_total = EXCLUDED.inventario_total,
                    precio_min       = EXCLUDED.precio_min,
                    imagen_url       = EXCLUDED.imagen_url,
                    updated_at       = EXCLUDED.updated_at,
                    synced_at        = NOW()
            """), {
                "id":     pid,
                "titulo": p["title"],
                "handle": p["handle"],
                "estado": p["status"],
                "vendor": p["vendor"],
                "tipo":   p.get("productType", ""),
                "tags":   ",".join(p.get("tags", [])),
                "inv":    p.get("totalInventory", 0),
                "img":    img_url,
                "precio": precio_min,
                "cat":    p["createdAt"],
                "uat":    p["updatedAt"],
            })

            for ve in p["variants"]["edges"]:
                v = ve["node"]
                conn.execute(text("""
                    INSERT INTO shopify_variantes
                        (shopify_id, product_id, titulo, sku, precio, inventario,
                         created_at, updated_at)
                    VALUES (:id, :pid, :titulo, :sku, :precio, :inv, :cat, :uat)
                    ON CONFLICT (shopify_id) DO UPDATE SET
                        titulo     = EXCLUDED.titulo,
                        sku        = EXCLUDED.sku,
                        precio     = EXCLUDED.precio,
                        inventario = EXCLUDED.inventario,
                        updated_at = EXCLUDED.updated_at
                """), {
                    "id":     gid(v["id"]),
                    "pid":    pid,
                    "titulo": v["title"],
                    "sku":    v.get("sku") or "",
                    "precio": float(v["price"]),
                    "inv":    v.get("inventoryQuantity", 0),
                    "cat":    v["createdAt"],
                    "uat":    v["updatedAt"],
                })

            total += 1

        if not page_info["hasNextPage"]:
            break
        cursor = page_info["endCursor"]

    log.info(f"  ✓ {total} productos")
    return total


def sync_orders(conn) -> int:
    log.info("→ Pedidos…")
    cursor = None
    total = 0

    while True:
        data = gql(ORDERS_QUERY, {"cursor": cursor})
        edges = data["orders"]["edges"]
        page_info = data["orders"]["pageInfo"]

        for edge in edges:
            o = edge["node"]
            oid = gid(o["id"])

            ship = o.get("shippingAddress") or {}
            cust = o.get("customer") or {}
            cust_id = gid(cust.get("id", "")) if cust else None
            cust_email = cust.get("email") or o.get("email", "")

            total_precio = money(o, "totalPriceSet")
            subtotal     = money(o, "subtotalPriceSet")
            impuestos    = money(o, "totalTaxSet")
            descuentos   = money(o, "totalDiscountsSet")
            # Ventas netas: precio bruto / 1.19 (IVA Chile)
            ventas_netas = total_precio / 1.19

            conn.execute(text("""
                INSERT INTO shopify_pedidos
                    (shopify_id, order_number, created_at, updated_at, processed_at,
                     estado_financiero, estado_fulfillment,
                     customer_id, customer_email,
                     ciudad_envio, pais_envio,
                     total_precio, subtotal, total_impuestos, total_descuentos,
                     ventas_netas, moneda, synced_at)
                VALUES
                    (:id, :num, :cat, :uat, :pat,
                     :fin, :ful,
                     :cid, :cem,
                     :ciudad, :pais,
                     :total, :sub, :imp, :desc,
                     :neto, :mon, NOW())
                ON CONFLICT (shopify_id) DO UPDATE SET
                    estado_financiero  = EXCLUDED.estado_financiero,
                    estado_fulfillment = EXCLUDED.estado_fulfillment,
                    total_precio       = EXCLUDED.total_precio,
                    subtotal           = EXCLUDED.subtotal,
                    total_impuestos    = EXCLUDED.total_impuestos,
                    total_descuentos   = EXCLUDED.total_descuentos,
                    ventas_netas       = EXCLUDED.ventas_netas,
                    updated_at         = EXCLUDED.updated_at,
                    synced_at          = NOW()
            """), {
                "id":     oid,
                "num":    o["name"],
                "cat":    o["createdAt"],
                "uat":    o["updatedAt"],
                "pat":    o.get("processedAt"),
                "fin":    o.get("displayFinancialStatus", ""),
                "ful":    o.get("displayFulfillmentStatus", ""),
                "cid":    cust_id,
                "cem":    cust_email,
                "ciudad": ship.get("city", ""),
                "pais":   ship.get("country", ""),
                "total":  total_precio,
                "sub":    subtotal,
                "imp":    impuestos,
                "desc":   descuentos,
                "neto":   ventas_netas,
                "mon":    o.get("currencyCode", "CLP"),
            })

            for le in o["lineItems"]["edges"]:
                li = le["node"]
                unit_price  = money(li, "originalUnitPriceSet")
                total_disc  = money(li, "totalDiscountSet")
                total_linea = unit_price * li["quantity"] - total_disc

                conn.execute(text("""
                    INSERT INTO shopify_lineas_pedido
                        (shopify_id, order_id, product_id, variant_id,
                         titulo, titulo_variante, sku, cantidad,
                         precio, total_descuento, total_linea)
                    VALUES
                        (:id, :oid, :pid, :vid,
                         :tit, :var, :sku, :qty,
                         :precio, :desc, :total)
                    ON CONFLICT (shopify_id) DO UPDATE SET
                        cantidad        = EXCLUDED.cantidad,
                        precio          = EXCLUDED.precio,
                        total_descuento = EXCLUDED.total_descuento,
                        total_linea     = EXCLUDED.total_linea
                """), {
                    "id":     gid(li["id"]),
                    "oid":    oid,
                    "pid":    gid(li["product"]["id"]) if li.get("product") else None,
                    "vid":    gid(li["variant"]["id"]) if li.get("variant") else None,
                    "tit":    li["title"],
                    "var":    li.get("variantTitle") or "",
                    "sku":    li.get("sku") or "",
                    "qty":    li["quantity"],
                    "precio": unit_price,
                    "desc":   total_disc,
                    "total":  total_linea,
                })

            total += 1

        if not page_info["hasNextPage"]:
            break
        cursor = page_info["endCursor"]

    log.info(f"  ✓ {total} pedidos")
    return total


def sync_customers(conn) -> int:
    log.info("→ Clientes…")
    cursor = None
    total = 0

    while True:
        data = gql(CUSTOMERS_QUERY, {"cursor": cursor})
        edges = data["customers"]["edges"]
        page_info = data["customers"]["pageInfo"]

        for edge in edges:
            c = edge["node"]
            addr    = c.get("defaultAddress") or {}
            mkt     = c.get("emailMarketingConsent") or {}
            acepta  = mkt.get("marketingState") == "SUBSCRIBED"
            gastado = float(c["amountSpent"]["amount"]) if c.get("amountSpent") else 0.0

            conn.execute(text("""
                INSERT INTO shopify_clientes
                    (shopify_id, email, nombre, apellido, telefono,
                     ciudad, pais, pedidos_count, total_gastado,
                     acepta_marketing, created_at, updated_at, synced_at)
                VALUES
                    (:id, :email, :nom, :ap, :tel,
                     :ciudad, :pais, :orders, :spent,
                     :mkt, :cat, :uat, NOW())
                ON CONFLICT (shopify_id) DO UPDATE SET
                    email            = EXCLUDED.email,
                    nombre           = EXCLUDED.nombre,
                    apellido         = EXCLUDED.apellido,
                    pedidos_count    = EXCLUDED.pedidos_count,
                    total_gastado    = EXCLUDED.total_gastado,
                    acepta_marketing = EXCLUDED.acepta_marketing,
                    updated_at       = EXCLUDED.updated_at,
                    synced_at        = NOW()
            """), {
                "id":     gid(c["id"]),
                "email":  c.get("email", ""),
                "nom":    c.get("firstName") or "",
                "ap":     c.get("lastName") or "",
                "tel":    c.get("phone") or "",
                "ciudad": addr.get("city", ""),
                "pais":   addr.get("country", ""),
                "orders": c.get("numberOfOrders", 0),
                "spent":  gastado,
                "mkt":    acepta,
                "cat":    c["createdAt"],
                "uat":    c["updatedAt"],
            })
            total += 1

        if not page_info["hasNextPage"]:
            break
        cursor = page_info["endCursor"]

    log.info(f"  ✓ {total} clientes")
    return total


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_sync():
    engine = create_engine(DATABASE_URL)

    log.info("Creando tablas si no existen…")
    with engine.begin() as conn:
        for ddl in DDL:
            conn.execute(text(ddl.strip()))
    log.info("✓ Tablas listas\n")

    with engine.begin() as conn:
        sync_products(conn)

    with engine.begin() as conn:
        sync_orders(conn)

    with engine.begin() as conn:
        sync_customers(conn)

    log.info("\n✓ Sync completo.")


if __name__ == "__main__":
    run_sync()
