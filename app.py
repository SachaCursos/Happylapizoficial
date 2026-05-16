"""
Happy Lapiz — Financial Dashboard
FastAPI entry point.
"""

from __future__ import annotations

import os
import json
import shutil
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd
from fastapi import FastAPI, File, UploadFile, Query, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine, text

from src.calc.pnl_shopify import calcular_pnl_shopify
from src.calc.pnl_ml import calcular_pnl_ml
from src.calc.kpis import check_alerts, classify_product, BREAK_EVEN_MENSUAL_NETO
from src.calc.costos_fijos import TOTAL_CF_MENSUAL
from src.ingestion.meta_ads import parse_meta_json, match_campana_producto, get_latest_meta_json
from src.ingestion.mercadolibre import parse_ml_xlsx, load_ml_to_db
from src.ingestion.cartola import parse_cartola_xlsx, load_cartola_to_db
from src.reports.dashboard import render_dashboard
from src.reports.email_report import reporte_diario, reporte_semanal, reporte_mensual

DATABASE_URL = os.getenv("DATABASE_URL", "")
UPLOADS_DIR = Path("uploads")
UPLOADS_DIR.mkdir(exist_ok=True)

app = FastAPI(title="Happy Lapiz Dashboard", version="1.0.0")
app.mount("/static", StaticFiles(directory="static"), name="static")


def get_engine():
    if not DATABASE_URL:
        raise HTTPException(status_code=500, detail="DATABASE_URL no configurada")
    return create_engine(DATABASE_URL)


def _current_period() -> tuple[int, int]:
    now = date.today()
    return now.year, now.month


def _get_keywords_df(engine) -> pd.DataFrame:
    try:
        return pd.read_sql("SELECT * FROM marketing_keywords", engine)
    except Exception:
        return pd.DataFrame(columns=["producto", "palabras", "prioridad"])


def _build_campanas_context(engine) -> list[dict]:
    """Build campaign context from latest Meta Ads JSON or DB."""
    json_path = get_latest_meta_json()
    keywords_df = _get_keywords_df(engine)
    campanas = []

    if json_path:
        try:
            meta_data = parse_meta_json(json_path)
            df_c = meta_data.get("campanas", pd.DataFrame())
            for _, row in df_c.iterrows():
                nombre = str(row.get("nombre", ""))
                gasto = float(row.get("gasto_clp", 0))
                ingresos = float(row.get("ingresos_clp", 0))
                roas = float(row.get("roas", 0))
                producto = match_campana_producto(nombre, keywords_df)
                margen_pct = ((ingresos - gasto) / ingresos * 100) if ingresos > 0 else 0
                senal = classify_product(margen_pct, volumen_relevante=gasto > 50000)
                campanas.append({
                    "nombre": nombre,
                    "producto": producto,
                    "gasto_clp": round(gasto),
                    "ingresos_clp": round(ingresos),
                    "roas": round(roas, 2),
                    "senal": senal,
                })
        except Exception:
            pass

    return campanas


def _build_recomendaciones(pnl_shopify: dict, pnl_ml: dict, alertas: list) -> dict:
    hoy = []
    semana = []
    mes = []

    if pnl_shopify.get("roas", 0) > 0 and pnl_shopify["roas"] < 3.5:
        hoy.append(f"ROAS {pnl_shopify['roas']:.2f}x bajo umbral: revisar segmentación y creativos Meta Ads.")

    if pnl_ml.get("incumplimiento_full", 0) > 0:
        hoy.append("Multa de incumplimiento Full ML detectada. Revisar inventario en bodega ML.")

    if pnl_shopify.get("pct_meta_ventas", 0) > 28:
        semana.append("Gasto Meta Ads supera 28% de ventas netas. Considerar pausar conjuntos de anuncios ineficientes.")

    if pnl_ml.get("pct_product_ads", 0) > 15:
        semana.append("Product Ads ML supera 15% de ventas netas ML. Revisar presupuesto de publicidad interna.")

    if pnl_shopify.get("resultado_operativo", 0) < 0:
        semana.append("Resultado operativo negativo. Identificar línea de mayor impacto y reducir gasto variable.")

    if pnl_shopify.get("ventas_netas", 0) < BREAK_EVEN_MENSUAL_NETO:
        mes.append(f"Ventas netas bajo break-even mensual (${BREAK_EVEN_MENSUAL_NETO:,.0f}). Evaluar estrategia de escalamiento.")

    if not hoy and not semana and not mes:
        mes.append("Todos los indicadores dentro de rangos normales. Continuar monitoreo semanal.")

    return {"hoy": hoy, "semana": semana, "mes": mes}


