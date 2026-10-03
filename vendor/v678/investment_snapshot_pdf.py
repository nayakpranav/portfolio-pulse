"""Private, one-page investment snapshot built only from canonical dashboard outputs.

No transaction classification, valuation, tax, or return methodology lives here.
"""

from __future__ import annotations

from datetime import date, datetime
from io import BytesIO
import calendar
import math

import pandas as pd
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas


BG = HexColor("#081423")
PANEL = HexColor("#112238")
PANEL_2 = HexColor("#142a44")
PANEL_EDGE = HexColor("#24415e")
WHITE = HexColor("#f0f6fc")
MUTED = HexColor("#9aadc3")
GRID = HexColor("#29415a")
CYAN = HexColor("#42cbea")
BLUE = HexColor("#397df5")
GREEN = HexColor("#66d7a6")
AMBER = HexColor("#ffb963")
TEAL_DARK = HexColor("#183d50")
LIME = HexColor("#9be85b")


def _date(value):
    parsed = pd.to_datetime(value, errors="coerce")
    return None if pd.isna(parsed) else parsed.date()


def _number(value):
    parsed = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return None if pd.isna(parsed) or not math.isfinite(float(parsed)) else float(parsed)


def _eur(value):
    return "Unavailable" if value is None else f"EUR {value:,.2f}"


def build_snapshot_data(
    *, dividends: pd.DataFrame, interest: pd.DataFrame, holdings: pd.DataFrame,
    daily_nav: pd.DataFrame, latest_transaction_date, report_date=None,
    reporting_year: int | None = None,
):
    """Prepare presentation-only totals from the existing net ledgers and NAV.

    A month is complete only when its final calendar day is covered by the source
    transaction cutoff and the report date. Months before portfolio inception are
    not counted. The latest transaction date is an evidence cutoff, not a claim
    that future months have zero income.
    """
    report = _date(report_date) or date.today()
    transaction_end = _date(latest_transaction_date)
    cutoff = min(report, transaction_end) if transaction_end else report
    year = int(reporting_year or cutoff.year)
    first_dates = []
    income_rows = []
    for ledger, amount_col, kind in ((dividends, "net_dividend_income_eur", "dividends"),
                                     (interest, "net_interest_eur", "interest")):
        if ledger is None or ledger.empty:
            continue
        if amount_col not in ledger and kind == "dividends":
            amount_col = "net_dividend_eur"
        for row in ledger.to_dict("records"):
            when = _date(row.get("payment_date"))
            amount = _number(row.get(amount_col))
            if when is None:
                continue
            first_dates.append(when)
            if when.year == year and when <= cutoff and amount is not None:
                income_rows.append((when.month, kind, amount))
    nav = daily_nav.copy() if daily_nav is not None else pd.DataFrame()
    if not nav.empty and "date" in nav:
        nav["_date"] = pd.to_datetime(nav["date"], errors="coerce").dt.date
        nav = nav[nav["_date"].notna()]
        first_dates += list(nav["_date"].head(1))
    first_observed = min(first_dates) if first_dates else None
    months = []
    for month in range(1, 13):
        first_day = date(year, month, 1)
        last_day = date(year, month, calendar.monthrange(year, month)[1])
        started = first_observed is not None and first_observed <= first_day
        complete = started and last_day <= cutoff
        partial = first_observed is not None and first_day <= cutoff and not complete
        dividend_total = sum(v for m, k, v in income_rows if m == month and k == "dividends")
        interest_total = sum(v for m, k, v in income_rows if m == month and k == "interest")
        months.append({"month": month, "label": calendar.month_abbr[month],
                       "status": "complete" if complete else "partial" if partial else "unavailable",
                       "dividends": dividend_total if complete or partial else None,
                       "interest": interest_total if complete or partial else None,
                       "total": dividend_total + interest_total if complete or partial else None})
    ytd_dividends = sum(v for _, k, v in income_rows if k == "dividends")
    ytd_interest = sum(v for _, k, v in income_rows if k == "interest")
    completed = [m for m in months if m["status"] == "complete"]
    highest = max(completed, key=lambda m: m["total"]) if completed else None
    average = sum(m["total"] for m in completed) / len(completed) if completed else None
    trend = []
    if not nav.empty and "stockfund_value_eur" in nav:
        for row in nav.to_dict("records"):
            when = row["_date"]
            value = _number(row.get("stockfund_value_eur"))
            if when.year == year and when <= report and value is not None:
                trend.append((when, value))
    trend.sort(key=lambda item: item[0])
    valuation_date = trend[-1][0] if trend else None
    active = []
    if holdings is not None and not holdings.empty:
        for row in holdings.to_dict("records"):
            value = _number(row.get("live_current_value_eur"))
            quantity = _number(row.get("current_quantity"))
            if row.get("position_status") == "ACTIVE" and quantity is not None and quantity > 0 and value is not None:
                active.append({"name": str(row.get("security_name") or "Unnamed security"),
                               "isin": str(row.get("isin") or ""), "value": value})
    total_valued = sum(r["value"] for r in active)
    active.sort(key=lambda r: r["value"], reverse=True)
    top = [{**r, "weight_pct": 100 * r["value"] / total_valued if total_valued > 0 else None}
           for r in active[:10]]
    return {
        "year": year, "report_date": report, "latest_transaction_date": transaction_end,
        "income_cutoff": cutoff, "valuation_date": valuation_date,
        "months": months, "completed_month_count": len(completed),
        "highest_month": highest, "average_completed_monthly_income": average,
        "ytd_dividends": ytd_dividends, "ytd_interest": ytd_interest,
        "ytd_income": ytd_dividends + ytd_interest,
        "trend": trend, "top_holdings": top, "valued_stockfund_total": total_valued,
    }


