"""PDF-отчёт по оператору за период. Собирается локально (reportlab), шрифт DejaVu с кириллицей лежит в app/assets/fonts."""

from __future__ import annotations

import io
import json
from datetime import date, datetime
from pathlib import Path

from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.graphics.shapes import Drawing, Line, String
from reportlab.graphics.widgets.markers import makeMarker
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from xml.sax.saxutils import escape

from app.analytics import summary, top_mistakes, weekly_trend
from app.models import Call, Operator
from app.pii import mask_segments, mask_text
from app.quality import ADVICE

FONT_DIR = Path(__file__).resolve().parent / "assets" / "fonts"
FONT, FONT_BOLD = "DejaVuSans", "DejaVuSans-Bold"

INK = colors.HexColor("#142119")
MUTED = colors.HexColor("#6a766d")
LEAF = colors.HexColor("#2d7a51")
GOLD = colors.HexColor("#c9a44a")
BAD = colors.HexColor("#9c3b32")
LINE = colors.HexColor("#d9d3c4")
CARD = colors.HexColor("#faf6ee")

METRIC_RU = [
    ("overall", "Итоговая оценка"),
    ("politeness", "Вежливость"),
    ("completeness", "Полнота ответа"),
    ("script_compliance", "Соблюдение скрипта"),
    ("resolution", "Решение проблемы"),
    ("violation_pct", "Процент нарушений"),
]
EXTRA_ADVICE = {
    "resolution_lang": "Называйте решение прямо: что сделано, что оформлено или что клиенту сделать дальше.",
    "extra_script": "Произносите обязательные фразы скрипта, заданные руководителем.",
    "clarify": ADVICE.get("clarify", ""),
}
TONALITY_RU = {"positive": "позитивная", "neutral": "нейтральная", "negative": "негативная"}


def _fonts() -> None:
    if FONT not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(FONT, str(FONT_DIR / "DejaVuSans.ttf")))
        pdfmetrics.registerFont(TTFont(FONT_BOLD, str(FONT_DIR / "DejaVuSans-Bold.ttf")))
        pdfmetrics.registerFontFamily(FONT, normal=FONT, bold=FONT_BOLD, italic=FONT, boldItalic=FONT_BOLD)


def _styles() -> dict[str, ParagraphStyle]:
    base = ParagraphStyle("base", fontName=FONT, fontSize=9.5, leading=13, textColor=INK, alignment=TA_LEFT)
    return {
        "base": base,
        "small": ParagraphStyle("small", parent=base, fontSize=8, leading=10.5, textColor=MUTED),
        "title": ParagraphStyle("title", parent=base, fontName=FONT_BOLD, fontSize=17, leading=21, spaceAfter=2),
        "h2": ParagraphStyle("h2", parent=base, fontName=FONT_BOLD, fontSize=12, leading=15, spaceBefore=10, spaceAfter=5, textColor=LEAF),
        "h3": ParagraphStyle("h3", parent=base, fontName=FONT_BOLD, fontSize=10, leading=13, spaceBefore=4, spaceAfter=2),
        "line": ParagraphStyle("line", parent=base, fontSize=8.5, leading=11.5, leftIndent=8),
    }


def _num(value, digits: int = 1, suffix: str = "") -> str:
    if value is None:
        return "-"
    return f"{value:.{digits}f}".replace(".", ",") + suffix


def _p(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(str(text)), style)


