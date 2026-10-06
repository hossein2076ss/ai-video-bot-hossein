import os
import re
import random
import asyncio
import logging
import tempfile
import textwrap
import threading
import time

import numpy as np
import requests
import edge_tts
import arabic_reshaper
from flask import Flask
from PIL import Image, ImageDraw, ImageFont
from moviepy import VideoFileClip, AudioFileClip, ImageClip, CompositeVideoClip, vfx
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

try:
    from bidi import get_display  # python-bidi >= 0.5
except ImportError:
    from bidi.algorithm import get_display  # نسخه‌های قدیمی

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("shorts-bot")

# ---------------------------------------------------------
# ۱. تنظیمات (همه رایگان)
# ---------------------------------------------------------
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
COVERR_KEY = os.environ.get("COVERR_API_KEY")  # اختیاری؛ بدون آن پس‌زمینه گرادیان ساخته می‌شود
PROXY_URL = os.environ.get("PROXY_URL")  # اختیاری؛ مثال: socks5://127.0.0.1:1080 (اگر ربات داخل ایران اجرا می‌شود)

W, H = 720, 1280  # رزولوشن سبک‌تر برای هاست‌های رایگان (RAM کم)
VOICE = "fa-IR-FaridNeural"  # صدای فارسی رایگان مایکروسافت
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FONT_PATH = os.path.join(BASE_DIR, "font.ttf")
FONT_URLS = [
    "https://github.com/rastikerdar/vazirmatn/raw/master/fonts/ttf/Vazirmatn-Bold.ttf",
    "https://cdn.jsdelivr.net/gh/rastikerdar/vazirmatn@master/fonts/ttf/Vazirmatn-Bold.ttf",
]
SYSTEM_FONTS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "C:/Windows/Fonts/tahomabd.ttf",
    "C:/Windows/Fonts/tahoma.ttf",
]

RENDER_LOCK = asyncio.Semaphore(1)  # هر بار فقط یک ویدیو (جلوگیری از پر شدن RAM)

# ---------------------------------------------------------
# ۲. وب‌سرور زنده نگه‌دارنده
# ---------------------------------------------------------
web_app = Flask(__name__)


@web_app.route("/")
@web_app.route("/healthz")
def home():
    return "ربات فعال است!", 200


def run_flask():
    port = int(os.environ.get("PORT", 8080))
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    web_app.run(host="0.0.0.0", port=port)


# ---------------------------------------------------------
# ۳. فونت فارسی
# ---------------------------------------------------------
def ensure_font():
    """فونت وزیرمتن را یک‌بار دانلود می‌کند؛ اگر نشد از فونت سیستم یا font.ttf استفاده می‌کند."""
    if os.path.exists(FONT_PATH):
        return FONT_PATH
    for url in FONT_URLS:
        try:
            r = requests.get(url, timeout=30)
            if r.status_code == 200 and len(r.content) > 50_000:
                with open(FONT_PATH, "wb") as f:
                    f.write(r.content)
                log.info("فونت فارسی دانلود شد.")
                return FONT_PATH
        except Exception as e:
            log.warning("دانلود فونت ناموفق: %s", e)
    for p in SYSTEM_FONTS:
        if os.path.exists(p):
            return p
    raise RuntimeError("فونت فارسی پیدا نشد. یک فایل فونت فارسی (مثل Vazirmatn) را با نام font.ttf کنار main.py بگذارید.")


# ---------------------------------------------------------
# ۴. تولید متن + کلمه کلیدی انگلیسی (رایگان و بدون کلید)
# ---------------------------------------------------------
def generate_script(topic):
    prompt = (
        f"برای یک ویدیوی کوتاه (کمتر از ۳۰ ثانیه) درباره‌ی «{topic}» بنویس.\n"
        "قالب خروجی دقیقاً این‌طور باشد:\n"
        "خط اول: فقط ۲ تا ۳ کلمه‌ی انگلیسی برای جستجوی ویدیوی پس‌زمینه‌ی مرتبط (مثلاً: galaxy space).\n"
        "از خط دوم به بعد: متن گوینده به فارسی روان، جذاب و هیجان‌انگیز، حداکثر ۶۵ کلمه.\n"
        "هیچ مقدمه، توضیح، ایموجی یا علامت مارک‌داون ننویس."
    )
    payload = {"messages": [{"role": "user", "content": prompt}], "model": "openai"}

    last_err = None
    for attempt in range(4):
        try:
            res = requests.post("https://text.pollinations.ai/", json=payload, timeout=60)
            text = res.text.strip()
            if res.status_code == 200 and len(text) > 20 and not text.startswith("{"):
                return parse_script(text)
            last_err = f"status={res.status_code}"
        except Exception as e:
            last_err = e
        log.warning("تلاش %d برای تولید متن ناموفق بود: %s", attempt + 1, last_err)
        time.sleep(16)  # محدودیت رایگان: تقریباً یک درخواست در هر ۱۵ ثانیه

    raise RuntimeError("ارتباط با موتور هوش مصنوعی برقرار نشد. چند دقیقه بعد دوباره تلاش کنید.")


