# --------------------------------------------------------------
# app.py – Flask + Pandas + Plotly + Web-scraping + CRUD UI
# --------------------------------------------------------------
from flask import Flask, render_template_string, request, redirect, url_for, flash
from markupsafe import Markup
import pandas as pd
import requests
from bs4 import BeautifulSoup
import plotly.express as px
import plotly.io as pio
import datetime
import time
import threading
import os

CSV_FILE = "funds.csv"
app = Flask(__name__)
app.secret_key = "super-secret-key-CHANGE-ME"

# ------------------------------------------------------------------
# -------------------------- HELPERS -----------------------------
# ------------------------------------------------------------------
def parse_number(val):
    if pd.isna(val) or val == "": return 0.0
    if isinstance(val, (int, float)): return float(val)
    s = str(val).replace("VND", "").strip().replace(" ", "")
    if "." in s and "," in s:
        if s.rfind(".") > s.rfind(","):
            s = s.replace(",", "")
        else:
            s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        parts = s.split(",")
        if len(parts[-1]) == 3:
            s = s.replace(",", "")
        else:
            s = s.replace(",", ".")
    try:
        return float(s)
    except:
        return 0.0

def format_vn(value):
    try:
        f = float(value)
        s = f"{f:,.2f}"
        return s.replace(",", "X").replace(".", ",").replace("X", ".")
    except (ValueError, TypeError):
        return value

# ------------------------------------------------------------------
# -------------------------- SCRAPERS ----------------------------
# ------------------------------------------------------------------
def fetch_fund_price(fund_code):
    try:
        url = f"https://fmarket.vn/quy/{fund_code}"
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        price_span = soup.find("span", class_="nav")
        if price_span:
            txt = price_span.get_text(strip=True).replace("VND", "")
            return parse_number(txt)
    except Exception as e:
        print(f"Error fund {fund_code}: {e}")
    return None

def fetch_stock_price(stock_code):
    try:
        url = f"https://24hmoney.vn/stock/{stock_code}"
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        price_tag = soup.find("p", class_="price-detail")
        if price_tag:
            span = price_tag.find("span", class_="price")
            if span:
                txt = span.get_text(strip=True).replace(",", "")
                price = float(txt) * 1000.0
                return round(price, 2)
    except Exception as e:
        print(f"Error stock {stock_code}: {e}")
    return None

# ------------------------------------------------------------------
# -------------------------- CSV LOGIC ---------------------------
# ------------------------------------------------------------------
def load_csv():
    if not os.path.exists(CSV_FILE):
        empty = pd.DataFrame(columns=["items", "type", "quantity", "buy_price", "current_price", "profit_loss"])
        empty.to_csv(CSV_FILE, sep=";", index=False)
        return empty
    df = pd.read_csv(CSV_FILE, sep=";")
    for col in ["items", "type", "quantity", "buy_price", "current_price", "profit_loss"]:
        if col not in df.columns:
            df[col] = ""
    return df

def save_csv(df):
    keep = ["items", "type", "quantity", "buy_price", "current_price", "profit_loss"]
    out = df[keep].copy()
    out = out[out["items"] != "TOTAL"]
    out.to_csv(CSV_FILE, sep=";", index=False)

def recalc_prices_and_profit(df):
    df = df.copy()
    df = df[df["items"] != "TOTAL"].copy()
    total_buy = total_current = 0.0

    for idx, row in df.iterrows():
        code = str(row["items"]).strip().upper()
        typ = str(row["type"]).strip().lower()
        qty = parse_number(row["quantity"])
        buy = parse_number(row["buy_price"])
        price = None
        if typ == "fund":
            price = fetch_fund_price(code)
        elif typ == "stock":
            price = fetch_stock_price(code)

        df.at[idx, "quantity"] = format_vn(qty)
        df.at[idx, "buy_price"] = format_vn(buy)

        if price is not None:
            cur_val = price * qty
            buy_val = buy * qty
            profit = cur_val - buy_val
            total_buy += buy_val
            total_current += cur_val
            df.at[idx, "current_price"] = format_vn(price)
            df.at[idx, "profit_loss"] = format_vn(profit)
        else:
            df.at[idx, "current_price"] = ""
            df.at[idx, "profit_loss"] = "0,00"

        df.at[idx, "total_buy"] = format_vn(total_buy)
        df.at[idx, "total_current"] = format_vn(total_current)

    total_row = {
        "items": "TOTAL", "type": "", "quantity": "", "buy_price": format_vn(total_buy),
        "current_price": format_vn(total_current), "profit_loss": format_vn(total_current - total_buy),
        "total_buy": format_vn(total_buy), "total_current": format_vn(total_current)
    }
    df = pd.concat([df, pd.DataFrame([total_row])], ignore_index=True)

    for c in ["total_buy", "total_current"]:
        if c not in df.columns:
            df[c] = ""
    return df

