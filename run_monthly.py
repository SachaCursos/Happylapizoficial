import os
import smtplib
import subprocess
import sys
from datetime import datetime
from email.mime.text import MIMEText

os.environ["REPORT_MODE"] = "monthly"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PIPELINE = [
    ("fetch_meta_ads.py",       "Descarga datos Meta Ads"),
    ("fetch_shopify_orders.py", "Descarga pedidos Shopify"),
    ("calculate_pl.py",         "Calcula P&L operacional"),
    ("generate_report.py",      "Genera reporte texto"),
    ("send_report.py",          "Envía email"),
]


def send_error_alert(step_name, error_detail):
    host = os.environ.get("SMTP_HOST")
    port = int(os.environ.get("SMTP_PORT", 587))
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASS")
    to = os.environ.get("REPORT_EMAIL_TO")

    if not all([host, user, password, to]):
        print("[ERROR] SMTP no configurado, no se puede enviar alerta.")
        return

    body = (
        f"El pipeline MENSUAL de Happy Lápiz falló.\n\n"
        f"Paso:  {step_name}\n"
        f"Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
        f"Detalle del error:\n{error_detail}"
    )
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = f"❌ Happy Lápiz — Error en pipeline mensual ({step_name})"
    msg["From"] = user
    msg["To"] = to

    try:
        with smtplib.SMTP(host, port) as server:
            server.starttls()
            server.login(user, password)
            server.send_message(msg)
        print(f"[ALERTA] Email de error enviado ({step_name})")
    except Exception as exc:
        print(f"[ALERTA] No se pudo enviar email de error: {exc}")


def main():
    print("=" * 50)
    print(f"PIPELINE MENSUAL — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 50)

    env = os.environ.copy()

    for script_name, description in PIPELINE:
        script_path = os.path.join(BASE_DIR, script_name)
        print(f"\n▶ {description} ({script_name})...")

        result = subprocess.run(
            [sys.executable, script_path],
            capture_output=True,
            text=True,
            env=env,
        )

        if result.stdout:
            print(result.stdout, end="")

        if result.returncode != 0:
            error_detail = (result.stderr or result.stdout or "Sin detalle de error").strip()
            print(f"\n❌ {script_name} falló (código {result.returncode}):", file=sys.stderr)
            print(error_detail, file=sys.stderr)
            send_error_alert(script_name, error_detail)
            sys.exit(result.returncode)

        print(f"✅ {script_name} OK")

    print("\n" + "=" * 50)
    print("✅ PIPELINE MENSUAL COMPLETADO")
    print("=" * 50)


if __name__ == "__main__":
    main()
