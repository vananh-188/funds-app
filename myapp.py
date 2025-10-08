from flask import Flask, render_template_string
from markupsafe import Markup
import pandas as pd
import requests
from bs4 import BeautifulSoup
import plotly.express as px
import plotly.io as pio
import plotly.graph_objects as go
import datetime
import time
import threading

CSV_FILE = "funds.csv"
app = Flask(__name__)

# --- Helpers ---------------------------------------------------------------
def parse_number(val):
    """
    Parse number from formats like:
    '31,953' -> 31953.0
    '98.020,00' -> 98020.0
    '148,00' -> 148.0
    """
    if pd.isna(val) or val == "":
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)

    s = str(val).replace("VND", "").strip()

    # Remove spaces
    s = s.replace(" ", "")

    # Case: both dot and comma
    if "." in s and "," in s:
        if s.rfind(".") > s.rfind(","):
            # Dot is decimal (e.g. 98,020.00) => remove commas
            s = s.replace(",", "")
        else:
            # Comma is decimal (e.g. 98.020,00) => remove dots, replace comma
            s = s.replace(".", "").replace(",", ".")
    elif "," in s and "." not in s:
        # Only comma present
        parts = s.split(",")
        if len(parts[-1]) == 3:  # e.g. 31,953
            s = s.replace(",", "")
        else:
            s = s.replace(",", ".")
    # else: only dot or none -> leave as is

    try:
        return float(s)
    except:
        return 0.0


def format_vn(value):
    try:
        value = float(value)
        formatted = f"{value:,.2f}"
        formatted = formatted.replace(",", "X").replace(".", ",").replace("X", ".")
        return formatted
    except (ValueError, TypeError):
        return value


# --- Fetchers --------------------------------------------------------------
def fetch_fund_price(fund_code):
    try:
        url = f"https://fmarket.vn/quy/{fund_code}"
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        price_span = soup.find("span", class_="nav")
        if price_span:
            price_text = price_span.get_text(strip=True)
            price_text = price_text.replace("VND", "").strip()
            # parse to numeric using robust parser
            return parse_number(price_text)
    except Exception as e:
        print(f"⚠️ Error fetching fund {fund_code}: {e}")
    return None

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
                # price on 24hmoney is presentation '64.40' meaning 64.40 * 1000 VND
                price = float(price_text) * 1000.0
                return round(price, 2)
    except Exception as e:
        print(f"⚠️ Error fetching stock {stock_code}: {e}")
    return None

# --- CSV updater -----------------------------------------------------------
def update_funds_csv(file_path=CSV_FILE):
    try:
        df = pd.read_csv(file_path, sep=";")
        # Force normalize buy_price and quantity before loop
        df["buy_price"] = df["buy_price"].apply(lambda x: format_vn(parse_number(x)))
        df["quantity"] = df["quantity"].apply(lambda x: format_vn(parse_number(x)))
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

    # remove any existing TOTAL row before recalculating
    if "items" in df.columns:
        df = df[df["items"] != "TOTAL"].copy()
    else:
        df = df.copy()

    # Ensure we can assign formatted strings safely: convert display columns to object dtype
    for col in ["buy_price", "current_price", "profit_loss", "quantity"]:
        if col in df.columns:
            df[col] = df[col].astype(object)

    total_profit_loss = 0.0
    for i, row in df.iterrows():
        code = str(row.get("items", "")).strip().upper()
        asset_type = str(row.get("type", "")).strip().lower()

        # parse numeric buy_price and quantity safely
        buy_price_val = parse_number(row.get("buy_price", 0))  # parsed from formatted string
        quantity_val = parse_number(row.get("quantity", 0))

        # fetch current price depending on type
        price = None
        try:
            if asset_type == "fund":
                price = fetch_fund_price(code)
            elif asset_type == "stock":
                price = fetch_stock_price(code)
        except Exception as e:
            print(f"⚠️ Fetch error for {code}: {e}")

        # ✅ Always re-format numeric values
        df.at[i, "quantity"] = format_vn(quantity_val)
        df.at[i, "buy_price"] = format_vn(buy_price_val)

        if price is not None:
            profit_loss_value = (price - buy_price_val) * quantity_val
            total_profit_loss += profit_loss_value

            df.at[i, "current_price"] = format_vn(price)
            df.at[i, "profit_loss"] = format_vn(profit_loss_value)
        else:
            df.at[i, "current_price"] = ""
            df.at[i, "profit_loss"] = "0,00"
    # Append TOTAL row
    sum_row = {
        "items": "TOTAL",
        "type": "",
        "quantity": "",
        "buy_price": "",
        "current_price": "",
        "profit_loss": format_vn(total_profit_loss)
    }
    df = pd.concat([df, pd.DataFrame([sum_row])], ignore_index=True)

    # Overwrite CSV (so app can reload without scraping next time)
    try:
        df.to_csv(file_path, sep=";", index=False)
    except Exception as e:
        print(f"⚠️ Could not write CSV {file_path}: {e}")

    return df