DATA_FALTANTE_DEFAULT = [
    {"dato": "COGS real por pedido Shopify 2026", "impacto": "Alto — margen bruto estimado con mix promedio", "como": "Exportar líneas de pedido desde Shopify admin"},
    {"dato": "Peso real productos pendientes", "impacto": "Medio — estimación BluExpress por peso volumétrico", "como": "Pesar físicamente SKUs sin dato"},
    {"dato": "Ventas ML dic2025 y anteriores", "impacto": "Alto — historial ML incompleto pre-nov2025", "como": "Descargar reportes históricos desde panel ML"},
    {"dato": "Token Meta Ads actualizado", "impacto": "Crítico si expirado — sin datos campañas recientes", "como": "Regenerar desde meta_ads_happylapiz.html cada 60 días"},
    {"dato": "Tarifario BluExpress actualizado", "impacto": "Medio — puede haber variación en tarifas 2026", "como": "Solicitar tarifario actualizado a ejecutivo BluExpress"},
]


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    """Main interactive dashboard."""
    try:
        engine = get_engine()
    except HTTPException:
        return HTMLResponse("<h2>Error: DATABASE_URL no configurada.</h2>", status_code=500)

    anio, mes = _current_period()
    pnl_shopify = calcular_pnl_shopify(engine, anio, mes)
    pnl_ml = calcular_pnl_ml(engine, anio, mes)
    alertas = check_alerts(pnl_shopify, pnl_ml)
    campanas = _build_campanas_context(engine)
    recs = _build_recomendaciones(pnl_shopify, pnl_ml, alertas)

    periodo = date.today().strftime("%B %Y")

    html = render_dashboard({
        "periodo": periodo,
        "pnl_shopify": pnl_shopify,
        "pnl_ml": pnl_ml,
        "alertas": alertas,
        "campanas": campanas,
        "recomendaciones_hoy": recs["hoy"],
        "recomendaciones_semana": recs["semana"],
        "recomendaciones_mes": recs["mes"],
        "data_faltante": DATA_FALTANTE_DEFAULT,
    })
    return HTMLResponse(html)


@app.get("/api/pnl/shopify")
async def api_pnl_shopify(
    desde: str = Query(default=None, description="YYYY-MM"),
    hasta: str = Query(default=None, description="YYYY-MM"),
):
    engine = get_engine()
    anio, mes = _current_period()
    if desde:
        try:
            anio, mes = int(desde[:4]), int(desde[5:7])
        except ValueError:
            raise HTTPException(400, "Formato inválido. Usar YYYY-MM")

    return calcular_pnl_shopify(engine, anio, mes)


@app.get("/api/pnl/ml")
async def api_pnl_ml(
    desde: str = Query(default=None, description="YYYY-MM"),
):
    engine = get_engine()
    anio, mes = _current_period()
    if desde:
        try:
            anio, mes = int(desde[:4]), int(desde[5:7])
        except ValueError:
            raise HTTPException(400, "Formato inválido. Usar YYYY-MM")

    return calcular_pnl_ml(engine, anio, mes)


@app.get("/api/kpis")
async def api_kpis():
    engine = get_engine()
    anio, mes = _current_period()
    pnl_shopify = calcular_pnl_shopify(engine, anio, mes)
    pnl_ml = calcular_pnl_ml(engine, anio, mes)
    alertas = check_alerts(pnl_shopify, pnl_ml)
    return {
        "pnl_shopify": pnl_shopify,
        "pnl_ml": pnl_ml,
        "alertas": alertas,
        "break_even_mensual": BREAK_EVEN_MENSUAL_NETO,
        "costos_fijos_mensual": TOTAL_CF_MENSUAL,
    }


@app.get("/api/campaigns")
async def api_campaigns():
    engine = get_engine()
    campanas = _build_campanas_context(engine)
    return {"campanas": campanas}


