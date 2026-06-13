import os
import json
import base64
import sqlite3
import logging
import requests
import asyncio
from datetime import datetime, date

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
ALLOWED_USER_ID = os.environ.get("ALLOWED_USER_ID", "")
DB_PATH = "kasir.db"

# ── Database ──────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            vendor TEXT,
            date TEXT,
            subtotal REAL DEFAULT 0,
            tax REAL DEFAULT 0,
            total REAL NOT NULL,
            category TEXT,
            ref TEXT,
            type TEXT DEFAULT 'expense',
            status TEXT DEFAULT 'lunas',
            notes TEXT,
            created_at TEXT
        )
    """)
    conn.commit()
    conn.close()

def save_tx(user_id, data):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT INTO transactions (user_id, vendor, date, subtotal, tax, total, category, ref, type, status, notes, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        str(user_id),
        data.get("vendor", "Unknown"),
        data.get("date", str(date.today())),
        float(data.get("subtotal") or 0),
        float(data.get("tax") or 0),
        float(data.get("total") or 0),
        data.get("category", "Lainnya"),
        data.get("ref", ""),
        data.get("type", "expense"),
        data.get("status", "lunas"),
        data.get("notes", ""),
        datetime.now().isoformat()
    ))
    conn.commit()
    conn.close()

def get_summary(user_id, period="month"):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    today = date.today()

    if period == "today":
        date_filter = f"AND date = '{today}'"
    elif period == "month":
        date_filter = f"AND strftime('%Y-%m', date) = '{today.strftime('%Y-%m')}'"
    elif period == "year":
        date_filter = f"AND strftime('%Y', date) = '{today.strftime('%Y')}'"
    else:
        date_filter = ""

    c.execute(f"""
        SELECT 
            SUM(CASE WHEN type='income' THEN total ELSE 0 END),
            SUM(CASE WHEN type='expense' THEN total ELSE 0 END),
            COUNT(*)
        FROM transactions 
        WHERE user_id=? {date_filter}
    """, (str(user_id),))
    row = c.fetchone()

    c.execute(f"""
        SELECT category, SUM(total) as total
        FROM transactions
        WHERE user_id=? AND type='expense' {date_filter}
        GROUP BY category
        ORDER BY total DESC
        LIMIT 5
    """, (str(user_id),))
    cats = c.fetchall()

    c.execute(f"""
        SELECT vendor, date, total, type, status
        FROM transactions
        WHERE user_id=? {date_filter}
        ORDER BY created_at DESC
        LIMIT 5
    """, (str(user_id),))
    recent = c.fetchall()
    conn.close()

    return {
        "income": row[0] or 0,
        "expense": row[1] or 0,
        "count": row[2] or 0,
        "categories": cats,
        "recent": recent
    }

def get_pending(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT vendor, date, total, ref FROM transactions
        WHERE user_id=? AND status='belum'
        ORDER BY date ASC
    """, (str(user_id),))
    rows = c.fetchall()
    conn.close()
    return rows

