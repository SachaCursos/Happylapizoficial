"""
Cálculo de costo de envío BluExpress por pedido.

Lógica:
  peso_volumetrico_g = (largo_cm * ancho_cm * alto_cm) / 4000 * 1000
  peso_efectivo_g    = max(peso_fisico_g, peso_volumetrico_g)
  peso_efectivo_kg   = peso_efectivo_g / 1000

  El costo se busca en blueexpress_tarifario_hd por comuna de destino
  y tramo de peso usando la función calcular_envio_hd(comuna, kg).
"""

from __future__ import annotations
from sqlalchemy import text
from sqlalchemy.engine import Engine


TRAMOS = [
    ("kg_0_05",   0,    0.5),
    ("kg_05_15",  0.5,  1.5),
    ("kg_15_3",   1.5,  3.0),
    ("kg_3_6",    3.0,  6.0),
    ("kg_6_10",   6.0,  10.0),
    ("kg_10_16",  10.0, 16.0),
    ("kg_16_25",  16.0, 25.0),
]


def _tarifa_por_peso(row: dict, peso_kg: float) -> int | None:
    """Calcula tarifa dado un row de blueexpress_tarifario_hd y el peso en kg."""
    if peso_kg <= 0.5:
        return row["kg_0_05"]
    elif peso_kg <= 1.5:
        return row["kg_05_15"]
    elif peso_kg <= 3:
        return row["kg_15_3"]
    elif peso_kg <= 6:
        return row["kg_3_6"]
    elif peso_kg <= 10:
        return row["kg_6_10"]
    elif peso_kg <= 16:
        return row["kg_10_16"]
    elif peso_kg <= 25:
        return row["kg_16_25"]
    elif peso_kg <= 50:
        xkg = row["kg_25_50_xkg"] or 0
        return int((row["kg_16_25"] or 0) + (peso_kg - 25) * xkg)
    else:
        xkg_a = row["kg_25_50_xkg"] or 0
        xkg_b = row["kg_mas50_xkg"] or 0
        return int((row["kg_16_25"] or 0) + 25 * xkg_a + (peso_kg - 50) * xkg_b)