def _table(rows: list[list], widths: list[float], header: bool = True) -> Table:
    t = Table(rows, colWidths=widths, hAlign="LEFT")
    style = [
        ("FONTNAME", (0, 0), (-1, -1), FONT),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TEXTCOLOR", (0, 0), (-1, -1), INK),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    if header:
        style += [
            ("FONTNAME", (0, 0), (-1, 0), FONT_BOLD),
            ("FONTSIZE", (0, 0), (-1, 0), 8),
            ("TEXTCOLOR", (0, 0), (-1, 0), MUTED),
            ("BACKGROUND", (0, 0), (-1, 0), CARD),
        ]
    t.setStyle(TableStyle(style))
    return t


def _trend_chart(own: list[dict], team: list[dict]) -> Drawing:
    width, height = 170 * mm, 62 * mm
    d = Drawing(width, height)
    weeks = sorted({p["week"] for p in own} | {p["week"] for p in team})
    if not weeks:
        d.add(String(10, height / 2, "Нет оценённых звонков за период", fontName=FONT, fontSize=9, fillColor=MUTED))
        return d
    idx = {w: i for i, w in enumerate(weeks)}
    lp = LinePlot()
    lp.x, lp.y, lp.width, lp.height = 30, 22, width - 50, height - 40
    series = [[(idx[p["week"]], p["overall"]) for p in own if p["overall"] is not None]]
    team_pts = [(idx[p["week"]], p["overall"]) for p in team if p["overall"] is not None]
    if team_pts:
        series.append(team_pts)
    lp.data = series
    lp.joinedLines = 1
    lp.lines[0].strokeColor = LEAF
    lp.lines[0].strokeWidth = 2
    lp.lines[0].symbol = makeMarker("FilledCircle", size=4, fillColor=LEAF, strokeColor=LEAF)
    if team_pts:
        lp.lines[1].strokeColor = GOLD
        lp.lines[1].strokeWidth = 1.2
        lp.lines[1].strokeDashArray = (3, 2)
        lp.lines[1].symbol = makeMarker("Circle", size=3, strokeColor=GOLD)
    lp.xValueAxis.valueMin = -0.3
    lp.xValueAxis.valueMax = max(len(weeks) - 1, 1) + 0.3
    lp.xValueAxis.valueSteps = list(range(len(weeks)))
    lp.xValueAxis.labelTextFormat = lambda v: _week_label(weeks[int(round(v))]) if 0 <= int(round(v)) < len(weeks) else ""
    lp.xValueAxis.labels.fontName = FONT
    lp.xValueAxis.labels.fontSize = 7
    lp.yValueAxis.valueMin = 0
    lp.yValueAxis.valueMax = 100
    lp.yValueAxis.valueStep = 20
    lp.yValueAxis.labels.fontName = FONT
    lp.yValueAxis.labels.fontSize = 7
    lp.yValueAxis.gridStrokeColor = LINE
    lp.yValueAxis.visibleGrid = 1
    d.add(lp)
    legend_y = height - 10
    d.add(Line(30, legend_y, 46, legend_y, strokeColor=LEAF, strokeWidth=2))
    d.add(String(50, legend_y - 3, "оператор", fontName=FONT, fontSize=7.5, fillColor=INK))
    if team_pts:
        d.add(Line(110, legend_y, 126, legend_y, strokeColor=GOLD, strokeWidth=1.2, strokeDashArray=(3, 2)))
        d.add(String(130, legend_y - 3, "команда", fontName=FONT, fontSize=7.5, fillColor=INK))
    d.add(String(190, legend_y - 3, "средний балл за неделю (с понедельника)", fontName=FONT, fontSize=7.5, fillColor=MUTED))
    return d


def _week_label(iso: str) -> str:
    d = date.fromisoformat(iso)
    return d.strftime("%d.%m")


def _checks(call: Call) -> list[dict]:
    try:
        raw = json.loads(call.quality.checks_json or "[]") if call.quality else []
    except (TypeError, ValueError):
        return []
    return [c for c in raw if isinstance(c, dict)]


def _example(call: Call, label: str, st: dict) -> list:
    q = call.quality
    failed = [c.get("title") for c in _checks(call) if not c.get("passed")]
    head = (
        f"{label}: звонок №{call.id}, {call.call_at.strftime('%d.%m.%Y %H:%M')}, "
        f"оценка {_num(q.overall, 0)}, нарушения {_num(q.violation_pct, 0, '%')}"
    )
    items = [_p(head, st["h3"])]
    if failed:
        items.append(_p("Не выполнено: " + ", ".join(failed), st["small"]))
    if q.summary:
        items.append(_p(mask_text(q.summary), st["small"]))
    segs = []
    if call.transcript:
        try:
            segs = json.loads(call.transcript.segments_json or "[]")
        except (TypeError, ValueError):
            segs = []
    segs = mask_segments([s for s in segs if isinstance(s, dict)])
    for s in segs[:8]:
        who = "Оператор" if s.get("role") == "operator" else "Клиент"
        items.append(Paragraph(f"<b>{who}:</b> {escape(str(s.get('text') or ''))}", st["line"]))
    if len(segs) > 8:
        items.append(_p(f"... ещё реплик: {len(segs) - 8}", st["small"]))
    items.append(Spacer(1, 6))
    return [KeepTogether(items)]


WEAK_BELOW = 80  # ниже этой оценки звонок считается слабым (как «требует внимания» в интерфейсе)


def example_picks(calls: list[Call]) -> list[tuple[str, Call]]:
    """До двух слабых звонков (оценка ниже WEAK_BELOW) и лучший. Если слабых нет, честно пишем «самый низкий балл»."""
    if not calls:
        return []
    ordered = sorted(calls, key=lambda c: (c.quality.overall, c.id))
    weak = [c for c in ordered if c.quality.overall < WEAK_BELOW][:2]
    picks: list[tuple[str, Call]] = [("Слабый", c) for c in weak]
    best = ordered[-1]
    if not weak and len(ordered) > 1:
        picks.append(("Самый низкий балл периода", ordered[0]))
    if best not in [c for _, c in picks]:
        picks.append(("Лучший", best))
    return picks


def build_operator_report(
    operator: Operator,
    calls: list[Call],
    team_calls: list[Call],
    date_from: date,
    date_to: date,
    generated_at: datetime,
    rank: int | None = None,
    ranked_total: int = 0,
) -> bytes:
    """calls - оценённые звонки оператора за период, team_calls - все оценённые звонки за период."""
    _fonts()
    st = _styles()
    own = summary(calls)
    team = summary(team_calls)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=f"Отчёт по оператору {operator.name}",
        author="РечьКонтроль",
        subject="Качество обслуживания",
    )
    period = f"{date_from.strftime('%d.%m.%Y')} - {date_to.strftime('%d.%m.%Y')}"
    story: list = [
        _p("РечьКонтроль · отчёт по оператору", st["small"]),
        _p(f"{operator.name}" + (f" ({operator.external_id})" if operator.external_id else ""), st["title"]),
        _p(f"Период: {period}. Сформирован {generated_at.strftime('%d.%m.%Y %H:%M')} локально, без внешних сервисов.", st["small"]),
        Spacer(1, 8),
    ]
    if not own["calls_done"]:
        story.append(_p("За выбранный период у оператора нет оценённых звонков.", st["base"]))
        doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
        return buf.getvalue()

    story.append(_p("Сводка", st["h2"]))
    rows = [["Показатель", "Оператор", "Команда", "Разница"]]
    for key, label in METRIC_RU:
        mine, avg = own[key], team[key]
        diff = None if mine is None or avg is None else round(mine - avg, 1)
        rows.append([label, _num(mine, 1, "%" if key == "violation_pct" else ""), _num(avg, 1, "%" if key == "violation_pct" else ""),
                     "-" if diff is None else ("+" if diff > 0 else "") + _num(diff)])
    rows.append(["Оценено звонков", str(own["calls_done"]), str(team["calls_done"]), ""])
    rows.append(["Среднее время ответа, сек", _num(own["avg_response_sec"], 2), _num(team["avg_response_sec"], 2), ""])
    tone = ", ".join(f"{TONALITY_RU.get(k, k)} {v}" for k, v in sorted(own["tonality"].items())) or "-"
    rows.append(["Тональность звонков", tone, "", ""])
    if rank:
        rows.append(["Место в рейтинге за период", f"{rank} из {ranked_total}", "", ""])
    t = _table(rows, [62 * mm, 42 * mm, 32 * mm, 28 * mm])
    t.setStyle(TableStyle([("ALIGN", (1, 1), (-1, -1), "RIGHT"), ("ALIGN", (1, 0), (-1, 0), "RIGHT")]))
    story.append(t)

    story.append(_p("Динамика по неделям", st["h2"]))
    own_trend = weekly_trend(calls)
    story.append(_trend_chart(own_trend, weekly_trend(team_calls)))
    trows = [["Неделя с", "Звонков", "Средний балл", "Нарушения"]]
    for p in own_trend:
        trows.append([_week_label(p["week"]), str(p["count"]), _num(p["overall"]), _num(p["violation_pct"], 1, "%")])
    tt = _table(trows, [40 * mm, 30 * mm, 40 * mm, 40 * mm])
    tt.setStyle(TableStyle([("ALIGN", (1, 0), (-1, -1), "RIGHT")]))
    story.append(tt)

    story.append(_p("Частые ошибки", st["h2"]))
    mistakes = top_mistakes(calls, 5)
    if mistakes:
        mrows = [["Пункт чек-листа", "Звонков", "Доля", "Что делать"]]
        for m in mistakes:
            mrows.append([
                Paragraph(escape(m["title"]), st["base"]),
                str(m["calls"]),
                _num(m["share"], 0, "%"),
                Paragraph(escape(ADVICE.get(m["id"]) or EXTRA_ADVICE.get(m["id"]) or "-"), st["small"]),
            ])
        mt = _table(mrows, [44 * mm, 18 * mm, 16 * mm, 86 * mm])
        mt.setStyle(TableStyle([("VALIGN", (0, 1), (-1, -1), "TOP"), ("ALIGN", (1, 0), (2, -1), "RIGHT")]))
        story.append(mt)
    else:
        story.append(_p("Все пункты чек-листа выполнены во всех звонках периода.", st["base"]))

    heading = _p("Примеры звонков (персональные данные скрыты)", st["h2"])
    for i, (label, call) in enumerate(example_picks(calls)):
        block = _example(call, label, st)
        if i == 0:
            # заголовок не должен висеть один внизу страницы
            block = [KeepTogether([heading, *block[0]._content])]
        story += block

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()


def _footer(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFont(FONT, 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(18 * mm, 10 * mm, "РечьКонтроль · данные для внутреннего пользования")
    canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"стр. {doc.page}")
    canvas.restoreState()