def delete_last_tx(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id FROM transactions WHERE user_id=? ORDER BY created_at DESC LIMIT 1", (str(user_id),))
    row = c.fetchone()
    if row:
        c.execute("DELETE FROM transactions WHERE id=?", (row[0],))
        conn.commit()
        conn.close()
        return True
    conn.close()
    return False

# ── Gemini OCR ────────────────────────────────────────────
def ocr_invoice(image_bytes):
    if not GEMINI_API_KEY:
        return None, "GEMINI_API_KEY belum diset."

    b64 = base64.b64encode(image_bytes).decode()
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"

    prompt = """Kamu adalah sistem OCR invoice/struk keuangan. Analisis gambar dan ekstrak data.
Kembalikan HANYA JSON ini (tanpa markdown, tanpa backtick):
{"vendor":"nama toko","date":"YYYY-MM-DD","subtotal":0,"tax":0,"total":0,"category":"Makanan & Minuman|Transport|Belanja|Tagihan & Utilitas|Kesehatan|Hiburan|Bisnis|Invoice|Lainnya","ref":"","type":"expense","status":"lunas","notes":""}"""

    payload = {
        "contents": [{
            "parts": [
                {"inline_data": {"mime_type": "image/jpeg", "data": b64}},
                {"text": prompt}
            ]
        }]
    }

    try:
        res = requests.post(url, json=payload, timeout=30)
        res.raise_for_status()
        data = res.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        text = text.replace("```json", "").replace("```", "").strip()
        return json.loads(text), None
    except Exception as e:
        return None, str(e)

# ── Helpers ───────────────────────────────────────────────
def fmt_rp(n):
    try:
        return "Rp {:,}".format(int(float(n))).replace(",", ".")
    except:
        return "Rp 0"

def is_allowed(user_id):
    if not ALLOWED_USER_ID:
        return True
    return str(user_id) == str(ALLOWED_USER_ID)

def esc(text):
    text = str(text)
    for c in r'_*[]()~`>#+-=|{}.!':
        text = text.replace(c, f'\\{c}')
    return text

# ── Bot Handlers ──────────────────────────────────────────
async def start(update, context):
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("Akses tidak diizinkan.")
        return
    name = update.effective_user.first_name
    await update.message.reply_text(
        f"Halo {name}! Selamat datang di Kasir Bot.\n\n"
        "Cara pakai:\n"
        "📸 Kirim foto invoice/struk → AI baca otomatis\n"
        "📊 /hari → Laporan hari ini\n"
        "📅 /bulan → Laporan bulan ini\n"
        "📈 /tahun → Laporan tahun ini\n"
        "💰 /pending → Tagihan belum dibayar\n"
        "❌ /hapus → Hapus transaksi terakhir\n\n"
        "Langsung kirim foto invoice pertama kamu!"
    )

async def handle_photo(update, context):
    if not is_allowed(update.effective_user.id):
        return

    await update.message.reply_text("🔍 Lagi baca invoice lo...")

    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    image_bytes = await file.download_as_bytearray()

    result, error = ocr_invoice(bytes(image_bytes))

    if error:
        await update.message.reply_text(f"❌ Gagal membaca: {error}")
        return

    save_tx(update.effective_user.id, result)

    status_label = {"lunas": "✅ Lunas", "belum": "⏳ Belum dibayar", "pending": "🔄 Pending"}.get(result.get("status", "lunas"), "✅ Lunas")
    tx_type = "💸 Pengeluaran" if result.get("type") == "expense" else "💰 Pemasukan"

    msg = (
        f"✅ Invoice berhasil dicatat!\n\n"
        f"🏪 Vendor: {result.get('vendor', '-')}\n"
        f"📅 Tanggal: {result.get('date', '-')}\n"
        f"🏷️ Kategori: {result.get('category', '-')}\n"
    )
    if result.get("subtotal"):
        msg += f"💵 Subtotal: {fmt_rp(result['subtotal'])}\n"
    if result.get("tax"):
        msg += f"🧾 Pajak: {fmt_rp(result['tax'])}\n"
    msg += (
        f"💰 Total: {fmt_rp(result.get('total', 0))}\n"
        f"{tx_type}\n"
        f"{status_label}\n"
    )
    if result.get("ref"):
        msg += f"🔖 Ref: {result['ref']}\n"
    if result.get("notes"):
        msg += f"📝 {result['notes']}\n"
    msg += "\nKetik /hapus kalau mau batalin input ini"

    await update.message.reply_text(msg)

async def laporan_cmd(update, context, period="month"):
    if not is_allowed(update.effective_user.id):
        return
    data = get_summary(update.effective_user.id, period)
    label = {"today": "Hari Ini", "month": "Bulan Ini", "year": "Tahun Ini"}.get(period)
    balance = data["income"] - data["expense"]
    bal_mark = "🟢" if balance >= 0 else "🔴"

    msg = (
        f"📊 Laporan {label}\n"
        f"{'─'*25}\n"
        f"💰 Pemasukan:   {fmt_rp(data['income'])}\n"
        f"💸 Pengeluaran: {fmt_rp(data['expense'])}\n"
        f"{bal_mark} Saldo:       {fmt_rp(balance)}\n"
        f"📋 Transaksi:   {data['count']}\n"
    )
    if data["categories"]:
        msg += "\n🏷️ Top Kategori:\n"
        for cat, total in data["categories"]:
            msg += f"  • {cat}: {fmt_rp(total)}\n"
    if data["recent"]:
        msg += "\n🕐 Terakhir:\n"
        for vendor, tx_date, total, tx_type, status in data["recent"]:
            sign = "-" if tx_type == "expense" else "+"
            st = {"lunas": "✅", "belum": "⏳", "pending": "🔄"}.get(status, "✅")
            msg += f"  {st} {vendor} {sign}{fmt_rp(total)}\n"

    await update.message.reply_text(msg)

async def hari(update, context):
    await laporan_cmd(update, context, "today")

async def bulan(update, context):
    await laporan_cmd(update, context, "month")

async def tahun(update, context):
    await laporan_cmd(update, context, "year")

async def pending(update, context):
    if not is_allowed(update.effective_user.id):
        return
    rows = get_pending(update.effective_user.id)
    if not rows:
        await update.message.reply_text("✅ Tidak ada tagihan yang belum dibayar!")
        return
    total = sum(r[2] for r in rows)
    msg = "⏳ Tagihan Belum Dibayar\n" + "─"*25 + "\n"
    for vendor, tx_date, tx_total, ref in rows:
        msg += f"• {vendor} — {fmt_rp(tx_total)}"
        if ref:
            msg += f" ({ref})"
        msg += f"\n  📅 {tx_date}\n"
    msg += f"\n💰 Total: {fmt_rp(total)}"
    await update.message.reply_text(msg)

async def hapus(update, context):
    if not is_allowed(update.effective_user.id):
        return
    ok = delete_last_tx(update.effective_user.id)
    if ok:
        await update.message.reply_text("✅ Transaksi terakhir berhasil dihapus.")
    else:
        await update.message.reply_text("❌ Tidak ada transaksi yang bisa dihapus.")

async def handle_text(update, context):
    if not is_allowed(update.effective_user.id):
        return
    text = update.message.text.lower()
    if "hari" in text:
        await laporan_cmd(update, context, "today")
    elif "bulan" in text:
        await laporan_cmd(update, context, "month")
    elif "tahun" in text:
        await laporan_cmd(update, context, "year")
    elif "pending" in text or "belum" in text:
        await pending(update, context)
    else:
        await update.message.reply_text("📸 Kirim foto invoice untuk dicatat, atau ketik /bulan untuk laporan.")

# ── Main ──────────────────────────────────────────────────
def main():
    from telegram.ext import Application, CommandHandler, MessageHandler, filters

    init_db()
    logger.info("Starting bot...")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("hari", hari))
    app.add_handler(CommandHandler("bulan", bulan))
    app.add_handler(CommandHandler("tahun", tahun))
    app.add_handler(CommandHandler("pending", pending))
    app.add_handler(CommandHandler("hapus", hapus))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Bot running!")
    app.run_polling(allowed_updates=["message"])

if __name__ == "__main__":
    main()