def _label(c, value, x, y, size=9, color=WHITE, font="Helvetica", align="left"):
    c.setFillColor(color)
    c.setFont(font, size)
    {"left": c.drawString, "right": c.drawRightString, "center": c.drawCentredString}[align](x, y, str(value))


def _panel(c, x, y, w, h):
    c.setFillColor(PANEL)
    c.roundRect(x, y, w, h, 10, fill=1, stroke=0)
    c.setStrokeColor(PANEL_EDGE)
    c.setLineWidth(.5)
    c.roundRect(x+.25, y+.25, w-.5, h-.5, 10, fill=0, stroke=1)


def _pill(c, x, y, w, h, fill, text, text_color=WHITE, size=6.5):
    c.setFillColor(fill)
    c.roundRect(x, y, w, h, h/2, fill=1, stroke=0)
    _label(c, text, x+w/2, y+(h-size)/2+2, size, text_color, "Helvetica-Bold", "center")


def _kpi_icon(c, kind, x, y, color):
    """Small vector glyphs: no font, logo, remote asset or license dependency."""
    c.setFillColor(PANEL_2)
    c.circle(x, y, 13, fill=1, stroke=0)
    c.setStrokeColor(color)
    c.setFillColor(color)
    c.setLineWidth(1.4)
    if kind == 0:  # income path
        p = c.beginPath()
        p.moveTo(x-7, y-4); p.lineTo(x-2, y); p.lineTo(x+2, y-2); p.lineTo(x+7, y+5)
        c.drawPath(p)
        c.line(x+4, y+5, x+7, y+5); c.line(x+7, y+5, x+7, y+2)
    elif kind == 1:  # best month / star
        p = c.beginPath()
        for j in range(10):
            angle = math.pi/2 + j*math.pi/5
            radius = 6.8 if j%2 == 0 else 3.2
            xx, yy = x+radius*math.cos(angle), y+radius*math.sin(angle)
            p.moveTo(xx, yy) if j == 0 else p.lineTo(xx, yy)
        p.close(); c.drawPath(p, fill=0, stroke=1)
    elif kind == 2:  # calendar
        c.roundRect(x-6, y-5, 12, 11, 1.6, fill=0, stroke=1)
        c.line(x-6, y+2, x+6, y+2)
        c.circle(x-2, y-1, .9, fill=1, stroke=0)
        c.circle(x+2, y-1, .9, fill=1, stroke=0)
    else:  # monthly average bars
        for dx, height in ((-5, 4), (0, 8), (5, 6)):
            c.roundRect(x+dx-1.2, y-5, 2.4, height, 1, fill=1, stroke=0)


