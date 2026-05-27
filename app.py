"""
Happy Lapiz — Financial Dashboard
FastAPI entry point.
"""

from __future__ import annotations

import os
import json
import logging
import shutil
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, File, UploadFile, Query, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine, text

log = logging.getLogger("happylapiz")

from src.calc.pnl_shopify import calcular_pnl_shopify
from src.calc.pnl_ml import calcular_pnl_ml
from src.calc.kpis import check_alerts, classify_product, BREAK_EVEN_MENSUAL_NETO
from src.calc.costos_fijos import TOTAL_CF_MENSUAL
from src.ingestion.meta_ads import parse_meta_json, match_campana_producto, get_latest_meta_json
from src.ingestion.meta_api import fetch_meta_ads, get_meta_gasto_mes, backfill_meta_historico, ensure_meta_tables
from src.ingestion.mercadolibre import parse_ml_xlsx, load_ml_to_db
from src.ingestion.cartola import parse_cartola_xlsx, load_cartola_to_db
from src.reports.dashboard import render_dashboard
from src.reports.email_report import reporte_diario, reporte_semanal, reporte_mensual

DATABASE_URL = os.getenv("DATABASE_URL", "")
UPLOADS_DIR = Path("uploads")
UPLOADS_DIR.mkdir(exist_ok=True)

app = FastAPI(title="Happy Lapiz Dashboard", version="1.0.0")
app.mount("/static", StaticFiles(directory="static"), name="static")

scheduler = AsyncIOScheduler(timezone="America/Santiago")


async def _job_sync_meta_ads():
    """Daily job: fetch Meta Ads for current month and persist to DB."""
    if not os.getenv("META_ACCESS_TOKEN"):
        log.warning("[scheduler] META_ACCESS_TOKEN no configurado — saltando sync Meta Ads")
        return
    if not DATABASE_URL:
        log.warning("[scheduler] DATABASE_URL no configurado — saltando sync Meta Ads")
        return
    try:
        engine = create_engine(DATABASE_URL)
        today = date.today()
        from src.ingestion.meta_api import fetch_meta_ads, _save_month_to_db
        data = fetch_meta_ads(today.year, today.month)
        result = _save_month_to_db(engine, today.year, today.month, data)
        log.info(f"[scheduler] Meta Ads sync OK: {result}")
    except Exception as e:
        log.error(f"[scheduler] Meta Ads sync ERROR: {e}")


@app.on_event("startup")
async def startup():
    # Sync Meta Ads diariamente a las 06:00 hora Chile
    scheduler.add_job(
        _job_sync_meta_ads,
        CronTrigger(hour=6, minute=0),
        id="sync_meta_ads_diario",
        replace_existing=True,
    )
    scheduler.start()
    log.info("[scheduler] Iniciado — Meta Ads sync diario a las 06:00 Santiago")


@app.on_event("shutdown")
async def shutdown():
    scheduler.shutdown(wait=False)


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