def parse_script(text):
    text = re.sub(r"[*#_`~>\[\]]", "", text)
    text = re.sub(r"[\U00010000-\U0010ffff\u2600-\u27bf]", "", text)  # حذف ایموجی
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    keywords = "nature"
    if lines:
        first = re.sub(r"^(keywords?|کلمات کلیدی)\s*[:：]\s*", "", lines[0], flags=re.I)
        if re.fullmatch(r"[A-Za-z0-9 ,\-]{2,40}", first):
            keywords = first
            lines = lines[1:]

    script = " ".join(lines).strip()
    if not script:
        raise RuntimeError("متن خالی تولید شد.")
    return script, keywords


# ---------------------------------------------------------
# ۵. صدا
# ---------------------------------------------------------
async def generate_audio(text, output_file):
    try:
        await edge_tts.Communicate(text, VOICE).save(output_file)
        if os.path.getsize(output_file) > 1000:
            return output_file
    except Exception as e:
        log.warning("edge-tts ناموفق بود، تلاش با gTTS: %s", e)

    def _gtts():
        from gtts import gTTS
        gTTS(text=text, lang="fa").save(output_file)

    await asyncio.to_thread(_gtts)
    return output_file


# ---------------------------------------------------------
# ۶. ویدیوی پس‌زمینه
# ---------------------------------------------------------
def download_background(keywords, out_path):
    if not COVERR_KEY:
        return None
    headers = {"Authorization": f"Bearer {COVERR_KEY}"}
    for q in (keywords, "nature"):
        try:
            r = requests.get(
                "https://api.coverr.co/videos",
                params={"query": q, "urls": "true", "page_size": 20},
                headers=headers,
                timeout=30,
            )
            r.raise_for_status()
            hits = r.json().get("hits", [])
            if not hits:
                continue
            vertical = [h for h in hits if h.get("is_vertical")]
            urls = random.choice(vertical or hits).get("urls", {})
            video_url = urls.get("mp4") or urls.get("mp4_download")
            if not video_url:
                continue
            with requests.get(video_url, stream=True, timeout=60) as vr:
                vr.raise_for_status()
                with open(out_path, "wb") as f:
                    for chunk in vr.iter_content(1 << 16):
                        f.write(chunk)
            return out_path
        except Exception as e:
            log.warning("دانلود ویدیو (%s) ناموفق: %s", q, e)
    return None


def build_background(video_path, duration):
    if video_path:
        try:
            clip = VideoFileClip(video_path).without_audio()
            if clip.duration < duration:
                clip = clip.with_effects([vfx.Loop(duration=duration)])
            else:
                clip = clip.subclipped(0, duration)
            scale = max(W / clip.w, H / clip.h)  # پر کردن کادر عمودی بدون کشیدگی
            clip = clip.resized(scale)
            return clip.cropped(x_center=clip.w / 2, y_center=clip.h / 2, width=W, height=H)
        except Exception as e:
            log.warning("پردازش ویدیوی پس‌زمینه ناموفق، گرادیان جایگزین شد: %s", e)

    y = np.linspace(0, 1, H)[:, None, None]
    top, bottom = np.array([20, 24, 60]), np.array([90, 30, 120])
    frame = ((top * (1 - y) + bottom * y) * np.ones((1, W, 1))).astype("uint8")
    return ImageClip(frame).with_duration(duration)


# ---------------------------------------------------------
# ۷. زیرنویس فارسی (اتصال حروف + راست‌به‌چپ)
# ---------------------------------------------------------
def render_caption(text, font_path, font_size=54, max_chars=22):
    font = ImageFont.truetype(font_path, font_size, layout_engine=ImageFont.Layout.BASIC)
    lines = textwrap.wrap(text, width=max_chars) or [text]
    visual = "\n".join(get_display(arabic_reshaper.reshape(l)) for l in lines)

    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    l, t, r, b = probe.multiline_textbbox((0, 0), visual, font=font, align="center", spacing=14, stroke_width=4)
    pad = 20
    img = Image.new("RGBA", (int(r - l) + 2 * pad, int(b - t) + 2 * pad), (0, 0, 0, 0))
    ImageDraw.Draw(img).multiline_text(
        (pad - l, pad - t), visual, font=font, fill="white",
        stroke_width=4, stroke_fill="black", align="center", spacing=14,
    )
    return np.array(img)