def _truncate(text, max_width, size=9):
    text = str(text)
    if stringWidth(text, "Helvetica", size) <= max_width:
        return text
    while text and stringWidth(text + "...", "Helvetica", size) > max_width:
        text = text[:-1]
    return text + "..."


def _holding_name_lines(value, max_width, size=6.8):
    """Prefer a recognizable two-line label within a narrow ranked tile."""
    words = str(value).split()
    if not words:
        return ("Unnamed", "")
    first = words[0]
    if stringWidth(first, "Helvetica", size) > max_width:
        return (_truncate(first, max_width, size), _truncate(" ".join(words[1:]), max_width, size))
    index = 1
    while index < len(words) and stringWidth(first + " " + words[index], "Helvetica", size) <= max_width:
        first += " " + words[index]
        index += 1
    return (first, _truncate(" ".join(words[index:]), max_width, size) if index < len(words) else "")


def _snapshot_layout():
    """Three-row A4 grid: header/cards, parallel charts, compact holdings strip."""
    width, _ = landscape(A4)
    margin, gap = 28, 9
    inner = width - 2 * margin
    monthly_w, source_w = 295, 210
    value_w = inner - monthly_w - source_w - 2 * gap
    cards_w = (inner - 3 * gap) / 4
    return {
        "cards": [(margin + i * (cards_w + gap), 450, cards_w, 74) for i in range(4)],
        "monthly": (margin, 162, monthly_w, 278),
        "sources": (margin + monthly_w + gap, 162, source_w, 278),
        "value": (margin + monthly_w + source_w + 2 * gap, 162, value_w, 278),
        "holdings": (margin, 56, inner, 96),
    }