def create_profit_loss_chart(df):
    df_plot = df[df["items"] != "TOTAL"].copy()

    # Convert VN-style profit_loss strings to float
    def vn_to_float(x):
        try:
            return parse_number(x)
        except:
            return 0.0

    df_plot["profit_loss_float"] = df_plot["profit_loss"].apply(vn_to_float)
    df_plot = df_plot.sort_values("profit_loss_float", ascending=False)

    fig = px.bar(
        df_plot,
        x="items",
        y="profit_loss_float",
        labels={"profit_loss_float": "Profit/Loss (VND)", "items": "Stock/Fund"},
        text="profit_loss"
    )

    fig.update_traces(textposition="outside")
    fig.update_layout(
        title="Profit/Loss by Stock/Fund",
        xaxis_title="Stock/Fund",
        yaxis_title="Profit/Loss (VND)",
        template="plotly_white",
        margin=dict(l=40, r=40, t=60, b=100),
        height=500
    )

    return pio.to_html(fig, full_html=False)

# --- Style table ----------------------------------------------------------
def style_table(df):
    def color_profit(val):
        try:
            val_float = parse_number(val)
            if val_float > 0:
                return "color: green; font-weight: bold;"
            elif val_float < 0:
                return "color: red; font-weight: bold;"
            else:
                return ""
        except:
            return ""

    styled_df = df.style.map(color_profit, subset=["profit_loss"]) \
        .set_table_attributes('class="table table-bordered table-striped table-hover"') \
        .set_table_styles([
            {"selector": "th", "props": [("background-color", "#f8f9fa"), ("font-weight", "bold")]},
            {"selector": "td", "props": [("text-align", "center")]}
        ])

    return styled_df.to_html()

# --- Daily updater (background) -------------------------------------------
def update_csv_daily():
    while True:
        now = datetime.datetime.now()
        # Weekday Mon-Fri and hour 15 (3 PM)
        if now.weekday() < 5 and now.hour == 15:
            try:
                update_funds_csv(CSV_FILE)
                print(f"✅ CSV updated for {now.strftime('%Y-%m-%d')} at 3 PM.")
            except Exception as e:
                print(f"⚠️ Daily update failed: {e}")

            # Sleep until next day 3 PM
            tomorrow = now + datetime.timedelta(days=1)
            next_run = datetime.datetime.combine(tomorrow.date(), datetime.time(15, 0))
            seconds_to_sleep = (next_run - datetime.datetime.now()).total_seconds()
            time.sleep(max(seconds_to_sleep, 0))
        else:
            time.sleep(60)

# --- Flask route (single) -----------------------------------------------
@app.route("/")
def index():
    try:
        df = update_funds_csv(CSV_FILE)
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
            <style>
                table {
                    width: 100%;
                    border-collapse: collapse;
                }
                th, td {
                    border: 1px solid #dee2e6;
                    padding: 8px;
                    text-align: center;
                }
                th {
                    background-color: #f8f9fa;
                    font-weight: bold;
                }
                tr:nth-child(even) {
                    background-color: #f2f2f2;
                }
            </style>
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

# --- Start background thread ---
threading.Thread(target=update_csv_daily, daemon=True).start()

if __name__ == "__main__":
    app.run(debug=True)