@app.post("/upload/meta-json")
async def upload_meta_json(file: UploadFile = File(...)):
    if not file.filename.endswith(".json"):
        raise HTTPException(400, "Debe ser un archivo .json")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = UPLOADS_DIR / f"meta_ads_{timestamp}.json"
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    try:
        data = parse_meta_json(dest)
        n_campanas = len(data.get("campanas", pd.DataFrame()))
        n_dias = len(data.get("gasto_diario", pd.DataFrame()))
        return {"mensaje": f"JSON procesado: {n_campanas} campañas, {n_dias} días de gasto", "archivo": str(dest)}
    except Exception as e:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, f"Error al parsear JSON: {str(e)}")


@app.post("/upload/ml-report")
async def upload_ml_report(
    file: UploadFile = File(...),
    mes: int = Query(..., ge=1, le=12),
    anio: int = Query(..., ge=2020, le=2030),
):
    if not file.filename.endswith(".xlsx"):
        raise HTTPException(400, "Debe ser un archivo .xlsx")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = UPLOADS_DIR / f"ml_report_{anio}_{mes:02d}_{timestamp}.xlsx"
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    try:
        engine = get_engine()
        df = parse_ml_xlsx(dest, mes, anio)
        rows = load_ml_to_db(df, engine)
        return {"mensaje": f"Cargadas {rows} filas ML para {mes}/{anio}", "archivo": str(dest)}
    except Exception as e:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, f"Error al procesar xlsx ML: {str(e)}")


@app.get("/admin/upload", response_class=HTMLResponse)
async def admin_upload_page():
    return HTMLResponse("""<!DOCTYPE html>
<html lang="es">
<head><meta charset="UTF-8"><title>Carga CSV Shopify</title>
<style>
  body{font-family:sans-serif;max-width:600px;margin:60px auto;padding:0 20px;background:#0f0f0f;color:#eee}
  h2{color:#a78bfa}
  .card{background:#1a1a2e;border:1px solid #333;border-radius:12px;padding:30px}
  label{display:block;margin-bottom:8px;color:#ccc}
  input[type=file]{width:100%;padding:10px;background:#111;border:1px dashed #555;border-radius:8px;color:#eee;cursor:pointer;box-sizing:border-box}
  button{margin-top:20px;width:100%;padding:14px;background:#7c3aed;color:#fff;border:none;border-radius:8px;font-size:16px;cursor:pointer}
  button:hover{background:#6d28d9}
  #status{margin-top:20px;padding:15px;border-radius:8px;display:none}
  .ok{background:#052e16;border:1px solid #16a34a;color:#86efac}
  .err{background:#2d0505;border:1px solid #dc2626;color:#fca5a5}
  pre{white-space:pre-wrap;word-break:break-all;font-size:13px;margin:0}
</style>
</head>
<body>
<h2>Carga Histórico Shopify → PostgreSQL</h2>
<div class="card">
  <label>Selecciona el CSV de ventas Shopify (2020-2026):</label>
  <input type="file" id="csvFile" accept=".csv">
  <button onclick="upload()">Cargar a PostgreSQL</button>
  <div id="status"></div>
</div>
<script>
async function upload() {
  const file = document.getElementById('csvFile').files[0];
  if (!file) { alert('Selecciona un archivo CSV primero'); return; }
  const btn = document.querySelector('button');
  const status = document.getElementById('status');
  btn.textContent = 'Cargando... (puede tardar 30-60 seg)';
  btn.disabled = true;
  status.style.display = 'none';
  const fd = new FormData();
  fd.append('file', file);
  try {
    const r = await fetch('/upload/shopify-historico', {method:'POST', body:fd});
    const j = await r.json();
    status.className = r.ok ? 'ok' : 'err';
    status.style.display = 'block';
    status.innerHTML = '<pre>' + JSON.stringify(j, null, 2) + '</pre>';
  } catch(e) {
    status.className = 'err';
    status.style.display = 'block';
    status.innerHTML = '<pre>Error: ' + e.message + '</pre>';
  }
  btn.textContent = 'Cargar a PostgreSQL';
  btn.disabled = false;
}
</script>
</body>
</html>""")