def calcular_costo_envio_pedido(engine: Engine, order_id: str) -> dict:
    """
    Calcula el costo de envío real de un pedido basado en:
    - Productos del pedido (shopify_lineas_pedido)
    - Dimensiones y peso físico (shopify_productos)
    - Tarifario BluExpress HD (blueexpress_tarifario_hd)

    Retorna dict con desglose completo o error si falta data.
    """
    with engine.connect() as conn:
        # 1. Obtener pedido y comuna destino
        pedido = conn.execute(text("""
            SELECT shopify_id, order_number, created_at, ciudad_envio, total_precio
            FROM shopify_pedidos
            WHERE shopify_id = :oid
        """), {"oid": order_id}).fetchone()

        if not pedido:
            return {"error": f"Pedido {order_id} no encontrado"}

        comuna_destino = (pedido[3] or "").strip().upper()

        # 2. Obtener líneas del pedido
        lineas = conn.execute(text("""
            SELECT lp.titulo, lp.sku, lp.cantidad, lp.precio, lp.total_linea,
                   sp.largo_cm, sp.ancho_cm, sp.alto_cm, sp.peso_fisico_g
            FROM shopify_lineas_pedido lp
            LEFT JOIN shopify_productos sp
                ON UPPER(TRIM(sp.titulo)) = UPPER(TRIM(lp.titulo))
            WHERE lp.order_id = :oid
        """), {"oid": order_id}).fetchall()

        if not lineas:
            return {"error": f"Sin líneas de pedido para {order_id}"}

        # 3. Calcular peso total del pedido
        items_detalle = []
        peso_fisico_total_g = 0.0
        peso_volumetrico_total_g = 0.0
        sin_dimensiones = []

        for linea in lineas:
            titulo, sku, cantidad, precio, total_linea, largo, ancho, alto, peso_g = linea
            cantidad = cantidad or 1

            tiene_dimensiones = all(v is not None for v in [largo, ancho, alto, peso_g])

            if tiene_dimensiones:
                peso_vol_unitario = (float(largo) * float(ancho) * float(alto)) / 4000 * 1000
                peso_fis_unitario = float(peso_g)
                peso_efectivo_unitario = max(peso_fis_unitario, peso_vol_unitario)

                peso_fisico_total_g += peso_fis_unitario * cantidad
                peso_volumetrico_total_g += peso_vol_unitario * cantidad

                items_detalle.append({
                    "titulo": titulo,
                    "sku": sku,
                    "cantidad": cantidad,
                    "largo_cm": float(largo),
                    "ancho_cm": float(ancho),
                    "alto_cm": float(alto),
                    "peso_fisico_g": float(peso_g),
                    "peso_volumetrico_g": round(peso_vol_unitario, 1),
                    "peso_efectivo_g": round(peso_efectivo_unitario, 1),
                    "peso_efectivo_total_g": round(peso_efectivo_unitario * cantidad, 1),
                })
            else:
                sin_dimensiones.append(titulo)
                items_detalle.append({
                    "titulo": titulo,
                    "sku": sku,
                    "cantidad": cantidad,
                    "largo_cm": None,
                    "ancho_cm": None,
                    "alto_cm": None,
                    "peso_fisico_g": None,
                    "peso_volumetrico_g": None,
                    "peso_efectivo_g": None,
                    "peso_efectivo_total_g": None,
                    "advertencia": "Sin dimensiones — no incluido en peso total",
                })

        peso_efectivo_total_g = max(peso_fisico_total_g, peso_volumetrico_total_g)
        peso_efectivo_kg = peso_efectivo_total_g / 1000

        # 4. Buscar tarifa en BluExpress
        tarifa_row = conn.execute(text("""
            SELECT kg_0_05, kg_05_15, kg_15_3, kg_3_6, kg_6_10,
                   kg_10_16, kg_16_25, kg_25_50_xkg, kg_mas50_xkg, region, posta
            FROM blueexpress_tarifario_hd
            WHERE UPPER(TRIM(comuna)) = :comuna
            LIMIT 1
        """), {"comuna": comuna_destino}).fetchone()

        if not tarifa_row:
            costo_envio = None
            tarifa_info = None
            advertencia_tarifa = f"Comuna '{comuna_destino}' no encontrada en tarifario BluExpress"
        else:
            t = dict(zip(
                ["kg_0_05","kg_05_15","kg_15_3","kg_3_6","kg_6_10",
                 "kg_10_16","kg_16_25","kg_25_50_xkg","kg_mas50_xkg","region","posta"],
                tarifa_row
            ))
            costo_envio = _tarifa_por_peso(t, peso_efectivo_kg)
            tarifa_info = {"region": t["region"], "posta": t["posta"], "comuna": comuna_destino}
            advertencia_tarifa = None

    return {
        "order_id": order_id,
        "order_number": pedido[1],
        "fecha": str(pedido[2]),
        "comuna_destino": comuna_destino,
        "tarifa": tarifa_info,
        "peso_fisico_total_g": round(peso_fisico_total_g, 1),
        "peso_volumetrico_total_g": round(peso_volumetrico_total_g, 1),
        "peso_efectivo_total_g": round(peso_efectivo_total_g, 1),
        "peso_efectivo_kg": round(peso_efectivo_kg, 3),
        "costo_envio_clp": costo_envio,
        "productos_sin_dimensiones": sin_dimensiones,
        "advertencia_tarifa": advertencia_tarifa,
        "lineas": items_detalle,
    }


def resumen_envios_pedidos(engine: Engine, desde: str | None = None, hasta: str | None = None) -> list[dict]:
    """
    Retorna costo de envío estimado para todos los pedidos en el rango.
    Solo incluye pedidos donde la comuna está en el tarifario.
    """
    filtros = []
    params: dict = {}
    if desde:
        filtros.append("p.created_at >= :desde")
        params["desde"] = desde
    if hasta:
        filtros.append("p.created_at <= :hasta")
        params["hasta"] = hasta
    where = f"WHERE {' AND '.join(filtros)}" if filtros else ""

    with engine.connect() as conn:
        # Verificar que la tabla tarifario existe
        existe = conn.execute(text(
            "SELECT EXISTS(SELECT FROM information_schema.tables WHERE table_name='blueexpress_tarifario_hd')"
        )).scalar()
        if not existe:
            return []

        rows = conn.execute(text(f"""
            SELECT
                p.shopify_id,
                p.created_at,
                p.ciudad_envio,
                UPPER(TRIM(p.ciudad_envio)) AS comuna_upper,
                p.total_precio
            FROM shopify_pedidos p
            {where}
            ORDER BY p.created_at DESC
        """), params).fetchall()

    resultados = []
    for row in rows:
        oid, fecha, ciudad, comuna_upper, total = row
        r = calcular_costo_envio_pedido(engine, oid)
        if r.get("costo_envio_clp") is not None:
            resultados.append({
                "order_id": oid,
                "fecha": str(fecha),
                "ciudad_envio": ciudad,
                "peso_efectivo_kg": r["peso_efectivo_kg"],
                "costo_envio_clp": r["costo_envio_clp"],
                "total_pedido_clp": float(total or 0),
                "productos_sin_dimensiones": r["productos_sin_dimensiones"],
            })

    return resultados
