import os
import json
import smtplib
import sys
from datetime import date, timedelta
from email.mime.text import MIMEText

import requests

META_API_VERSION = "v19.0"
ACCOUNT_ID = "act_449499746278703"
BASE_URL = f"https://graph.facebook.com/{META_API_VERSION}"
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
OUTPUT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "meta_ads_data.json")


# ── Fechas ────────────────────────────────────────────────────────────────────

def get_date_range(mode):
    today = date.today()
    if mode == "weekly":
        # Semana anterior: lunes → domingo
        days_since_monday = today.weekday()          # 0 = lunes
        last_sunday = today - timedelta(days=days_since_monday + 1)
        last_monday = last_sunday - timedelta(days=6)
        return str(last_monday), str(last_sunday)
    if mode == "monthly":
        first_this_month = today.replace(day=1)
        last_day_prev = first_this_month - timedelta(days=1)
        first_day_prev = last_day_prev.replace(day=1)
        return str(first_day_prev), str(last_day_prev)
    raise ValueError(f"REPORT_MODE debe ser 'weekly' o 'monthly', recibido: '{mode}'")


# ── Email de alerta ───────────────────────────────────────────────────────────

def send_alert_email(subject, body):
    host = os.environ.get("SMTP_HOST")
    port = int(os.environ.get("SMTP_PORT", 587))
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASS")
    to = os.environ.get("REPORT_EMAIL_TO")

    if not all([host, user, password, to]):
        print(f"[ALERTA] SMTP no configurado — no se pudo enviar: {subject}")
        return

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to

    try:
        with smtplib.SMTP(host, port) as server:
            server.starttls()
            server.login(user, password)
            server.send_message(msg)
        print(f"[ALERTA] Email enviado: {subject}")
    except Exception as exc:
        print(f"[ALERTA] Fallo al enviar email: {exc}")


TOKEN_EXPIRED_BODY = """El token de acceso de Meta Ads ha expirado.

Para renovarlo:
1. Ir a https://developers.facebook.com/tools/explorer
2. Seleccionar la app "Integracion Claude"
3. Agregar permisos: ads_read + ads_management
4. Hacer clic en "Generar token de acceso"
5. Copiar el nuevo token
6. Actualizar la variable META_ACCESS_TOKEN en Railway

El pipeline no pudo completarse. Renueva el token y vuelve a ejecutar."""


# ── API Meta ──────────────────────────────────────────────────────────────────

def fetch_all_pages(url, params=None):
    results = []
    current_params = params

    while url:
        resp = requests.get(url, params=current_params, timeout=30)
        data = resp.json()

        if "error" in data:
            error = data["error"]
            code = error.get("code")
            msg = error.get("message", "")
            if code == 190:
                send_alert_email(
                    "⚠️ Token Meta Ads expirado — Happy Lápiz",
                    TOKEN_EXPIRED_BODY + f"\n\nError original: {msg}",
                )
                sys.exit(1)
            raise RuntimeError(f"Meta API error {code}: {msg}")

        results.extend(data.get("data", []))
        url = data.get("paging", {}).get("next")
        current_params = None   # la URL next ya lleva todos los params

    return results


def fetch_insights(access_token, since, until, level):
    base_fields = "spend,impressions,clicks,ctr,cpm,cpc"
    extra = ""
    if level == "campaign":
        extra = ",campaign_name,actions,action_values"
    elif level == "adset":
        extra = ",campaign_name,adset_name,actions,action_values"

    params = {
        "access_token": access_token,
        "time_range": json.dumps({"since": since, "until": until}),
        "fields": base_fields + extra,
        "level": level,
    }
    if level == "account":
        params["time_increment"] = "1"

    return fetch_all_pages(f"{BASE_URL}/{ACCOUNT_ID}/insights", params)


# ── Helpers ───────────────────────────────────────────────────────────────────

def extract_action(action_list, action_type):
    for item in (action_list or []):
        if item.get("action_type") == action_type:
            return float(item.get("value", 0))
    return 0.0


def load_products():
    with open(os.path.join(DATA_DIR, "products.json"), encoding="utf-8") as f:
        return json.load(f)


def match_product(name, products):
    name_lower = name.lower()
    for product in products:
        for kw in product.get("keywords", []):
            if kw.lower() in name_lower:
                return product["nombre"]
    return "Otros"