@app.post("/upload/shopify-historico")
async def upload_shopify_historico(file: UploadFile = File(...)):
    """
    Carga el CSV histórico de ventas Shopify (2021-2026) a PostgreSQL.
    Pobla: shopify_ventas_historico, shopify_ventas_2025, shopify_pedidos, shopify_lineas_pedido.
    """
    if not file.filename.endswith(".csv"):
        raise HTTPException(400, "Debe ser un archivo .csv")

    import csv
    from collections import defaultdict

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = UPLOADS_DIR / f"shopify_historico_{timestamp}.csv"
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        engine = get_engine()

        # --- parseo ---
        rows, orders_agg, line_items = [], defaultdict(
            lambda: {"fecha": None, "ciudad": "", "total": 0.0}
        ), []

        with open(dest, newline="", encoding="utf-8") as f:
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
                rows.append({"product": product or None, "price": price, "day": day,
                             "customer": customer, "city": city, "order_id": order_id,
                             "total": total, "year": year})
                agg = orders_agg[order_id]
                agg["total"] += total
                if agg["fecha"] is None:
                    agg["fecha"] = day
                    agg["ciudad"] = city
                if product:
                    line_items.append({"id": f"{order_id}-{i}", "order_id": order_id,
                                       "titulo": product, "precio": price or 0.0, "total": total})

        BATCH = 500

        with engine.begin() as conn:
            # --- DDL ---
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS shopify_ventas_historico (
                    id SERIAL PRIMARY KEY, nombre_del_producto TEXT,
                    precio_de_la_variante_de_producto NUMERIC, dia DATE,
                    nombre_del_cliente TEXT, ciudad_del_envio TEXT, ventas_totales NUMERIC)
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS shopify_ventas_2025 (
                    id SERIAL PRIMARY KEY, titulo_del_producto TEXT,
                    precio_de_la_variante_de_producto NUMERIC, dia DATE,
                    nombre_del_cliente TEXT, ciudad_del_envio TEXT,
                    id_de_pedido TEXT, ventas_totales NUMERIC, anio INTEGER)
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS shopify_pedidos (
                    shopify_id TEXT PRIMARY KEY, order_number TEXT, created_at DATE,
                    ciudad_envio TEXT, total_precio NUMERIC, ventas_netas NUMERIC,
                    moneda TEXT DEFAULT 'CLP', synced_at TIMESTAMPTZ DEFAULT NOW())
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS shopify_lineas_pedido (
                    shopify_id TEXT PRIMARY KEY, order_id TEXT, product_id TEXT,
                    variant_id TEXT, titulo TEXT, titulo_variante TEXT, sku TEXT,
                    cantidad INTEGER, precio NUMERIC, total_descuento NUMERIC DEFAULT 0,
                    total_linea NUMERIC)
            """))

            # --- limpiar y cargar tablas legado ---
            conn.execute(text("TRUNCATE shopify_ventas_historico RESTART IDENTITY"))
            conn.execute(text("TRUNCATE shopify_ventas_2025 RESTART IDENTITY"))
            hist = [r for r in rows if r["year"] < 2025]
            new_ = [r for r in rows if r["year"] >= 2025]
            for i in range(0, len(hist), BATCH):
                conn.execute(text("""
                    INSERT INTO shopify_ventas_historico
                        (nombre_del_producto, precio_de_la_variante_de_producto,
                         dia, nombre_del_cliente, ciudad_del_envio, ventas_totales)
                    VALUES (:prod, :precio, :dia, :cliente, :ciudad, :total)
                """), [{"prod": r["product"], "precio": r["price"], "dia": r["day"],
                        "cliente": r["customer"], "ciudad": r["city"], "total": r["total"]}
                       for r in hist[i:i+BATCH]])
            for i in range(0, len(new_), BATCH):
                conn.execute(text("""
                    INSERT INTO shopify_ventas_2025
                        (titulo_del_producto, precio_de_la_variante_de_producto,
                         dia, nombre_del_cliente, ciudad_del_envio,
                         id_de_pedido, ventas_totales, anio)
                    VALUES (:prod, :precio, :dia, :cliente, :ciudad, :oid, :total, :year)
                """), [{"prod": r["product"], "precio": r["price"], "dia": r["day"],
                        "cliente": r["customer"], "ciudad": r["city"], "oid": r["order_id"],
                        "total": r["total"], "year": r["year"]}
                       for r in new_[i:i+BATCH]])

            # --- shopify_pedidos ---
            conn.execute(text("TRUNCATE shopify_pedidos CASCADE"))
            items = list(orders_agg.items())
            for i in range(0, len(items), BATCH):
                conn.execute(text("""
                    INSERT INTO shopify_pedidos
                        (shopify_id, order_number, created_at, ciudad_envio,
                         total_precio, ventas_netas, moneda)
                    VALUES (:id, :num, :fecha, :ciudad, :total, :neto, 'CLP')
                    ON CONFLICT (shopify_id) DO NOTHING
                """), [{"id": oid, "num": oid, "fecha": agg["fecha"], "ciudad": agg["ciudad"],
                        "total": round(agg["total"], 2), "neto": round(agg["total"] / 1.19, 2)}
                       for oid, agg in items[i:i+BATCH]])

            # --- shopify_lineas_pedido ---
            conn.execute(text("TRUNCATE shopify_lineas_pedido"))
            for i in range(0, len(line_items), BATCH):
                conn.execute(text("""
                    INSERT INTO shopify_lineas_pedido
                        (shopify_id, order_id, titulo, precio, total_linea)
                    VALUES (:id, :oid, :titulo, :precio, :total)
                    ON CONFLICT (shopify_id) DO NOTHING
                """), [{"id": li["id"], "oid": li["order_id"], "titulo": li["titulo"],
                        "precio": li["precio"], "total": li["total"]}
                       for li in line_items[i:i+BATCH]])

        return {
            "mensaje": "Carga histórica Shopify completada",
            "filas_totales": len(rows),
            "shopify_ventas_historico": len(hist),
            "shopify_ventas_2025": len(new_),
            "shopify_pedidos": len(orders_agg),
            "shopify_lineas_pedido": len(line_items),
            "rango": f"{min(r['day'] for r in rows)} → {max(r['day'] for r in rows)}",
        }
    except Exception as e:
        dest.unlink(missing_ok=True)
        raise HTTPException(500, f"Error al cargar CSV: {str(e)}")


@app.post("/upload/cartola")
async def upload_cartola(
    file: UploadFile = File(...),
    mes: int = Query(..., ge=1, le=12),
    anio: int = Query(..., ge=2020, le=2030),
):
    if not file.filename.endswith(".xlsx"):
        raise HTTPException(400, "Debe ser un archivo .xlsx")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = UPLOADS_DIR / f"cartola_{anio}_{mes:02d}_{timestamp}.xlsx"
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    try:
        engine = get_engine()
        df = parse_cartola_xlsx(dest, mes, anio)
        rows = load_cartola_to_db(df, engine)
        return {"mensaje": f"Cargadas {rows} filas de cartola para {mes}/{anio}", "archivo": str(dest)}
    except Exception as e:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, f"Error al procesar cartola: {str(e)}")


@app.get("/report/daily", response_class=PlainTextResponse)
async def report_daily():
    engine = get_engine()
    anio, mes = _current_period()
    pnl_shopify = calcular_pnl_shopify(engine, anio, mes)
    pnl_ml = calcular_pnl_ml(engine, anio, mes)
    return reporte_diario(pnl_shopify, pnl_ml)


@app.get("/report/weekly", response_class=PlainTextResponse)
async def report_weekly():
    engine = get_engine()
    anio, mes = _current_period()
    pnl_shopify = calcular_pnl_shopify(engine, anio, mes)
    pnl_ml = calcular_pnl_ml(engine, anio, mes)
    return reporte_semanal(pnl_shopify, pnl_ml)


@app.get("/report/monthly", response_class=PlainTextResponse)
async def report_monthly(
    anio: Optional[int] = Query(default=None),
    mes: Optional[int] = Query(default=None),
):
    engine = get_engine()
    if not anio or not mes:
        anio, mes = _current_period()
    pnl_shopify = calcular_pnl_shopify(engine, anio, mes)
    pnl_ml = calcular_pnl_ml(engine, anio, mes)
    return reporte_mensual(pnl_shopify, pnl_ml, anio, mes)