# ------------------------------------------------------------------
# -------------------------- PLOTLY CHART -------------------------
# ------------------------------------------------------------------
def create_chart(df_display):
    df_plot = df_display[df_display["items"] != "TOTAL"].copy()
    if df_plot.empty:
        return "<p>No data</p>"
    df_plot["pl_num"] = df_plot["profit_loss"].apply(parse_number)
    df_plot = df_plot.sort_values("pl_num", ascending=False)
    fig = px.bar(df_plot, x="items", y="pl_num", labels={"pl_num": "Profit/Loss (VND)", "items": "Item"}, text="profit_loss")
    fig.update_traces(textposition="outside")
    fig.update_layout(title="Profit / Loss by Item", template="plotly_white", height=500,
                      margin=dict(l=40, r=40, t=60, b=100))
    return pio.to_html(fig, full_html=False)

# ------------------------------------------------------------------
# -------------------------- STYLING -----------------------------
# ------------------------------------------------------------------
def style_table(df):
    def color_pl(val):
        try:
            v = parse_number(val)
            return "color:green;font-weight:bold;" if v > 0 else "color:red;font-weight:bold;"
        except: return ""

    def bold_total(row):
        if row.get("items") == "TOTAL":
            return ["font-weight: bold; background-color: #f0f0f0;"] * len(row)
        return [""] * len(row)

    fmt = {c: lambda x: x for c in df.columns if c not in ["action", "items", "type"]}

    styled = (df.style
              .map(color_pl, subset=["profit_loss"])
              .apply(bold_total, axis=1)
              .set_table_attributes('class="table table-sm table-bordered table-hover" id="dataTable"')
              .set_table_styles([
                  {"selector": "th", "props": [("background","#f8f9fa"), ("font-weight","bold"), ("text-align","center")]},
                  {"selector": "td", "props": [("text-align","center")]},
              ])
              .format(fmt))

    html = styled.to_html()

    soup = BeautifulSoup(html, "html.parser")
    for tr, idx in zip(soup.select("tbody tr"), range(len(df))):
        if df.iloc[idx]["items"] != "TOTAL":
            tr["data-idx"] = str(idx)

    return str(soup)

# ------------------------------------------------------------------
# -------------------------- ROUTES -------------------------------
# ------------------------------------------------------------------
HTML_TEMPLATE = """
<!doctype html>
<html lang="vi">
<head>
    <meta charset="utf-8">
    <title>Portfolio Tracker</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>body {padding: 2rem;} .modal-header {background:#e9ecef;}</style>
</head>
<body>
<div class="container">
    <h1 class="mb-4">Portfolio Tracker</h1>
    {% with messages = get_flashed_messages(with_categories=true) %}
      {% if messages %}
        {% for cat, msg in messages %}
          <div class="alert alert-{{'success' if cat=='success' else 'danger'}} alert-dismissible fade show">
            {{ msg }} <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
          </div>
        {% endfor %}
      {% endif %}
    {% endwith %}

    {{ chart|safe }}
    <h2 class="mt-5">Data Table</h2>
    {{ table|safe }}
</div>

<div class="modal fade" id="editModal" tabindex="-1">
  <div class="modal-dialog">
    <form method="post" action="{{ url_for('save_row') }}">
      <div class="modal-content">
        <div class="modal-header">
          <h5 class="modal-title" id="modalTitle">Add Row</h5>
          <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
        </div>
        <div class="modal-body">
          <input type="hidden" name="row_id" id="row_id">
          <div class="mb-3"><label class="form-label">Item (code)</label><input class="form-control" name="items" id="items" required></div>
          <div class="mb-3"><label class="form-label">Type</label>
            <select class="form-select" name="type" id="type" required>
              <option value="fund">Fund</option><option value="stock">Stock</option>
            </select>
          </div>
          <div class="mb-3"><label class="form-label">Quantity</label><input class="form-control" name="quantity" id="quantity" required placeholder="e.g. 100"></div>
          <div class="mb-3"><label class="form-label">Buy Price (VND)</label><input class="form-control" name="buy_price" id="buy_price" required placeholder="e.g. 12.345,67"></div>
        </div>
        <div class="modal-footer">
          <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button>
          <button type="submit" class="btn btn-success">Save</button>
        </div>
      </div>
    </form>
  </div>
</div>

<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
<script>
function prepareAdd(){
    document.getElementById('modalTitle').innerText = 'Add Row';
    document.getElementById('row_id').value = '';
    ['items','type','quantity','buy_price'].forEach(id => document.getElementById(id).value = '');
}
function editRow(idx){
    const row = document.querySelector(`#dataTable tr[data-idx="${idx}"]`);
    if (!row) return;
    document.getElementById('modalTitle').innerText = 'Edit Row';
    document.getElementById('row_id').value = idx;
    const c = row.cells;
    document.getElementById('items').value     = c[0].innerText.trim();
    document.getElementById('type').value      = c[1].innerText.trim().toLowerCase();
    document.getElementById('quantity').value  = c[2].innerText.trim();
    document.getElementById('buy_price').value = c[3].innerText.trim();
    new bootstrap.Modal(document.getElementById('editModal')).show();
}
function deleteRow(idx){
    if(confirm('Delete this row?')){
        window.location = "{{ url_for('delete_row') }}?idx=" + idx;
    }
}
</script>
</body>
</html>
"""