def clp_fmt(amount):
    return f"${int(amount):,}".replace(",", ".")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    access_token = os.environ.get("META_ACCESS_TOKEN")
    if not access_token:
        print("Error: META_ACCESS_TOKEN no configurado")
        sys.exit(1)

    mode = os.environ.get("REPORT_MODE", "weekly").strip().lower()
    since, until = get_date_range(mode)

    print(f"Modo: {mode.upper()} | Período: {since} → {until}")
    print("Descargando datos de Meta Ads...")

    products = load_products()

    # Nivel cuenta — diario
    print("  [1/3] Nivel cuenta (diario)...")
    account_rows = fetch_insights(access_token, since, until, "account")
    gasto_diario = [
        {
            "dia": r.get("date_start"),
            "gasto_clp": float(r.get("spend", 0)),
            "impresiones": int(r.get("impressions", 0)),
            "clics": int(r.get("clicks", 0)),
            "ctr": float(r.get("ctr", 0)),
            "cpm": float(r.get("cpm", 0)),
            "cpc": float(r.get("cpc", 0)),
        }
        for r in sorted(account_rows, key=lambda x: x.get("date_start", ""))
    ]

    # Nivel campaña
    print("  [2/3] Nivel campaña...")
    campaign_rows = fetch_insights(access_token, since, until, "campaign")
    campanas = []
    for r in campaign_rows:
        gasto = float(r.get("spend", 0))
        if gasto == 0:
            continue
        ingresos = extract_action(r.get("action_values"), "purchase")
        compras = extract_action(r.get("actions"), "purchase")
        roas = round(ingresos / gasto, 2) if gasto > 0 else 0.0
        campanas.append({
            "nombre": r.get("campaign_name", ""),
            "producto": match_product(r.get("campaign_name", ""), products),
            "gasto_clp": gasto,
            "ingresos_pixel_clp": ingresos,
            "roas": roas,
            "compras_pixel": int(compras),
            "impresiones": int(r.get("impressions", 0)),
            "clics": int(r.get("clicks", 0)),
            "ctr": float(r.get("ctr", 0)),
        })

    # Nivel adset
    print("  [3/3] Nivel adset...")
    adset_rows = fetch_insights(access_token, since, until, "adset")
    adsets = []
    for r in adset_rows:
        gasto = float(r.get("spend", 0))
        if gasto == 0:
            continue
        ingresos = extract_action(r.get("action_values"), "purchase")
        compras = extract_action(r.get("actions"), "purchase")
        roas = round(ingresos / gasto, 2) if gasto > 0 else 0.0
        adset_name = r.get("adset_name", "")
        campaign_name = r.get("campaign_name", "")
        producto = match_product(adset_name, products)
        if producto == "Otros":
            producto = match_product(campaign_name, products)
        adsets.append({
            "nombre": adset_name,
            "campana": campaign_name,
            "producto": producto,
            "gasto_clp": gasto,
            "ingresos_pixel_clp": ingresos,
            "roas": roas,
            "compras_pixel": int(compras),
            "impresiones": int(r.get("impressions", 0)),
            "clics": int(r.get("clicks", 0)),
            "ctr": float(r.get("ctr", 0)),
        })

    # Resumen global
    gasto_total = sum(d["gasto_clp"] for d in gasto_diario)
    ingresos_total = sum(c["ingresos_pixel_clp"] for c in campanas)
    compras_total = int(sum(c["compras_pixel"] for c in campanas))
    impresiones_total = sum(d["impresiones"] for d in gasto_diario)
    clics_total = sum(d["clics"] for d in gasto_diario)
    roas_global = round(ingresos_total / gasto_total, 2) if gasto_total > 0 else 0.0
    ctr_promedio = round(clics_total / impresiones_total * 100, 2) if impresiones_total > 0 else 0.0
    dias_con_gasto = [d["dia"] for d in gasto_diario if d["gasto_clp"] > 0]
    ultimo_dia = max(dias_con_gasto) if dias_con_gasto else None

    output = {
        "meta": {
            "periodo": {"desde": since, "hasta": until},
            "modo": mode,
            "fecha_descarga": date.today().isoformat(),
            "cuenta": ACCOUNT_ID,
        },
        "resumen": {
            "gasto_total": gasto_total,
            "ingresos_pixel_total": ingresos_total,
            "roas_global": roas_global,
            "compras_pixel": compras_total,
            "impresiones": impresiones_total,
            "clics": clics_total,
            "ctr_promedio": ctr_promedio,
            "ultimo_dia_con_gasto": ultimo_dia,
        },
        "gasto_diario": gasto_diario,
        "campanas": campanas,
        "adsets": adsets,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n✅ meta_ads_data.json guardado")
    print(f"\n{'='*44}")
    print(f"RESUMEN META ADS — {mode.upper()}")
    print(f"Período: {since} → {until}")
    print(f"{'='*44}")
    print(f"Gasto total:      {clp_fmt(gasto_total)} CLP")
    print(f"Ingresos pixel:   {clp_fmt(ingresos_total)} CLP")
    print(f"ROAS global:      {roas_global}x")
    print(f"Compras pixel:    {compras_total}")
    print(f"Impresiones:      {impresiones_total:,}".replace(",", "."))
    print(f"CTR promedio:     {ctr_promedio}%")
    print(f"Último día gasto: {ultimo_dia}")
    print(f"Campañas activas: {len(campanas)}")
    print(f"Adsets activos:   {len(adsets)}")


if __name__ == "__main__":
    main()
