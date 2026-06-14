import os
import json
import base64
import sqlite3
import logging
import requests
from datetime import datetime, date

import telebot

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
OPENROUTER_KEY = os.environ.get("OPENROUTER_KEY", "")
ALLOWED_USER_ID = os.environ.get("ALLOWED_USER_ID", "")
DB_PATH = "kasir.db"

bot = telebot.TeleBot(BOT_TOKEN)

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

# ── OCR via OpenRouter ────────────────────────────────────
def ocr_invoice(image_bytes):
    if not OPENROUTER_KEY:
        return None, "OPENROUTER_KEY belum diset di Railway Variables."

    b64 = base64.b64encode(image_bytes).decode()

    prompt = """Kamu adalah sistem OCR invoice/struk keuangan. Analisis gambar dan ekstrak data keuangan.
Kembalikan HANYA JSON berikut (tanpa markdown, tanpa backtick, langsung JSON mentah):
{"vendor":"nama toko/vendor","date":"YYYY-MM-DD","subtotal":0,"tax":0,"total":0,"category":"Makanan & Minuman","ref":"","type":"expense","status":"lunas","notes":""}
Pilihan category: Makanan & Minuman, Transport, Belanja, Tagihan & Utilitas, Kesehatan, Hiburan, Bisnis, Invoice, Lainnya
Jika tidak ada tanggal gunakan hari ini. Jika tidak ada pajak isi 0."""

    payload = {
        "model": "google/gemini-2.0-flash-thinking-exp:free",
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{b64}"
                        }
                    },
                    {
                        "type": "text",
                        "text": prompt
                    }
                ]
            }
        ]
    }

    headers = {
        "Authorization": f"Bearer {OPENROUTER_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://kasir-bot.railway.app",
        "X-Title": "Kasir Bot"
    }

    try:
        res = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            json=payload,
            headers=headers,
            timeout=30
        )
        res.raise_for_status()
        data = res.json()
        text = data["choices"][0]["message"]["content"]
        text = text.replace("```json", "").replace("```", "").strip()
        return json.loads(text), None
    except Exception as e:
        logger.error(f"OCR error: {e}")
        return None, f"Gagal baca invoice: {str(e)[:100]}"

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

def laporan_text(user_id, period="month"):
    data = get_summary(user_id, period)
    label = {"today": "Hari Ini", "month": "Bulan Ini", "year": "Tahun Ini"}.get(period, "Semua")
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
    return msg

# ── Handlers ──────────────────────────────────────────────
@bot.message_handler(commands=["start"])
def start(message):
    if not is_allowed(message.from_user.id):
        bot.reply_to(message, "Akses tidak diizinkan.")
        return
    name = message.from_user.first_name
    bot.reply_to(message,
        f"Halo {name}! Selamat datang di Kasir Bot 💰\n\n"
        "Cara pakai:\n"
        "📸 Kirim foto invoice/struk → AI baca otomatis\n"
        "📊 /hari → Laporan hari ini\n"
        "📅 /bulan → Laporan bulan ini\n"
        "📈 /tahun → Laporan tahun ini\n"
        "💰 /pending → Tagihan belum dibayar\n"
        "❌ /hapus → Hapus transaksi terakhir\n\n"
        "Langsung kirim foto invoice pertama kamu!"
    )

@bot.message_handler(commands=["hari"])
def hari(message):
    if not is_allowed(message.from_user.id): return
    bot.reply_to(message, laporan_text(message.from_user.id, "today"))

@bot.message_handler(commands=["bulan"])
def bulan(message):
    if not is_allowed(message.from_user.id): return
    bot.reply_to(message, laporan_text(message.from_user.id, "month"))

@bot.message_handler(commands=["tahun"])
def tahun(message):
    if not is_allowed(message.from_user.id): return
    bot.reply_to(message, laporan_text(message.from_user.id, "year"))

