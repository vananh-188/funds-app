from flask import Flask, render_template_string
from markupsafe import Markup
import pandas as pd
import requests
from bs4 import BeautifulSoup
import plotly.express as px
import plotly.io as pio
import datetime
import time
import threading

CSV_FILE = "funds.csv"
app = Flask(__name__)

# --- Daily updater ---
def update_csv_daily():
    while True:
        now = datetime.datetime.now()
        # Weekday Mon-Fri and hour 15
        if now.weekday() < 5 and now.hour == 15:
            update_funds_csv(CSV_FILE)
            print(f"✅ CSV updated for {now.strftime('%Y-%m-%d')} at 3 PM.")

            # Sleep until next day 3 PM
            tomorrow = now + datetime.timedelta(days=1)
            next_run = datetime.datetime.combine(tomorrow.date(), datetime.time(15, 0))
            seconds_to_sleep = (next_run - datetime.datetime.now()).total_seconds()
            time.sleep(max(seconds_to_sleep, 0))
        else:
            time.sleep(60)

# --- Fetch fund price ---
def fetch_fund_price(fund_code):
    try:
        url = f"https://fmarket.vn/quy/{fund_code}"
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        price_span = soup.find("span", class_="nav")
        if price_span:
            price_clean = price_span.get_text(strip=True).replace("VND", "").replace(",", "").strip()
            return float(price_clean)
    except Exception as e:
        print(f"⚠️ Error fetching fund {fund_code}: {e}")
    return None

# --- Fetch stock price ---
def fetch_stock_price(stock_code):
    try:
        url = f"https://24hmoney.vn/stock/{stock_code}"
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        price_tag = soup.find("p", class_="price-detail")
        if price_tag:
            price_span = price_tag.find("span", class_="price")
            if price_span:
                price_text = price_span.get_text(strip=True).replace(",", "")
                return round(float(price_text) * 1000, 2)
    except Exception as e:
        print(f"⚠️ Error fetching stock {stock_code}: {e}")
    return None

# --- Format VN style ---
def format_vn(number) -> str:
    if pd.isna(number) or number == "":
        return ""
    return f"{float(number):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

# --- Update CSV ---
def update_funds_csv(file_path=CSV_FILE):
    try:
        df = pd.read_csv(file_path, sep=";")
    except Exception as e:
        print(f"⚠️ Could not read {file_path}: {e}")
        return pd.DataFrame([{
            "items": "TOTAL",
            "type": "",
            "quantity": "",
            "buy_price": "",
            "current_price": "",
            "profit_loss": "0,00"
        }])

    df = df[df["items"] != "TOTAL"]
    total_profit_loss = 0.0

    for i, row in df.iterrows():
        code = str(row["items"]).strip().upper()
        asset_type = str(row.get("type", "")).strip().lower()
        price = None

        try:
            if asset_type == "fund":
                price = fetch_fund_price(code)
            elif asset_type == "stock":
                price = fetch_stock_price(code)
        except Exception as e:
            print(f"⚠️ Fetch error for {code}: {e}")

        if price is not None:
            profit_loss_value = (price - float(row["buy_price"])) * float(row["quantity"])
            total_profit_loss += profit_loss_value
            df.at[i, "current_price"] = format_vn(price)
            df.at[i, "profit_loss"] = format_vn(profit_loss_value)
        else:
            # fallback fast (no blocking)
            df.at[i, "current_price"] = "0,00"
            df.at[i, "profit_loss"] = "0,00"

    sum_row = {
        "items": "TOTAL",
        "type": "",
        "quantity": "",
        "buy_price": "",
        "current_price": "",
        "profit_loss": format_vn(total_profit_loss)
    }
    df = pd.concat([df, pd.DataFrame([sum_row])], ignore_index=True)

    # overwrite CSV so app can reload without scraping
    df.to_csv(file_path, sep=";", index=False)
    return df


# --- Flask route ---
@app.route("/")
def home():
    try:
        df = update_funds_csv()
        table_html = style_table(df)
        chart_html = create_profit_loss_chart(df)
    except Exception as e:
        print(f"⚠️ Page render failed: {e}")
        return "⚠️ Error building page. Please check logs."

    html_template = """
    <html>
        <head>
            <title>Profit/Loss Chart & Table</title>
            <link rel="stylesheet" href="https://stackpath.bootstrapcdn.com/bootstrap/4.5.2/css/bootstrap.min.css">
            <style> table {width: 100%;} </style>
        </head>
        <body class="container">
            <h1 class="mt-3 mb-3">Profit/Loss by Item</h1>
            {{ chart_div|safe }}
            <h2 class="mt-5">Data Table</h2>
            {{ table_div|safe }}
        </body>
    </html>
    """
    return render_template_string(
        html_template,
        table_div=Markup(table_html),
        chart_div=Markup(chart_html)
    )

# --- Plotly chart ---
def create_profit_loss_chart(df):
    df_plot = df[df["items"] != "TOTAL"].copy()
    # Convert profit_loss to float
    def vn_to_float(x):
        try:
            return float(str(x).replace(".", "").replace(",", "."))
        except:
            return 0
    df_plot["profit_loss_float"] = df_plot["profit_loss"].apply(vn_to_float)
    df_plot = df_plot.sort_values("profit_loss_float")
    fig = px.bar(df_plot, x="profit_loss_float", y="items", orientation='h',
                 labels={"profit_loss_float": "Profit/Loss (VND)", "items": "Stock/Fund"},
                 text="profit_loss")
    fig.update_traces(textposition='outside')
    return pio.to_html(fig, full_html=False)

# --- Style table ---
def style_table(df):
    def color_profit(val):
        try:
            val_float = float(str(val).replace(".", "").replace(",", "."))
            if val_float > 0:
                return "color: green; font-weight: bold;"
            elif val_float < 0:
                return "color: red; font-weight: bold;"
            else:
                return ""
        except:
            return ""
    styled_df = df.style.applymap(color_profit, subset=["profit_loss"])
    return styled_df.to_html()

# --- Flask route ---
@app.route("/")
def index():
    df = update_funds_csv()
    table_html = style_table(df)
    chart_html = create_profit_loss_chart(df)
    html_template = """
    <html>
        <head>
            <title>Profit/Loss Chart & Table</title>
            <link rel="stylesheet" href="https://stackpath.bootstrapcdn.com/bootstrap/4.5.2/css/bootstrap.min.css">
            <style> table {width: 100%;} </style>
        </head>
        <body class="container">
            <h1 class="mt-3 mb-3">Profit/Loss by Item</h1>
            {{ chart_div|safe }}
            <h2 class="mt-5">Data Table</h2>
            {{ table_div|safe }}
        </body>
    </html>
    """
    return render_template_string(html_template, table_div=Markup(table_html), chart_div=Markup(chart_html))

# --- Start background thread ---
threading.Thread(target=update_csv_daily, daemon=True).start()

if __name__ == "__main__":
    app.run(debug=True)