def _build_campanas_context(engine, anio: int = None, mes: int = None) -> list[dict]:
    """Build campaign context from Meta API (live) or fallback to uploaded JSON."""
    if anio is None or mes is None:
        anio, mes = _current_period()
    keywords_df = _get_keywords_df(engine)
    campanas = []

    # Try live Meta API first
    try:
        import os
        if os.getenv("META_ACCESS_TOKEN"):
            meta_data = fetch_meta_ads(anio, mes)
            df_c = meta_data.get("campanas", pd.DataFrame())
            if not df_c.empty:
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
                        "fuente": "api",
                    })
                return campanas
    except Exception:
        pass

    # Fallback: uploaded JSON file
    json_path = get_latest_meta_json()
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
                    "fuente": "json",
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
    meta_gasto = get_meta_gasto_mes(anio, mes)
    pnl_shopify = calcular_pnl_shopify(engine, anio, mes, meta_gasto_override=meta_gasto if meta_gasto > 0 else None)
    pnl_ml = calcular_pnl_ml(engine, anio, mes)
    alertas = check_alerts(pnl_shopify, pnl_ml)
    campanas = _build_campanas_context(engine, anio, mes)
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
<head><meta charset="UTF-8"><title>Carga de datos - Happy Lapiz</title>
<style>
  body{font-family:sans-serif;max-width:700px;margin:60px auto;padding:0 20px;background:#0f0f0f;color:#eee}
  h1{color:#a78bfa;margin-bottom:4px}
  p.sub{color:#888;margin-top:0;margin-bottom:32px}
  h3{color:#c4b5fd;margin-bottom:12px}
  .card{background:#1a1a2e;border:1px solid #333;border-radius:12px;padding:28px;margin-bottom:24px}
  label{display:block;margin-bottom:8px;color:#ccc;font-size:14px}
  input[type=file]{width:100%;padding:10px;background:#111;border:1px dashed #555;border-radius:8px;color:#eee;cursor:pointer;box-sizing:border-box}
  button{margin-top:16px;width:100%;padding:13px;background:#7c3aed;color:#fff;border:none;border-radius:8px;font-size:15px;cursor:pointer;font-weight:600}
  button:hover{background:#6d28d9}
  button:disabled{background:#444;cursor:not-allowed}
  .status{margin-top:16px;padding:14px;border-radius:8px;display:none}
  .ok{background:#052e16;border:1px solid #16a34a;color:#86efac}
  .err{background:#2d0505;border:1px solid #dc2626;color:#fca5a5}
  pre{white-space:pre-wrap;word-break:break-all;font-size:12px;margin:0}
  .hint{font-size:12px;color:#666;margin-top:6px}
</style>
</head>
<body>
<h1>Carga de datos</h1>
<p class="sub">Happy Lapiz — Panel de administración</p>

<div class="card">
  <h3>📐 Costos y dimensiones de productos</h3>
  <label>CSV con Nombre_producto, Costo_bruto_CLP, Costo_neto_CLP, Ancho_cm, Alto_cm, Largo_cm, Peso_fisico_g:</label>
  <input type="file" id="costosFile" accept=".csv">
  <p class="hint">Actualiza shopify_productos con costo y dimensiones. Hace matching automático por nombre.</p>
  <button onclick="uploadCostos()">Cargar Costos → PostgreSQL</button>
  <div id="costosStatus" class="status"></div>
</div>

<div class="card">
  <h3>📦 Histórico Shopify</h3>
  <label>CSV de ventas Shopify (2020-2026):</label>
  <input type="file" id="shopifyFile" accept=".csv">
  <p class="hint">Exportado desde Shopify Analytics → Ventas por producto/día</p>
  <button onclick="uploadShopify()">Cargar Shopify → PostgreSQL</button>
  <div id="shopifyStatus" class="status"></div>
</div>

<div class="card">
  <h3>🏷️ Keywords de productos</h3>
  <label>CSV con producto, palabras, prioridad:</label>
  <input type="file" id="kwFile" accept=".csv">
  <p class="hint">Subí esto primero — se usa para asignar productos a las campañas Meta Ads.</p>
  <button onclick="uploadKW()">Cargar Keywords → PostgreSQL</button>
  <div id="kwStatus" class="status"></div>
</div>

<div class="card">
  <h3>📢 Histórico Meta Ads</h3>
  <label>CSV exportado desde Meta Ads Manager (un archivo por año):</label>
  <input type="file" id="metaFile" accept=".csv" multiple>
  <p class="hint">Columnas: Nombre de la campaña, Día, Importe gastado (CLP), Resultados…<br>Podés seleccionar los 4 archivos a la vez (2022, 2023, 2024, 2025).</p>
  <button onclick="uploadMeta()">Cargar Meta Ads → PostgreSQL</button>
  <div id="metaStatus" class="status"></div>
</div>

<script>
async function uploadCostos() {
  const file = document.getElementById('costosFile').files[0];
  if (!file) { alert('Selecciona el CSV de costos'); return; }
  const btn = event.target;
  const status = document.getElementById('costosStatus');
  btn.textContent = 'Cargando…'; btn.disabled = true;
  status.style.display = 'none';
  const fd = new FormData(); fd.append('file', file);
  try {
    const r = await fetch('/upload/productos-costos', {method:'POST', body:fd});
    const j = await r.json();
    status.className = 'status ' + (r.ok ? 'ok' : 'err');
    status.style.display = 'block';
    status.innerHTML = '<pre>' + JSON.stringify(j, null, 2) + '</pre>';
  } catch(e) {
    status.className = 'status err'; status.style.display = 'block';
    status.innerHTML = '<pre>Error: ' + e.message + '</pre>';
  }
  btn.textContent = 'Cargar Costos → PostgreSQL'; btn.disabled = false;
}

async function uploadShopify() {
  const file = document.getElementById('shopifyFile').files[0];
  if (!file) { alert('Selecciona un archivo CSV primero'); return; }
  const btn = event.target;
  const status = document.getElementById('shopifyStatus');
  btn.textContent = 'Cargando… (30-60 seg)'; btn.disabled = true;
  status.style.display = 'none';
  const fd = new FormData(); fd.append('file', file);
  try {
    const r = await fetch('/upload/shopify-historico', {method:'POST', body:fd});
    const j = await r.json();
    status.className = 'status ' + (r.ok ? 'ok' : 'err');
    status.style.display = 'block';
    status.innerHTML = '<pre>' + JSON.stringify(j, null, 2) + '</pre>';
  } catch(e) {
    status.className = 'status err'; status.style.display = 'block';
    status.innerHTML = '<pre>Error: ' + e.message + '</pre>';
  }
  btn.textContent = 'Cargar Shopify → PostgreSQL'; btn.disabled = false;
}

async function uploadKW() {
  const file = document.getElementById('kwFile').files[0];
  if (!file) { alert('Selecciona el CSV de keywords'); return; }
  const btn = event.target;
  const status = document.getElementById('kwStatus');
  btn.textContent = 'Cargando…'; btn.disabled = true;
  status.style.display = 'none';
  const fd = new FormData(); fd.append('file', file);
  try {
    const r = await fetch('/upload/marketing-keywords', {method:'POST', body:fd});
    const j = await r.json();
    status.className = 'status ' + (r.ok ? 'ok' : 'err');
    status.style.display = 'block';
    status.innerHTML = '<pre>' + JSON.stringify(j, null, 2) + '</pre>';
  } catch(e) {
    status.className = 'status err'; status.style.display = 'block';
    status.innerHTML = '<pre>Error: ' + e.message + '</pre>';
  }
  btn.textContent = 'Cargar Keywords → PostgreSQL'; btn.disabled = false;
}

async function uploadMeta() {
  const files = document.getElementById('metaFile').files;
  if (!files.length) { alert('Selecciona al menos un archivo CSV de Meta Ads'); return; }
  const btn = event.target;
  const status = document.getElementById('metaStatus');
  btn.textContent = 'Cargando… puede tardar un momento'; btn.disabled = true;
  status.style.display = 'none';

  let totalResult = {};
  for (const file of files) {
    const fd = new FormData(); fd.append('file', file);
    try {
      const r = await fetch('/upload/meta-csv', {method:'POST', body:fd});
      const j = await r.json();
      if (!r.ok) throw new Error(JSON.stringify(j));
      for (const [k, v] of Object.entries(j)) {
        if (typeof v === 'number') totalResult[k] = (totalResult[k] || 0) + v;
        else totalResult[k] = v;
      }
      totalResult['ultimo_archivo'] = file.name;
    } catch(e) {
      status.className = 'status err'; status.style.display = 'block';
      status.innerHTML = '<pre>Error en ' + file.name + ': ' + e.message + '</pre>';
      btn.textContent = 'Cargar Meta Ads → PostgreSQL'; btn.disabled = false;
      return;
    }
  }
  status.className = 'status ok'; status.style.display = 'block';
  status.innerHTML = '<pre>' + JSON.stringify(totalResult, null, 2) + '</pre>';
  btn.textContent = 'Cargar Meta Ads → PostgreSQL'; btn.disabled = false;
}
</script>
</body>
</html>""")


@app.post("/upload/meta-csv")
async def upload_meta_csv(file: UploadFile = File(...)):
    """
    Carga un CSV exportado desde Meta Ads Manager al historial PostgreSQL.
    Columnas esperadas: Nombre de la campaña, Día, Importe gastado (CLP), Tipo de resultado, Resultados.
    Pobla: meta_gasto_diario, meta_campanas_historico, meta_ads_detalle.
    """
    if not file.filename.endswith(".csv"):
        raise HTTPException(400, "Debe ser un archivo .csv")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = UPLOADS_DIR / f"meta_csv_{timestamp}_{file.filename}"
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        engine = get_engine()

        # Detect encoding
        enc = "utf-8"
        for e in ("utf-8-sig", "utf-8", "latin-1"):
            try:
                with open(dest, newline="", encoding=e) as f:
                    f.read(2048)
                enc = e
                break
            except UnicodeDecodeError:
                continue

        import csv as _csv
        from collections import defaultdict as _dd

        gasto_diario = _dd(lambda: {"gasto": 0, "compras": 0})
        campanas_mes = _dd(lambda: {"gasto": 0, "compras": 0})
        detalle_rows = []

        with open(dest, newline="", encoding=enc) as f:
            reader = _csv.DictReader(f)
            for row in reader:
                dia      = (row.get("Día") or "").strip()
                campana  = (row.get("Nombre de la campaña") or "").strip()
                conjunto = (row.get("Nombre del conjunto de anuncios") or "").strip()
                anuncio  = (row.get("Nombre del anuncio") or "").strip()
                tipo_res = (row.get("Tipo de resultado") or "").strip()
                res_s    = (row.get("Resultados") or "").strip()
                gasto_s  = (row.get("Importe gastado (CLP)") or "").strip()

                if not dia:
                    continue

                gasto_val  = int(float(gasto_s)) if gasto_s else 0
                res_val    = int(float(res_s))   if res_s   else 0
                compra_val = res_val if "compra" in tipo_res.lower() else 0

                gasto_diario[dia]["gasto"]   += gasto_val
                gasto_diario[dia]["compras"] += compra_val

                anio_csv, mes_csv = int(dia[:4]), int(dia[5:7])
                key = (anio_csv, mes_csv, campana)
                campanas_mes[key]["gasto"]   += gasto_val
                campanas_mes[key]["compras"] += compra_val

                detalle_rows.append({
                    "dia": dia, "campana": campana, "conjunto": conjunto,
                    "anuncio": anuncio, "tipo_resultado": tipo_res,
                    "resultados": res_val, "gasto": gasto_val,
                })

        if not detalle_rows:
            raise HTTPException(400, "El CSV no tiene filas válidas o las columnas no coinciden.")

        # Ensure tables exist (create + migrate missing columns)
        from src.ingestion.meta_api import ensure_meta_tables
        ensure_meta_tables(engine)

        with engine.begin() as conn:
            # Add compras column if table existed before without it
            conn.execute(text(
                "ALTER TABLE meta_gasto_diario ADD COLUMN IF NOT EXISTS compras INTEGER DEFAULT 0"
            ))
            # campaign_id was NOT NULL in old schema — make it nullable
            conn.execute(text(
                "ALTER TABLE meta_campanas_historico ALTER COLUMN campaign_id DROP NOT NULL"
            ))
            # Add unique constraint if table existed without it
            conn.execute(text("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint
                        WHERE conname = 'meta_campanas_historico_anio_mes_nombre_key'
                    ) THEN
                        ALTER TABLE meta_campanas_historico
                            ADD CONSTRAINT meta_campanas_historico_anio_mes_nombre_key
                            UNIQUE (anio, mes, nombre);
                    END IF;
                END$$
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS meta_ads_detalle (
                    id             SERIAL PRIMARY KEY,
                    dia            DATE NOT NULL,
                    campana        TEXT,
                    conjunto       TEXT,
                    anuncio        TEXT,
                    tipo_resultado TEXT,
                    resultados     INTEGER DEFAULT 0,
                    gasto          NUMERIC DEFAULT 0,
                    synced_at      TIMESTAMPTZ DEFAULT NOW()
                )
            """))

        BATCH = 500

        # Upsert meta_gasto_diario
        dias_items = list(gasto_diario.items())
        with engine.begin() as conn:
            for i in range(0, len(dias_items), BATCH):
                conn.execute(text("""
                    INSERT INTO meta_gasto_diario (dia, gasto, compras)
                    VALUES (:dia, :gasto, :compras)
                    ON CONFLICT (dia) DO UPDATE SET
                        gasto     = meta_gasto_diario.gasto + EXCLUDED.gasto,
                        compras   = meta_gasto_diario.compras + EXCLUDED.compras,
                        synced_at = NOW()
                """), [{"dia": d, "gasto": v["gasto"], "compras": v["compras"]}
                       for d, v in dias_items[i:i+BATCH]])

        # Upsert meta_campanas_historico
        camp_items = list(campanas_mes.items())
        with engine.begin() as conn:
            for i in range(0, len(camp_items), BATCH):
                conn.execute(text("""
                    INSERT INTO meta_campanas_historico (anio, mes, nombre, gasto, compras)
                    VALUES (:anio, :mes, :nombre, :gasto, :compras)
                    ON CONFLICT (anio, mes, nombre) DO UPDATE SET
                        gasto     = meta_campanas_historico.gasto + EXCLUDED.gasto,
                        compras   = meta_campanas_historico.compras + EXCLUDED.compras,
                        synced_at = NOW()
                """), [{"anio": k[0], "mes": k[1], "nombre": k[2],
                        "gasto": v["gasto"], "compras": v["compras"]}
                       for k, v in camp_items[i:i+BATCH]])

        # Apply producto_publicitado matching
        keywords_df = _get_keywords_df(engine)
        for row in detalle_rows:
            row["producto_publicitado"] = match_campana_producto(row["campana"], keywords_df)

        # Ensure producto_publicitado column exists
        with engine.begin() as conn:
            conn.execute(text(
                "ALTER TABLE meta_ads_detalle ADD COLUMN IF NOT EXISTS producto_publicitado TEXT"
            ))

        # Insert meta_ads_detalle (append — no dedup needed for raw detail)
        with engine.begin() as conn:
            for i in range(0, len(detalle_rows), BATCH):
                conn.execute(text("""
                    INSERT INTO meta_ads_detalle
                        (dia, campana, conjunto, anuncio, tipo_resultado, resultados, gasto, producto_publicitado)
                    VALUES (:dia, :campana, :conjunto, :anuncio, :tipo_resultado, :resultados, :gasto, :producto_publicitado)
                """), detalle_rows[i:i+BATCH])

        # Summary
        dias_unico = len(dias_items)
        gasto_total = sum(v["gasto"] for v in gasto_diario.values())
        compras_total = sum(v["compras"] for v in gasto_diario.values())
        rango_dias = sorted(gasto_diario.keys())

        return {
            "mensaje": f"Meta Ads cargado: {file.filename}",
            "dias_unicos": dias_unico,
            "campanas_x_mes": len(camp_items),
            "filas_detalle": len(detalle_rows),
            "gasto_total_clp": gasto_total,
            "compras_totales": compras_total,
            "rango": f"{rango_dias[0]} → {rango_dias[-1]}",
        }

    except HTTPException:
        raise
    except Exception as e:
        dest.unlink(missing_ok=True)
        raise HTTPException(500, f"Error al procesar CSV: {str(e)}")


@app.post("/upload/marketing-keywords")
async def upload_marketing_keywords(file: UploadFile = File(...)):
    """Carga el CSV de marketing_keywords a PostgreSQL."""
    if not file.filename.endswith(".csv"):
        raise HTTPException(400, "Debe ser un archivo .csv")

    import csv as _csv
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = UPLOADS_DIR / f"marketing_keywords_{timestamp}.csv"
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        engine = get_engine()
        rows = []
        for enc in ("utf-8-sig", "utf-8", "latin-1"):
            try:
                with open(dest, newline="", encoding=enc) as f:
                    rows = list(_csv.DictReader(f))
                break
            except UnicodeDecodeError:
                continue

        with engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS marketing_keywords (
                    id        SERIAL PRIMARY KEY,
                    producto  TEXT NOT NULL,
                    palabras  TEXT NOT NULL,
                    prioridad INTEGER DEFAULT 5
                )
            """))
            conn.execute(text("TRUNCATE marketing_keywords RESTART IDENTITY"))
            for row in rows:
                conn.execute(text("""
                    INSERT INTO marketing_keywords (producto, palabras, prioridad)
                    VALUES (:producto, :palabras, :prioridad)
                """), {
                    "producto":  row.get("producto", "").strip(),
                    "palabras":  row.get("palabras", "").strip(),
                    "prioridad": int(row.get("prioridad", 5)),
                })

        return {"mensaje": f"Keywords cargadas: {len(rows)} productos", "archivo": str(dest)}

    except Exception as e:
        dest.unlink(missing_ok=True)
        raise HTTPException(500, f"Error: {str(e)}")


@app.post("/admin/backfill-producto-publicitado")
async def backfill_producto_publicitado():
    """
    1. Agrega columna producto_publicitado a meta_ads_detalle si no existe.
    2. Backfill de todas las filas existentes usando marketing_keywords.
    3. Devuelve schema de meta_ads para comparar con meta_ads_detalle.
    """
    engine = get_engine()
    keywords_df = _get_keywords_df(engine)

    if keywords_df.empty:
        raise HTTPException(400, "Primero cargá el CSV de marketing_keywords en /upload/marketing-keywords")

    with engine.begin() as conn:
        conn.execute(text(
            "ALTER TABLE meta_ads_detalle ADD COLUMN IF NOT EXISTS producto_publicitado TEXT"
        ))

    # Fetch all rows that need matching (NULL or 'Otros')
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, campana FROM meta_ads_detalle WHERE producto_publicitado IS NULL OR producto_publicitado = 'Otros'"
        )).fetchall()

    BATCH = 200
    updated = 0
    with engine.begin() as conn:
        for i in range(0, len(rows), BATCH):
            batch = rows[i:i+BATCH]
            for row_id, campana in batch:
                producto = match_campana_producto(campana or "", keywords_df)
                conn.execute(text(
                    "UPDATE meta_ads_detalle SET producto_publicitado = :p WHERE id = :id"
                ), {"p": producto, "id": row_id})
            updated += len(batch)

    # Inspect meta_ads schema vs meta_ads_detalle
    with engine.connect() as conn:
        def get_cols(table):
            result = conn.execute(text("""
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_name = :t ORDER BY ordinal_position
            """), {"t": table}).fetchall()
            return {r[0]: r[1] for r in result}

        cols_meta_ads    = get_cols("meta_ads")
        cols_meta_detalle = get_cols("meta_ads_detalle")

    only_in_meta_ads = {k: v for k, v in cols_meta_ads.items() if k not in cols_meta_detalle}

    return {
        "filas_actualizadas": updated,
        "keywords_usadas": len(keywords_df),
        "columnas_en_meta_ads_que_faltan_en_detalle": only_in_meta_ads,
        "columnas_meta_ads_detalle": list(cols_meta_detalle.keys()),
    }


@app.post("/admin/migrar-columnas-meta-ads")
async def migrar_columnas_meta_ads():
    """
    Copia las columnas relevantes de meta_ads a meta_ads_detalle
    que no sean IDs técnicos ni datos ya presentes.
    """
    engine = get_engine()

    # Columns to skip (internal IDs, duplicates, or irrelevant)
    SKIP = {"id", "created_at", "updated_at", "synced_at", "account_id",
            "campaign_id", "adset_id", "ad_id", "date_start", "date_stop"}

    with engine.connect() as conn:
        def get_cols(table):
            r = conn.execute(text("""
                SELECT column_name, data_type FROM information_schema.columns
                WHERE table_name = :t ORDER BY ordinal_position
            """), {"t": table}).fetchall()
            return {row[0]: row[1] for row in r}

        cols_source = get_cols("meta_ads")
        cols_dest   = get_cols("meta_ads_detalle")

    to_add = {k: v for k, v in cols_source.items()
              if k not in cols_dest and k not in SKIP}

    if not to_add:
        return {"mensaje": "No hay columnas nuevas para migrar.", "columnas_revisadas": list(cols_source.keys())}

    # Map Postgres types to safe ADD COLUMN types
    TYPE_MAP = {
        "integer": "INTEGER", "bigint": "BIGINT", "numeric": "NUMERIC",
        "double precision": "NUMERIC", "real": "NUMERIC",
        "character varying": "TEXT", "text": "TEXT",
        "boolean": "BOOLEAN", "date": "DATE",
        "timestamp with time zone": "TIMESTAMPTZ",
        "timestamp without time zone": "TIMESTAMPTZ",
    }

    added = {}
    with engine.begin() as conn:
        for col, dtype in to_add.items():
            pg_type = TYPE_MAP.get(dtype, "TEXT")
            conn.execute(text(
                f"ALTER TABLE meta_ads_detalle ADD COLUMN IF NOT EXISTS {col} {pg_type}"
            ))
            added[col] = pg_type

    return {
        "mensaje": f"Se agregaron {len(added)} columnas a meta_ads_detalle",
        "columnas_agregadas": added,
    }


@app.post("/upload/productos-costos")
async def upload_productos_costos(file: UploadFile = File(...)):
    """
    Agrega costo_bruto_clp, costo_neto_clp, ancho_cm, alto_cm, largo_cm, peso_fisico_g
    a shopify_productos haciendo match por nombre de producto.
    """
    if not file.filename.endswith(".csv"):
        raise HTTPException(400, "Debe ser un archivo .csv")

    import csv as _csv

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = UPLOADS_DIR / f"productos_costos_{timestamp}.csv"
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        engine = get_engine()

        # Detect encoding and parse CSV
        rows = []
        for enc in ("utf-8-sig", "utf-8", "latin-1"):
            try:
                with open(dest, newline="", encoding=enc) as f:
                    rows = list(_csv.DictReader(f))
                break
            except UnicodeDecodeError:
                continue

        if not rows:
            raise HTTPException(400, "CSV vacío o sin filas válidas")

        # Create table if it doesn't exist yet (ETL may not have run)
        with engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS shopify_productos (
                    shopify_id       TEXT PRIMARY KEY,
                    titulo           TEXT,
                    handle           TEXT,
                    estado           TEXT,
                    vendor           TEXT,
                    tipo             TEXT,
                    tags             TEXT,
                    inventario_total INTEGER,
                    imagen_url       TEXT,
                    precio_min       NUMERIC,
                    created_at       TIMESTAMPTZ,
                    updated_at       TIMESTAMPTZ,
                    synced_at        TIMESTAMPTZ DEFAULT NOW()
                )
            """))

        # Add cost/dimension columns if they don't exist
        with engine.begin() as conn:
            for col, tipo in [
                ("costo_bruto_clp", "NUMERIC"),
                ("costo_neto_clp",  "NUMERIC"),
                ("ancho_cm",        "NUMERIC"),
                ("alto_cm",         "NUMERIC"),
                ("largo_cm",        "NUMERIC"),
                ("peso_fisico_g",   "NUMERIC"),
            ]:
                conn.execute(text(
                    f"ALTER TABLE shopify_productos ADD COLUMN IF NOT EXISTS {col} {tipo}"
                ))

        # Load existing product titles from DB for fuzzy matching
        with engine.connect() as conn:
            db_products = conn.execute(
                text("SELECT shopify_id, titulo FROM shopify_productos WHERE titulo IS NOT NULL")
            ).fetchall()

        # If table is empty, insert products directly from CSV using name as ID
        if not db_products:
            with engine.begin() as conn:
                for row in rows:
                    nombre = (row.get("Nombre_producto") or "").strip()
                    if not nombre:
                        continue
                    conn.execute(text("""
                        INSERT INTO shopify_productos
                            (shopify_id, titulo, costo_bruto_clp, costo_neto_clp,
                             ancho_cm, alto_cm, largo_cm, peso_fisico_g)
                        VALUES
                            (:id, :titulo, :cb, :cn, :ancho, :alto, :largo, :peso)
                        ON CONFLICT (shopify_id) DO UPDATE SET
                            costo_bruto_clp = EXCLUDED.costo_bruto_clp,
                            costo_neto_clp  = EXCLUDED.costo_neto_clp,
                            ancho_cm        = EXCLUDED.ancho_cm,
                            alto_cm         = EXCLUDED.alto_cm,
                            largo_cm        = EXCLUDED.largo_cm,
                            peso_fisico_g   = EXCLUDED.peso_fisico_g,
                            synced_at       = NOW()
                    """), {
                        "id":    nombre,
                        "titulo": nombre,
                        "cb":    safe_num(row.get("Costo_bruto_CLP", "")),
                        "cn":    safe_num(row.get("Costo_neto_CLP", "")),
                        "ancho": safe_num(row.get("Ancho_cm", "")),
                        "alto":  safe_num(row.get("Alto_cm", "")),
                        "largo": safe_num(row.get("Largo_cm", "")),
                        "peso":  safe_num(row.get("Peso_fisico_g", "")),
                    })
            return {
                "mensaje": "shopify_productos estaba vacía — productos insertados directamente desde CSV",
                "insertados": len([r for r in rows if r.get("Nombre_producto")]),
                "nota": "Cuando el ETL de Shopify sincronice, actualizará estos registros con los IDs reales",
            }

        def safe_num(val: str):
            v = val.strip() if val else ""
            return float(v) if v else None

        def best_match(nombre_csv: str) -> str | None:
            """Return shopify_id of best matching product, or None."""
            nombre_lower = nombre_csv.lower().strip()
            # 1. Exact match
            for sid, titulo in db_products:
                if titulo and titulo.lower().strip() == nombre_lower:
                    return sid
            # 2. CSV name contained in DB title or vice versa
            for sid, titulo in db_products:
                if not titulo:
                    continue
                t = titulo.lower().strip()
                if nombre_lower in t or t in nombre_lower:
                    return sid
            # 3. All significant words present (>=4 chars)
            words = [w for w in nombre_lower.split() if len(w) >= 4]
            if words:
                for sid, titulo in db_products:
                    if not titulo:
                        continue
                    t = titulo.lower()
                    if all(w in t for w in words):
                        return sid
            return None

        updated, not_found = [], []

        with engine.begin() as conn:
            for row in rows:
                nombre = (row.get("Nombre_producto") or "").strip()
                if not nombre:
                    continue

                sid = best_match(nombre)
                if not sid:
                    not_found.append(nombre)
                    continue

                conn.execute(text("""
                    UPDATE shopify_productos SET
                        costo_bruto_clp = :cb,
                        costo_neto_clp  = :cn,
                        ancho_cm        = :ancho,
                        alto_cm         = :alto,
                        largo_cm        = :largo,
                        peso_fisico_g   = :peso,
                        synced_at       = NOW()
                    WHERE shopify_id = :sid
                """), {
                    "cb":    safe_num(row.get("Costo_bruto_CLP", "")),
                    "cn":    safe_num(row.get("Costo_neto_CLP", "")),
                    "ancho": safe_num(row.get("Ancho_cm", "")),
                    "alto":  safe_num(row.get("Alto_cm", "")),
                    "largo": safe_num(row.get("Largo_cm", "")),
                    "peso":  safe_num(row.get("Peso_fisico_g", "")),
                    "sid":   sid,
                })
                updated.append(nombre)

        return {
            "actualizados": len(updated),
            "no_encontrados": len(not_found),
            "productos_actualizados": updated,
            "productos_no_encontrados": not_found,
        }

    except HTTPException:
        raise
    except Exception as e:
        dest.unlink(missing_ok=True)
        raise HTTPException(500, f"Error: {str(e)}")


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

        # Try utf-8-sig first (handles BOM from Mac/Excel), fallback to latin-1
        for enc in ("utf-8-sig", "utf-8", "latin-1"):
            try:
                with open(dest, newline="", encoding=enc) as f:
                    sample = f.read(1024)
                if sample:
                    break
            except UnicodeDecodeError:
                continue

        with open(dest, newline="", encoding=enc) as f:
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

        if not rows:
            raise HTTPException(400, f"CSV sin filas válidas (encoding detectado: {enc}). Revisa el archivo.")

        return {
            "mensaje": "Carga histórica Shopify completada",
            "encoding_detectado": enc,
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


@app.post("/admin/sync-meta-hoy")
async def sync_meta_hoy():
    """Fuerza el sync de Meta Ads para el mes actual ahora mismo."""
    await _job_sync_meta_ads()
    today = date.today()
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT COUNT(*), SUM(gasto) FROM meta_gasto_diario "
            "WHERE EXTRACT(YEAR FROM dia)=:y AND EXTRACT(MONTH FROM dia)=:m"
        ), {"y": today.year, "m": today.month}).fetchone()
    return {
        "mensaje": f"Sync Meta Ads {today.year}-{today.month:02d} completado",
        "dias_en_db": row[0],
        "gasto_mes_clp": round(float(row[1] or 0)),
    }


@app.post("/admin/meta-historico")
async def admin_meta_historico(
    desde: str = Query(..., description="Mes inicial YYYY-MM, ej: 2023-01"),
    hasta: str = Query(default=None, description="Mes final YYYY-MM (default: mes actual)"),
):
    """
    Descarga y guarda el historial de Meta Ads mes a mes desde la Marketing API.
    Pobla: meta_gasto_diario, meta_campanas_historico.
    Puede tardar varios minutos dependiendo del rango solicitado.
    """
    try:
        desde_t = (int(desde[:4]), int(desde[5:7]))
    except Exception:
        raise HTTPException(400, "Formato inválido para 'desde'. Usar YYYY-MM")

    hasta_t = None
    if hasta:
        try:
            hasta_t = (int(hasta[:4]), int(hasta[5:7]))
        except Exception:
            raise HTTPException(400, "Formato inválido para 'hasta'. Usar YYYY-MM")

    engine = get_engine()
    try:
        results = backfill_meta_historico(engine=engine, desde=desde_t, hasta=hasta_t, sleep_between=1.0)
    except RuntimeError as e:
        raise HTTPException(500, str(e))

    ok = [r for r in results if r.get("ok")]
    fail = [r for r in results if not r.get("ok")]
    return {
        "meses_procesados": len(ok),
        "meses_con_error": len(fail),
        "dias_guardados": sum(r.get("dias_guardados", 0) for r in ok),
        "campanas_guardadas": sum(r.get("campanas_guardadas", 0) for r in ok),
        "gasto_total": round(sum(r.get("gasto_total", 0) for r in ok), 2),
        "detalle": results,
    }


@app.get("/api/meta/historico")
async def api_meta_historico(
    desde: str = Query(default=None, description="YYYY-MM"),
    hasta: str = Query(default=None, description="YYYY-MM"),
):
    """Consulta gasto diario Meta Ads guardado en PostgreSQL."""
    engine = get_engine()
    ensure_meta_tables(engine)

    filtros = []
    params: dict = {}
    if desde:
        filtros.append("dia >= :desde")
        params["desde"] = f"{desde}-01"
    if hasta:
        filtros.append("dia <= :hasta")
        # last day of month
        try:
            a, m = int(hasta[:4]), int(hasta[5:7])
            from datetime import date as _date, timedelta as _td
            if m < 12:
                ultimo = _date(a, m + 1, 1) - _td(days=1)
            else:
                ultimo = _date(a + 1, 1, 1) - _td(days=1)
            params["hasta"] = str(ultimo)
        except Exception:
            params["hasta"] = f"{hasta}-31"

    where = f"WHERE {' AND '.join(filtros)}" if filtros else ""
    with engine.connect() as conn:
        rows = conn.execute(
            text(f"SELECT dia, gasto, impresiones, clics, ctr FROM meta_gasto_diario {where} ORDER BY dia"),
            params,
        ).fetchall()

    return {
        "total_dias": len(rows),
        "gasto_total": round(sum(float(r[1] or 0) for r in rows), 2),
        "datos": [
            {"dia": str(r[0]), "gasto": float(r[1] or 0),
             "impresiones": r[2], "clics": r[3], "ctr": float(r[4] or 0)}
            for r in rows
        ],
    }


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