@app.route("/")
def index():
    raw_df = load_csv()
    display_df = recalc_prices_and_profit(raw_df).copy()
    display_df = display_df.reset_index(drop=True)

    # Action buttons
    def btn(idx):
        return f'''
            <div style="white-space:nowrap;">
                <button class="btn btn-sm btn-outline-primary" onclick="editRow({idx})">Edit</button>
                <button class="btn btn-sm btn-outline-danger" onclick="deleteRow({idx})">Delete</button>
            </div>
        '''
    display_df["action"] = ["" if r["items"] == "TOTAL" else btn(i) for i, r in display_df.iterrows()]

    cols = [c for c in display_df.columns if c != "action"] + ["action"]
    display_df = display_df[cols]

    add_btn = '''
    <div class="mb-2 text-end">
        <button class="btn btn-primary btn-sm" data-bs-toggle="modal" data-bs-target="#editModal" onclick="prepareAdd()">Add New Row</button>
    </div>
    '''

    table_html = style_table(display_df)
    table_html = add_btn + table_html
    chart_html = create_chart(display_df)

    return render_template_string(
        HTML_TEMPLATE,
        table=Markup(table_html),
        chart=Markup(chart_html)
    )

@app.route("/save", methods=["POST"])
def save_row():
    idx = request.form.get("row_id")
    items = request.form.get("items", "").strip().upper()
    typ = request.form.get("type", "").strip().lower()
    qty = request.form.get("quantity", "")
    buy = request.form.get("buy_price", "")

    if not all([items, typ in ("fund","stock"), qty, buy]):
        flash("All fields are required and type must be fund/stock.", "danger")
        return redirect(url_for('index'))

    df = load_csv()
    new_row = {"items": items, "type": typ, "quantity": qty, "buy_price": buy,
               "current_price": "", "profit_loss": ""}

    if idx == "" or idx is None:
        df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
        flash("Row added – price will be refreshed.", "success")
    else:
        idx = int(idx)
        if idx >= len(df):
            flash("Row index out of range.", "danger")
        else:
            df.iloc[idx] = new_row
            flash("Row updated.", "success")
    save_csv(df)
    return redirect(url_for('index'))

@app.route("/delete")
def delete_row():
    idx = request.args.get("idx")
    if not idx:
        flash("Missing index.", "danger")
        return redirect(url_for('index'))
    df = load_csv()
    try:
        idx = int(idx)
        if idx >= len(df) or df.iloc[idx]["items"] == "TOTAL":
            raise ValueError
        df = df.drop(df.index[idx]).reset_index(drop=True)
        save_csv(df)
        flash("Row deleted.", "success")
    except:
        flash("Cannot delete that row.", "danger")
    return redirect(url_for('index'))

# ------------------------------------------------------------------
threading.Thread(target=lambda: [time.sleep(1), daily_updater()], daemon=True).start()

def daily_updater():
    while True:
        now = datetime.datetime.now()
        if now.weekday() < 5 and now.hour == 15:
            df = load_csv()
            df = recalc_prices_and_profit(df)
            save_csv(df)
            print(f"Daily update completed – {now:%Y-%m-%d %H:%M}")
            tomorrow = now + datetime.timedelta(days=1)
            next_run = datetime.datetime.combine(tomorrow.date(), datetime.time(15, 0))
            time.sleep(max((next_run - datetime.datetime.now()).total_seconds(), 0))
        else:
            time.sleep(60)

if __name__ == "__main__":
    app.run(debug=True, use_reloader=False)