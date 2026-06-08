from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak, Image
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.enums import TA_CENTER
import os

PDF = "/root/dalnoboy/Dalnoboy_Bros_Presentation.pdf"
LOGO = "/var/www/dalnoboy/assets/logo.png"

pdfmetrics.registerFont(TTFont("DejaVu", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"))
pdfmetrics.registerFont(TTFont("DejaVuBold", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"))

W, H = landscape(A4)

doc = SimpleDocTemplate(
    PDF,
    pagesize=landscape(A4),
    rightMargin=45,
    leftMargin=45,
    topMargin=35,
    bottomMargin=35
)

title = ParagraphStyle("title", fontName="DejaVuBold", fontSize=34, leading=40, textColor=colors.white, alignment=TA_CENTER)
h = ParagraphStyle("h", fontName="DejaVuBold", fontSize=28, leading=34, textColor=colors.white)
body = ParagraphStyle("body", fontName="DejaVu", fontSize=17, leading=24, textColor=colors.HexColor("#e5e7eb"))
center = ParagraphStyle("center", fontName="DejaVu", fontSize=18, leading=26, textColor=colors.HexColor("#cbd5e1"), alignment=TA_CENTER)

def bg(canvas, doc):
    canvas.saveState()
    canvas.setFillColor(colors.HexColor("#0f172a"))
    canvas.rect(0, 0, W, H, fill=1, stroke=0)
    canvas.setFillColor(colors.HexColor("#1e293b"))
    canvas.roundRect(25, 25, W-50, H-50, 18, fill=1, stroke=0)
    canvas.restoreState()

def add_slide(story, heading, lines, logo=False):
    if logo and os.path.exists(LOGO):
        img = Image(LOGO, width=150, height=150)
        img.hAlign = "CENTER"
        story.append(img)
        story.append(Spacer(1, 16))

    story.append(Paragraph(heading, title if logo else h))
    story.append(Spacer(1, 22))

    if isinstance(lines, list):
        for line in lines:
            story.append(Paragraph("• " + line, body))
            story.append(Spacer(1, 9))
    else:
        story.append(Paragraph(lines, center if logo else body))

    story.append(PageBreak())

story = []

add_slide(story, "DALNOBOY BROS",
          "Цифровая платформа грузоперевозок<br/>От идеи до работающего MVP за один месяц<br/><br/>Смоленск • Россия • 2026",
          logo=True)

add_slide(story, "Проблема рынка", [
    "Перевозчики ищут грузы через чаты и звонки.",
    "Диспетчеры ведут сделки вручную.",
    "Заявки теряются, история не сохраняется.",
    "Нет единой системы, рейтингов и прозрачности."
])

add_slide(story, "Наше решение", [
    "Telegram-бот для быстрого входа пользователей.",
    "Карта грузов и транспорта.",
    "Сделки, отклики и чат внутри платформы.",
    "CRM-кабинет для диспетчера.",
    "Документы, фото транспорта и журнал действий."
])

add_slide(story, "Что уже создано", [
    "Telegram Bot.",
    "PostgreSQL база данных.",
    "Node.js API.",
    "Web CRM.",
    "Dispatcher Panel.",
    "GitHub и VPS-инфраструктура."
])

add_slide(story, "Роли пользователей", [
    "Перевозчик - размещает машину и ищет грузы.",
    "Грузоотправитель - размещает грузы и получает отклики.",
    "Диспетчер - ведёт клиентов, сделки и документы.",
    "Администратор - управляет платформой и тарифами."
])

add_slide(story, "Монетизация v1.0", [
    "На старте монетизация только через подписки.",
    "Перевозчик PRO - платный доступ для водителей.",
    "Грузоотправитель BUSINESS - платный доступ для размещения грузов.",
    "Диспетчеры пока бесплатны, потому что помогают росту сети."
])

add_slide(story, "Тарифы", [
    "Carrier FREE: 1 машина, радиус 150 км.",
    "Carrier PRO: до 3 машин, радиус 500 км, выгодные грузы.",
    "Shipper FREE: 1 активный груз.",
    "Shipper BUSINESS: до 20 грузов, VIP и приоритет."
])

add_slide(story, "Что сделано за месяц", [
    "От идеи перешли к работающему продукту.",
    "Создали бота, базу, API и веб-кабинет.",
    "Добавили карту, сделки, чат и документы.",
    "Сформировали первую коммерческую модель."
])

add_slide(story, "Цель ближайшего этапа", [
    "50 зарегистрированных пользователей.",
    "10 реальных перевозок.",
    "10 перевозчиков.",
    "5 грузоотправителей.",
    "3 диспетчера.",
    "1 первый платный тариф."
])

add_slide(story, "Финал",
          "Мы не создаём ещё один чат для перевозчиков.<br/><br/>Мы создаём цифровую экосистему грузоперевозок.",
          logo=True)

doc.build(story, onFirstPage=bg, onLaterPages=bg)

print("PDF готов:", PDF)