@bot.message_handler(commands=["pending"])
def pending(message):
    if not is_allowed(message.from_user.id): return
    rows = get_pending(message.from_user.id)
    if not rows:
        bot.reply_to(message, "✅ Tidak ada tagihan yang belum dibayar!")
        return
    total = sum(r[2] for r in rows)
    msg = "⏳ Tagihan Belum Dibayar\n" + "─"*25 + "\n"
    for vendor, tx_date, tx_total, ref in rows:
        msg += f"• {vendor} — {fmt_rp(tx_total)}"
        if ref:
            msg += f" ({ref})"
        msg += f"\n  📅 {tx_date}\n"
    msg += f"\n💰 Total: {fmt_rp(total)}"
    bot.reply_to(message, msg)

@bot.message_handler(commands=["hapus"])
def hapus(message):
    if not is_allowed(message.from_user.id): return
    ok = delete_last_tx(message.from_user.id)
    if ok:
        bot.reply_to(message, "✅ Transaksi terakhir berhasil dihapus.")
    else:
        bot.reply_to(message, "❌ Tidak ada transaksi yang bisa dihapus.")

@bot.message_handler(content_types=["photo"])
def handle_photo(message):
    if not is_allowed(message.from_user.id): return
    bot.reply_to(message, "🔍 Lagi baca invoice lo...")
    try:
        file_info = bot.get_file(message.photo[-1].file_id)
        downloaded = bot.download_file(file_info.file_path)
        result, error = ocr_invoice(downloaded)
        if error:
            bot.reply_to(message, f"❌ {error}")
            return
        save_tx(message.from_user.id, result)
        status_label = {"lunas": "✅ Lunas", "belum": "⏳ Belum dibayar", "pending": "🔄 Pending"}.get(result.get("status", "lunas"), "✅ Lunas")
        tx_type = "💸 Pengeluaran" if result.get("type") == "expense" else "💰 Pemasukan"
        msg = f"✅ Invoice berhasil dicatat!\n\n"
        msg += f"🏪 Vendor: {result.get('vendor', '-')}\n"
        msg += f"📅 Tanggal: {result.get('date', '-')}\n"
        msg += f"🏷️ Kategori: {result.get('category', '-')}\n"
        if result.get("subtotal"):
            msg += f"💵 Subtotal: {fmt_rp(result['subtotal'])}\n"
        if result.get("tax"):
            msg += f"🧾 Pajak: {fmt_rp(result['tax'])}\n"
        msg += f"💰 Total: {fmt_rp(result.get('total', 0))}\n"
        msg += f"{tx_type}\n"
        msg += f"{status_label}\n"
        if result.get("ref"):
            msg += f"🔖 Ref: {result['ref']}\n"
        if result.get("notes"):
            msg += f"📝 {result['notes']}\n"
        msg += "\nKetik /hapus kalau mau batalin"
        bot.reply_to(message, msg)
    except Exception as e:
        logger.error(f"Error: {e}")
        bot.reply_to(message, f"❌ Error: {str(e)[:100]}")

@bot.message_handler(func=lambda m: True)
def handle_text(message):
    if not is_allowed(message.from_user.id): return
    text = message.text.lower()
    if "hari" in text:
        bot.reply_to(message, laporan_text(message.from_user.id, "today"))
    elif "bulan" in text:
        bot.reply_to(message, laporan_text(message.from_user.id, "month"))
    elif "tahun" in text:
        bot.reply_to(message, laporan_text(message.from_user.id, "year"))
    elif "pending" in text or "belum" in text:
        pending(message)
    else:
        bot.reply_to(message, "📸 Kirim foto invoice untuk dicatat, atau ketik /bulan untuk laporan.")

# ── Main ──────────────────────────────────────────────────
if __name__ == "__main__":
    init_db()
    logger.info("Bot started!")
    bot.infinity_polling(timeout=30, long_polling_timeout=30)
