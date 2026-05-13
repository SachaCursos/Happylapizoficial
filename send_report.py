import json
import os
import smtplib
import sys
from datetime import datetime
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPORT_FILE = os.path.join(BASE_DIR, "report_output.txt")
META_FILE = os.path.join(BASE_DIR, "meta_ads_data.json")

MONTHS_ES = {
    1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril",
    5: "Mayo", 6: "Junio", 7: "Julio", 8: "Agosto",
    9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre",
}


def build_subject(meta_info):
    mode = meta_info["modo"]
    desde = meta_info["periodo"]["desde"]
    hasta = meta_info["periodo"]["hasta"]

    if mode == "weekly":
        d_desde = datetime.strptime(desde, "%Y-%m-%d")
        d_hasta = datetime.strptime(hasta, "%Y-%m-%d")
        semana = f"{d_desde.strftime('%d/%m')} al {d_hasta.strftime('%d/%m/%Y')}"
        return f"📊 Happy Lápiz — Reporte Semanal Meta Ads | Semana del {semana}"

    d_hasta = datetime.strptime(hasta, "%Y-%m-%d")
    mes_año = f"{MONTHS_ES[d_hasta.month]} {d_hasta.year}"
    return f"📊 Happy Lápiz — Reporte Mensual Meta Ads | {mes_año}"


def send_report():
    host = os.environ.get("SMTP_HOST")
    port = int(os.environ.get("SMTP_PORT", 587))
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASS")
    to = os.environ.get("REPORT_EMAIL_TO")

    missing = [
        k for k, v in {
            "SMTP_HOST": host,
            "SMTP_USER": user,
            "SMTP_PASS": password,
            "REPORT_EMAIL_TO": to,
        }.items()
        if not v
    ]
    if missing:
        print(f"Error: variables de entorno faltantes: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    for path, label in [(REPORT_FILE, "report_output.txt"), (META_FILE, "meta_ads_data.json")]:
        if not os.path.exists(path):
            print(f"Error: {label} no encontrado. Ejecuta los pasos anteriores primero.", file=sys.stderr)
            sys.exit(1)

    with open(REPORT_FILE, encoding="utf-8") as f:
        report_text = f.read()

    with open(META_FILE, encoding="utf-8") as f:
        meta_raw = f.read()
    meta_info = json.loads(meta_raw)["meta"]

    subject = build_subject(meta_info)

    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to

    msg.attach(MIMEText(report_text, "plain", "utf-8"))

    attachment = MIMEApplication(meta_raw.encode("utf-8"), Name="meta_ads_data.json")
    attachment["Content-Disposition"] = 'attachment; filename="meta_ads_data.json"'
    msg.attach(attachment)

    try:
        with smtplib.SMTP(host, port) as server:
            server.starttls()
            server.login(user, password)
            server.send_message(msg)
        print(f"✅ Email enviado a {to}")
        print(f"   Asunto: {subject}")
    except Exception as exc:
        print(f"❌ Error al enviar email: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    send_report()
