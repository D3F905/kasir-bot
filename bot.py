import os
import json
import base64
import sqlite3
import logging
import requests
from datetime import datetime, date
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from calendar import monthrange

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
ALLOWED_USER_ID = os.environ.get("ALLOWED_USER_ID", "")  # opsional, biar bot private

DB_PATH = os.environ.get("DB_PATH", "kasir.db")

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
        float(data.get("subtotal", 0) or 0),
        float(data.get("tax", 0) or 0),
        float(data.get("total", 0) or 0),
        data.get("category", "Lainnya"),
        data.get("ref", ""),
        data.get("type", "expense"),
        data.get("status", "lunas"),
        data.get("notes", ""),
        datetime.now().isoformat()
    ))
    conn.commit()
    tx_id = c.lastrowid
    conn.close()
    return tx_id

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
            SUM(CASE WHEN type='income' THEN total ELSE 0 END) as income,
            SUM(CASE WHEN type='expense' THEN total ELSE 0 END) as expense,
            COUNT(*) as count
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
    c.execute("""
        SELECT id FROM transactions WHERE user_id=?
        ORDER BY created_at DESC LIMIT 1
    """, (str(user_id),))
    row = c.fetchone()
    if row:
        c.execute("DELETE FROM transactions WHERE id=?", (row[0],))
        conn.commit()
        conn.close()
        return True
    conn.close()
    return False