def split_chunks(script, max_len=60):
    parts = re.split(r"(?<=[.!?؟۔،,;؛:])\s+", script)
    chunks, cur = [], ""
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if cur and len(cur) + len(p) + 1 > max_len:
            chunks.append(cur)
            cur = p
        else:
            cur = f"{cur} {p}".strip()
    if cur:
        chunks.append(cur)
    return chunks or [script]


# ---------------------------------------------------------
# ۸. مونتاژ
# ---------------------------------------------------------
def assemble_short(script, audio_path, video_path, out_path, font_path, tmp_dir):
    audio = AudioFileClip(audio_path)
    duration = audio.duration
    bg = build_background(video_path, duration)

    chunks = split_chunks(script)
    total = sum(len(c) for c in chunks)
    caps, t = [], 0.0
    for c in chunks:
        d = duration * len(c) / total
        caps.append(
            ImageClip(render_caption(c, font_path))
            .with_start(t).with_duration(d)
            .with_position(("center", 0.62), relative=True)
        )
        t += d

    final = CompositeVideoClip([bg, *caps], size=(W, H)).with_duration(duration).with_audio(audio)
    try:
        final.write_videofile(
            out_path, fps=24, codec="libx264", audio_codec="aac",
            preset="ultrafast", threads=2, logger=None,
            temp_audiofile=os.path.join(tmp_dir, "temp_audio.m4a"),
            ffmpeg_params=["-pix_fmt", "yuv420p", "-movflags", "+faststart"],
        )
    finally:
        for c in (final, bg, audio, *caps):
            try:
                c.close()
            except Exception:
                pass
    return out_path, duration


# ---------------------------------------------------------
# ۹. دستورات تلگرام
# ---------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("سلام! ربات آماده است. دستور:\n/make موضوع ویدیو")


async def make_short(update: Update, context: ContextTypes.DEFAULT_TYPE):
    topic = " ".join(context.args).strip()
    if not topic:
        await update.message.reply_text("لطفاً یک موضوع وارد کن. مثال:\n/make حقایق کهکشان")
        return

    status = await update.message.reply_text("⏳ در صف... اگر ویدیوی دیگری در حال ساخت باشد کمی صبر کن.")
    try:
        async with RENDER_LOCK:
            await status.edit_text("⏳ در حال ساخت متن، صدا و ویدیو (حدود ۱ تا ۳ دقیقه)...")
            font_path = await asyncio.to_thread(ensure_font)
            script, keywords = await asyncio.to_thread(generate_script, topic)

            with tempfile.TemporaryDirectory() as tmp:
                audio_path = await generate_audio(script, os.path.join(tmp, "voice.mp3"))
                video_path = await asyncio.to_thread(
                    download_background, keywords, os.path.join(tmp, "bg.mp4")
                )
                out_path, duration = await asyncio.to_thread(
                    assemble_short, script, audio_path, video_path,
                    os.path.join(tmp, "final_short.mp4"), font_path, tmp,
                )

                caption = f"🎬 ویدیو آماده شد!\n\n📝 متن:\n{script}"[:1000]
                with open(out_path, "rb") as f:
                    await update.message.reply_video(
                        video=f, caption=caption, supports_streaming=True,
                        width=W, height=H, duration=int(duration),
                        read_timeout=180, write_timeout=180,
                    )
        await status.delete()
    except Exception as e:
        log.exception("خطا در ساخت ویدیو")
        await update.message.reply_text(f"❌ خطایی رخ داد: {e}")


# ---------------------------------------------------------
# ۱۰. اجرا
# ---------------------------------------------------------
if __name__ == "__main__":
    if not BOT_TOKEN:
        raise SystemExit("متغیر TELEGRAM_BOT_TOKEN تنظیم نشده است.")

    threading.Thread(target=run_flask, daemon=True).start()

    builder = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .connect_timeout(30).read_timeout(60).write_timeout(120).pool_timeout(30)
    )
    if PROXY_URL:  # تلگرام در ایران فیلتر است؛ اگر ربات داخل ایران اجرا می‌شود پروکسی لازم است
        builder = builder.proxy(PROXY_URL).get_updates_proxy(PROXY_URL)

    bot_app = builder.build()
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(CommandHandler("make", make_short))
    bot_app.run_polling(drop_pending_updates=True)