def render_snapshot_pdf(data: dict, *, version: str, output_path=None) -> bytes:
    """Render one vector-first A4 landscape private PDF, with no network calls."""
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=landscape(A4), pageCompression=1)
    c.setTitle(f"Investment Income & Portfolio Snapshot | {version}")
    c.setAuthor("Trade Republic Private Dashboard")
    c.setSubject("Private analytical snapshot; not a broker statement or tax certificate")
    w, h = landscape(A4)
    c.setFillColor(BG)
    c.rect(0, 0, w, h, stroke=0, fill=1)
    layout = _snapshot_layout()
    c.setFillColor(CYAN)
    c.roundRect(28, h-21, 38, 3, 1.5, fill=1, stroke=0)
    c.setFillColor(BLUE)
    c.roundRect(69, h-21, 15, 3, 1.5, fill=1, stroke=0)
    _label(c, "INVESTMENT INCOME & PORTFOLIO SNAPSHOT", 28, h-42, 17.5, WHITE, "Helvetica-Bold")
    _label(c, "Actual investment income and tracked stock/fund assets", 29, h-56, 8.4, MUTED)
    c.setFillColor(PANEL)
    c.roundRect(w-185, h-68, 157, 49, 7, fill=1, stroke=0)
    _label(c, f"REPORT YEAR {data['year']}", w-173, h-33, 7.9, CYAN, "Helvetica-Bold")
    _label(c, version, w-39, h-33, 7.1, MUTED, align="right")
    tx = data.get("latest_transaction_date")
    vd = data.get("valuation_date")
    _label(c, f"Generated {data['report_date']:%d %b %Y}", w-39, h-45, 7.0, WHITE, align="right")
    _label(c, f"Transactions to {tx:%d %b %Y}" if tx else "Transaction date unavailable", w-39, h-56, 6.7, MUTED, align="right")
    _label(c, f"Stock/fund valuation to {vd:%d %b %Y}" if vd else "Historical valuation unavailable", w-39, h-67, 6.7, MUTED, align="right")

    cards = [
        ("NET INVESTMENT INCOME YTD", _eur(data["ytd_income"]), "Dividends + interest"),
        ("HIGHEST COMPLETED MONTH", f"{data['highest_month']['label']} {data['year']}" if data["highest_month"] else "Unavailable",
         _eur(data["highest_month"]["total"]) if data["highest_month"] else "No completed month"),
        ("COMPLETED MONTHS", f"{data['completed_month_count']} / 12", "Source cutoff respected"),
        ("AVG. COMPLETED MONTH", _eur(data["average_completed_monthly_income"]),
         f"Across {data['completed_month_count']} completed months"),
    ]
    for i, (title, value, subtitle) in enumerate(cards):
        x, card_y, card_w, card_h = layout["cards"][i]
        _panel(c, x, card_y, card_w, card_h)
        accent = (LIME, AMBER, BLUE, GREEN)[i]
        c.setFillColor(accent)
        c.roundRect(x+11, card_y+card_h-7, 25, 2.1, 1, fill=1, stroke=0)
        _kpi_icon(c, i, x+card_w-23, card_y+39, accent)
        _label(c, title, x+12, card_y+54, 6.8, MUTED, "Helvetica-Bold")
        _label(c, value, x+12, card_y+28, 17 if len(value) < 18 else 11.5,
               accent if i != 2 else WHITE, "Helvetica-Bold")
        _label(c, subtitle, x+12, card_y+11, 7, MUTED)

    # One canonical actual-net total per month; the source split is adjacent.
    mx, my, mw, mh = layout["monthly"]
    _panel(c, mx, my, mw, mh)
    _label(c, f"MONTHLY INVESTMENT INCOME - {data['year']}", mx+12, my+mh-22, 9.6, WHITE, "Helvetica-Bold")
    _label(c, "Actual net dividends + net interest", mx+12, my+mh-37, 7.2, MUTED)
    c.setFillColor(BLUE)
    c.roundRect(mx+12, my+mh-53, 7, 7, 1.5, fill=1, stroke=0)
    _label(c, "MONTHLY TOTAL", mx+24, my+mh-51, 6.8, BLUE, "Helvetica-Bold")
    best_month = data["highest_month"]["month"] if data["highest_month"] else None
    if best_month is not None:
        best = data["highest_month"]
        _pill(c, mx+mw-97, my+mh-56, 85, 13, PANEL_2,
              f"BEST {best['label'].upper()} {_eur(best['total'])}", AMBER, 5.8)
    chart_x, chart_y, chart_w, chart_h = mx+31, my+62, mw-43, 128
    values = [abs(m["total"]) for m in data["months"] if m["total"] is not None]
    max_v = max(values or [1.0])
    if max_v <= 0:
        max_v = 1.0
    for frac in (0, .5, 1):
        gy = chart_y+frac*chart_h
        c.setStrokeColor(GRID)
        c.setLineWidth(.3)
        c.line(chart_x, gy, chart_x+chart_w, gy)
        _label(c, f"{max_v*frac:,.0f}", chart_x-5, gy-2, 6.2, MUTED, align="right")
    slot = chart_w/12
    for i, m in enumerate(data["months"]):
        cx = chart_x+slot*(i+.5)
        _label(c, m["label"], cx, chart_y-15, 6.6, WHITE if m["month"] == best_month else MUTED, align="center")
        if m["total"] is None:
            c.setFillColor(GRID)
            c.roundRect(cx-5, chart_y+2, 10, 1.6, .8, fill=1, stroke=0)
            continue
        bw = min(12, slot*.58)
        total = m["total"]
        if total >= 0:
            c.setFillColor(BLUE)
            bar_h = chart_h*total/max_v
            if bar_h > 0:
                c.roundRect(cx-bw/2, chart_y, bw, bar_h, 1.7, fill=1, stroke=0)
            if total == 0:
                c.setFillColor(MUTED)
                c.circle(cx, chart_y+2, 2, fill=1, stroke=0)
        else:
            c.setFillColor(AMBER)
            c.roundRect(cx-bw/2, chart_y, bw, 3, 1, fill=1, stroke=0)
        top = chart_y+min(chart_h*abs(total)/max_v, chart_h)
        if m["month"] == best_month:
            c.setStrokeColor(AMBER)
            c.setLineWidth(1.2)
            c.line(cx-bw/2, top+1.3, cx+bw/2, top+1.3)
        _label(c, f"{total:,.0f}", cx, top+6,
               6.4, AMBER if m["month"] == best_month else WHITE, "Helvetica-Bold", "center")
        if m["status"] == "partial":
            _pill(c, cx-12, chart_y-31, 24, 9, PANEL_EDGE, "PARTIAL", AMBER, 4.8)
    c.setStrokeColor(GRID); c.setLineWidth(.45)
    c.line(mx+12, my+30, mx+mw-12, my+30)
    _label(c, f"YTD TO {data['income_cutoff']:%d %b}  {_eur(data['ytd_income'])}", mx+12, my+15, 8.3, WHITE, "Helvetica-Bold")
    _label(c, "Uncovered months are not zero", mx+mw-12, my+15, 5.8, MUTED, align="right")

    # One full-circle base plus one exact proportional overlay: no artificial gap.
    rx, dy, rw, dh = layout["sources"]
    _panel(c, rx, dy, rw, dh)
    _label(c, "INCOME SOURCE BREAKDOWN", rx+11, dy+dh-22, 9.3, WHITE, "Helvetica-Bold")
    _label(c, f"YTD {data['year']} - recognized net income", rx+11, dy+dh-36, 6.9, MUTED)
    dividend, interest = data["ytd_dividends"], data["ytd_interest"]
    combined = dividend+interest
    cx, cy, radius = rx+rw/2, dy+dh-111, 59
    if combined > 0 and dividend >= 0 and interest >= 0:
        c.setFillColor(LIME if dividend > 0 else BLUE)
        c.circle(cx, cy, radius, fill=1, stroke=0)
        # For single-source reports the full circle is the only colored shape.
        if dividend > 0 and interest > 0:
            c.setFillColor(BLUE)
            c.wedge(cx-radius, cy-radius, cx+radius, cy+radius,
                    90, 360*interest/combined, fill=1, stroke=0)
        c.setFillColor(PANEL)
        c.circle(cx, cy, 34, fill=1, stroke=0)
        _label(c, "YTD TOTAL", cx, cy+8, 6.6, MUTED, "Helvetica-Bold", "center")
        _label(c, _eur(combined), cx, cy-7, 10.2, WHITE, "Helvetica-Bold", "center")
        c.setStrokeColor(GRID); c.setLineWidth(.4)
        c.line(rx+12, dy+83, rx+rw-12, dy+83)
        for j, (name, amount, color) in enumerate((('Net dividends', dividend, LIME), ('Net interest', interest, BLUE))):
            yy = dy+65-j*25
            c.setFillColor(color)
            c.circle(rx+18, yy+2, 4, fill=1, stroke=0)
            _label(c, name.upper(), rx+29, yy+2, 7.0, WHITE, "Helvetica-Bold")
            _label(c, f"{100*amount/combined:.1f}%", rx+rw-12, yy+2, 7.2, color, "Helvetica-Bold", "right")
            _label(c, _eur(amount), rx+29, yy-10, 8, color, "Helvetica-Bold")
    else:
        c.setStrokeColor(GRID); c.setLineWidth(8)
        c.circle(cx, cy, 54, fill=0, stroke=1)
        _label(c, "NO POSITIVE SPLIT", cx, cy+3, 7, MUTED, "Helvetica-Bold", "center")
        _label(c, _eur(combined), cx, cy-12, 9, WHITE, "Helvetica-Bold", "center")
        _label(c, "Donut unavailable for zero/negative income", cx, dy+67, 7.1, MUTED, align="center")
        _label(c, f"Dividends {_eur(dividend)}", rx+14, dy+40, 7.3, LIME)
        _label(c, f"Interest {_eur(interest)}", rx+14, dy+22, 7.3, BLUE)

    vx, vy, vw, vh = layout["value"]
    _panel(c, vx, vy, vw, vh)
    _label(c, f"STOCK/FUND PORTFOLIO VALUE - {data['year']}", vx+11, vy+vh-22, 9.0, WHITE, "Helvetica-Bold")
    _label(c, "Historical stock/fund sleeve only", vx+11, vy+vh-36, 7, MUTED)
    trend = data["trend"]
    if trend:
        min_v = min(v for _, v in trend)
        max_v = max(v for _, v in trend)
        span = max(max_v-min_v, 1)
        first_day, last_day = trend[0][0], trend[-1][0]
        change = trend[-1][1]-trend[0][1]
        stat_x, stat_y, stat_gap = vx+10, vy+vh-84, 5
        stat_w = (vw-20-2*stat_gap)/3
        stats = (("START OBSERVED", _eur(trend[0][1]), CYAN),
                 ("LATEST OBSERVED", _eur(trend[-1][1]), WHITE),
                 ("ABS. CHANGE", f"{'+' if change >= 0 else '-'}{_eur(abs(change))}",
                  GREEN if change >= 0 else AMBER))
        for j, (heading, amount, color) in enumerate(stats):
            sx = stat_x+j*(stat_w+stat_gap)
            c.setFillColor(PANEL_2)
            c.roundRect(sx, stat_y, stat_w, 36, 4, fill=1, stroke=0)
            _label(c, heading, sx+5, stat_y+24, 5.6, MUTED, "Helvetica-Bold")
            _label(c, amount, sx+5, stat_y+9, 7.6 if len(amount)<15 else 6.6, color, "Helvetica-Bold")
        px, py, pw, ph = vx+42, vy+57, vw-58, 121
        c.setStrokeColor(GRID)
        c.setLineWidth(.35)
        for frac in (0, .5, 1):
            c.line(px, py+ph*frac, px+pw, py+ph*frac)
        _label(c, f"{max_v:,.0f}", px-5, py+ph-2, 6.2, MUTED, align="right")
        _label(c, f"{(max_v+min_v)/2:,.0f}", px-5, py+ph/2-2, 6.2, MUTED, align="right")
        _label(c, f"{min_v:,.0f}", px-5, py-2, 6.2, MUTED, align="right")
        elapsed = max((last_day-first_day).days, 1)
        points = []
        for d, value in trend:
            xx = px+pw*(d-first_day).days/elapsed
            yy = py+5+(ph-10)*(value-min_v)/span
            points.append((xx, yy))
        area = c.beginPath()
        area.moveTo(points[0][0], py)
        for xx, yy in points:
            area.lineTo(xx, yy)
        area.lineTo(points[-1][0], py)
        area.close()
        c.setFillColor(TEAL_DARK)
        c.drawPath(area, fill=1, stroke=0)
        line = c.beginPath()
        for j, (xx, yy) in enumerate(points):
            line.moveTo(xx, yy) if j == 0 else line.lineTo(xx, yy)
        c.setStrokeColor(CYAN)
        c.setLineWidth(2.0)
        c.setLineJoin(1)
        c.drawPath(line)
        for point, fill in ((points[0], BLUE), (points[-1], CYAN)):
            c.setFillColor(fill)
            c.circle(*point, 3.4, fill=1, stroke=0)
            c.setFillColor(PANEL)
            c.circle(*point, 1.4, fill=1, stroke=0)
        for month in range(1, 13, 2):
            tick = date(data["year"], month, 1)
            if first_day <= tick <= last_day:
                xx = px+pw*(tick-first_day).days/elapsed
                _label(c, calendar.month_abbr[month].upper(), xx, py-14, 6.0, MUTED, align="center")
        _label(c, f"{first_day:%d %b %Y} TO {last_day:%d %b %Y}", vx+12, vy+19, 6.7, MUTED, "Helvetica-Bold")
        _label(c, "Change in value is not investment return", vx+vw-12, vy+19, 6.4, MUTED, align="right")
    else:
        _label(c, "Historical valuation unavailable", vx+12, vy+vh/2, 9, MUTED)

    bx, by, bw, bh = layout["holdings"]
    _panel(c, bx, by, bw, bh)
    _label(c, "TOP 10 HOLDINGS - BY CURRENT VALUE", bx+12, by+bh-20, 10, WHITE, "Helvetica-Bold")
    _label(c, "Weight = share of valued stock/fund assets", bx+bw-12, by+bh-20, 6.8, MUTED, align="right")
    top = data["top_holdings"]
    if not top:
        _label(c, "No active valued stock/fund holdings", bx+12, by+50, 9, MUTED)
    max_weight = max((r["weight_pct"] or 0 for r in top), default=0)
    tile_gap, inset = 4, 10
    tile_w = (bw-2*inset-9*tile_gap)/10
    for i, row in enumerate(top):
        xx, yy = bx+inset+i*(tile_w+tile_gap), by+8
        c.setFillColor(PANEL_2)
        c.roundRect(xx, yy, tile_w, 62, 5, fill=1, stroke=0)
        rank_color = (AMBER, LIME, BLUE)[i] if i < 3 else PANEL_EDGE
        c.setFillColor(rank_color)
        c.circle(xx+11, yy+50, 7.5, fill=1, stroke=0)
        _label(c, f"{i+1:02d}", xx+11, yy+48, 6.3, BG if i < 3 else WHITE, "Helvetica-Bold", "center")
        mono = "".join(ch for ch in row["name"] if ch.isalpha())[:2].upper() or "--"
        _pill(c, xx+tile_w-27, yy+43, 22, 13, PANEL_EDGE, mono, CYAN, 6.0)
        line1, line2 = _holding_name_lines(row["name"], tile_w-12)
        _label(c, line1, xx+6, yy+33, 6.8, WHITE, "Helvetica-Bold")
        if line2:
            _label(c, line2, xx+6, yy+25, 6.8, WHITE, "Helvetica-Bold")
        _label(c, f"{row['weight_pct']:.2f}%", xx+6, yy+11, 10.1, LIME, "Helvetica-Bold")
        c.setFillColor(PANEL_EDGE)
        c.roundRect(xx+6, yy+4, tile_w-12, 2.3, 1.1, fill=1, stroke=0)
        if max_weight > 0:
            c.setFillColor(AMBER if i == 0 else BLUE)
            c.roundRect(xx+6, yy+4, (tile_w-12)*row["weight_pct"]/max_weight,
                        2.3, 1.1, fill=1, stroke=0)

    c.setStrokeColor(PANEL_EDGE)
    c.setLineWidth(.65)
    c.line(28, 50, w-28, 50)
    _pill(c, 28, 34, 68, 12, PANEL_2, "METHODOLOGY", CYAN, 6.0)
    _label(c, "Net income = recognized net dividends + net interest; excludes capital gains and promotions. Reinvested income counted once.", 106, 38, 6.5, MUTED)
    _label(c, "Value = historical stock/fund sleeve (not derivatives or cash). Holdings weights = current value / valued stock/fund total.", 29, 25, 6.5, MUTED)
    _label(c, "Private analytical report - not a broker statement, tax certificate or investment recommendation. Historical values depend on available price coverage.", 29, 13, 6.5, MUTED)
    c.showPage()
    c.save()
    result = buffer.getvalue()
    if output_path is not None:
        from pathlib import Path
        destination = Path(output_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(result)
    return result