# ── Gemini OCR ────────────────────────────────────────────
def ocr_invoice(image_bytes, mime_type="image/jpeg"):
    if not GEMINI_API_KEY:
        return None, "GEMINI_API_KEY belum diset di environment variable."

    b64 = base64.b64encode(image_bytes).decode()
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"

    prompt = """Kamu adalah sistem OCR invoice/struk keuangan. Analisis gambar ini dan ekstrak data keuangan.
Kembalikan HANYA JSON berikut (tanpa markdown, tanpa backtick, tanpa penjelasan):
{
  "vendor": "nama toko/vendor/perusahaan",
  "date": "YYYY-MM-DD",
  "subtotal": angka_atau_0,
  "tax": angka_pajak_atau_0,
  "total": angka_total,
  "category": "Makanan & Minuman|Transport|Belanja|Tagihan & Utilitas|Kesehatan|Hiburan|Pendidikan|Bisnis|Invoice|Lainnya",
  "ref": "nomor invoice/ref atau string kosong",
  "type": "expense atau income",
  "status": "lunas atau belum atau pending",
  "notes": "catatan singkat atau string kosong"
}
Jika gambar bukan invoice, tetap kembalikan JSON dengan estimasi terbaik."""

    payload = {
        "contents": [{
            "parts": [
                {"inline_data": {"mime_type": mime_type, "data": b64}},
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
        result = json.loads(text)
        return result, None
    except requests.exceptions.Timeout:
        return None, "Request timeout. Coba lagi."
    except json.JSONDecodeError:
        return None, "Gagal parse response AI. Coba foto yang lebih jelas."
    except Exception as e:
        return None, f"Error: {str(e)}"

# ── Helpers ───────────────────────────────────────────────
def fmt_rp(n):
    try:
        return f"Rp {int(float(n)):,}".replace(",", ".")
    except:
        return "Rp 0"

def is_allowed(user_id):
    if not ALLOWED_USER_ID:
        return True
    return str(user_id) == str(ALLOWED_USER_ID)

def main_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("📊 Hari Ini"), KeyboardButton("📅 Bulan Ini")],
        [KeyboardButton("💰 Belum Dibayar"), KeyboardButton("📈 Tahun Ini")],
        [KeyboardButton("❓ Bantuan")]
    ], resize_keyboard=True)

# ── Handlers ──────────────────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("⛔ Akses tidak diizinkan.")
        return
    name = update.effective_user.first_name
    await update.message.reply_text(
        f"👋 Halo *{name}*\\! Selamat datang di *Kasir Bot*\\.\n\n"
        f"Cara pakai:\n"
        f"📸 *Kirim foto invoice* → AI langsung baca otomatis\n"
        f"📊 Ketik /hari atau /bulan untuk laporan\n"
        f"💰 Ketik /pending untuk tagihan belum dibayar\n"
        f"❌ Ketik /hapus untuk batalkan input terakhir\n\n"
        f"Langsung kirim foto invoice pertama kamu\\!",
        parse_mode="MarkdownV2",
        reply_markup=main_keyboard()
    )

async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    await update.message.reply_text(
        "📖 *Perintah yang tersedia:*\n\n"
        "📸 *Kirim foto* → OCR invoice otomatis\n"
        "📊 /hari → Laporan hari ini\n"
        "📅 /bulan → Laporan bulan ini\n"
        "📈 /tahun → Laporan tahun ini\n"
        "💰 /pending → Tagihan belum dibayar\n"
        "❌ /hapus → Hapus transaksi terakhir\n\n"
        "Atau tekan tombol di bawah\\!",
        parse_mode="MarkdownV2",
        reply_markup=main_keyboard()
    )

async def handle_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("⛔ Akses tidak diizinkan.")
        return

    await update.message.reply_text("🔍 Lagi baca invoice lo\\.\\.\\.", parse_mode="MarkdownV2")

    photo = update.message.photo[-1]
    file = await ctx.bot.get_file(photo.file_id)
    image_bytes = await file.download_as_bytearray()

    result, error = ocr_invoice(bytes(image_bytes))

    if error:
        await update.message.reply_text(f"❌ Gagal membaca invoice: {error}")
        return

    tx_id = save_tx(update.effective_user.id, result)

    tx_type = "💸 Pengeluaran" if result.get("type") == "expense" else "💰 Pemasukan"
    status_emoji = {"lunas": "✅", "belum": "⏳", "pending": "🔄"}.get(result.get("status", "lunas"), "✅")
    status_label = {"lunas": "Lunas", "belum": "Belum dibayar", "pending": "Pending"}.get(result.get("status", "lunas"), "Lunas")

    msg = (
        f"✅ *Invoice berhasil dicatat\\!*\n\n"
        f"🏪 *Vendor:* {escape_md(result.get('vendor', '-'))}\n"
        f"📅 *Tanggal:* {result.get('date', '-')}\n"
        f"🏷️ *Kategori:* {escape_md(result.get('category', '-'))}\n"
    )
    if result.get("subtotal"):
        msg += f"💵 *Subtotal:* {escape_md(fmt_rp(result['subtotal']))}\n"
    if result.get("tax"):
        msg += f"🧾 *Pajak:* {escape_md(fmt_rp(result['tax']))}\n"
    msg += (
        f"💰 *Total:* {escape_md(fmt_rp(result.get('total', 0)))}\n"
        f"{tx_type}\n"
        f"{status_emoji} *Status:* {status_label}\n"
    )
    if result.get("ref"):
        msg += f"🔖 *Ref:* {escape_md(result['ref'])}\n"
    if result.get("notes"):
        msg += f"📝 *Catatan:* {escape_md(result['notes'])}\n"
    msg += f"\n_Ketik /hapus kalau mau batalin input ini_"

    await update.message.reply_text(msg, parse_mode="MarkdownV2", reply_markup=main_keyboard())

async def laporan(update: Update, ctx: ContextTypes.DEFAULT_TYPE, period="month"):
    if not is_allowed(update.effective_user.id):
        return
    user_id = update.effective_user.id
    data = get_summary(user_id, period)

    period_label = {"today": "Hari Ini", "month": "Bulan Ini", "year": "Tahun Ini"}.get(period, "Semua")
    emoji = {"today": "📊", "month": "📅", "year": "📈"}.get(period, "📊")

    balance = data["income"] - data["expense"]
    bal_emoji = "🟢" if balance >= 0 else "🔴"

    msg = (
        f"{emoji} *Laporan {period_label}*\n"
        f"{'─' * 25}\n"
        f"💰 Pemasukan:   {escape_md(fmt_rp(data['income']))}\n"
        f"💸 Pengeluaran: {escape_md(fmt_rp(data['expense']))}\n"
        f"{bal_emoji} Saldo:       {escape_md(fmt_rp(balance))}\n"
        f"📋 Transaksi:   {data['count']} transaksi\n"
    )

    if data["categories"]:
        msg += f"\n🏷️ *Top Kategori Pengeluaran:*\n"
        for cat, total in data["categories"]:
            bar = "█" * min(int(total / max(data["expense"], 1) * 10), 10)
            msg += f"  • {escape_md(cat)}: {escape_md(fmt_rp(total))}\n"

    if data["recent"]:
        msg += f"\n🕐 *Transaksi Terakhir:*\n"
        for vendor, tx_date, total, tx_type, status in data["recent"]:
            sign = "\\-" if tx_type == "expense" else "\\+"
            st = {"lunas": "✅", "belum": "⏳", "pending": "🔄"}.get(status, "✅")
            msg += f"  {st} {escape_md(vendor)} {sign}{escape_md(fmt_rp(total))}\n"

    await update.message.reply_text(msg, parse_mode="MarkdownV2", reply_markup=main_keyboard())

async def hari(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await laporan(update, ctx, "today")

async def bulan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await laporan(update, ctx, "month")

async def tahun(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await laporan(update, ctx, "year")

async def pending(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    rows = get_pending(update.effective_user.id)
    if not rows:
        await update.message.reply_text("✅ Tidak ada tagihan yang belum dibayar\\!", parse_mode="MarkdownV2", reply_markup=main_keyboard())
        return
    total = sum(r[2] for r in rows)
    msg = f"⏳ *Tagihan Belum Dibayar*\n{'─'*25}\n"
    for vendor, tx_date, tx_total, ref in rows:
        msg += f"  • {escape_md(vendor)} — {escape_md(fmt_rp(tx_total))}"
        if ref:
            msg += f" \\({escape_md(ref)}\\)"
        msg += f"\n    📅 {tx_date}\n"
    msg += f"\n💰 *Total: {escape_md(fmt_rp(total))}*"
    await update.message.reply_text(msg, parse_mode="MarkdownV2", reply_markup=main_keyboard())

async def hapus(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    ok = delete_last_tx(update.effective_user.id)
    if ok:
        await update.message.reply_text("✅ Transaksi terakhir berhasil dihapus\\.", parse_mode="MarkdownV2", reply_markup=main_keyboard())
    else:
        await update.message.reply_text("❌ Tidak ada transaksi yang bisa dihapus\\.", parse_mode="MarkdownV2", reply_markup=main_keyboard())

async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    text = update.message.text
    if "hari" in text.lower():
        await laporan(update, ctx, "today")
    elif "bulan" in text.lower():
        await laporan(update, ctx, "month")
    elif "tahun" in text.lower():
        await laporan(update, ctx, "year")
    elif "belum" in text.lower() or "pending" in text.lower():
        await pending(update, ctx)
    elif "bantuan" in text.lower() or "help" in text.lower():
        await help_cmd(update, ctx)
    else:
        await update.message.reply_text(
            "📸 Kirim foto invoice untuk dicatat, atau tekan tombol di bawah untuk laporan\\.",
            parse_mode="MarkdownV2",
            reply_markup=main_keyboard()
        )

def escape_md(text):
    text = str(text)
    chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    for c in chars:
        text = text.replace(c, f'\\{c}')
    return text

# ── Main ──────────────────────────────────────────────────
def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("hari", hari))
    app.add_handler(CommandHandler("bulan", bulan))
    app.add_handler(CommandHandler("tahun", tahun))
    app.add_handler(CommandHandler("pending", pending))
    app.add_handler(CommandHandler("hapus", hapus))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    logger.info("Bot started!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
