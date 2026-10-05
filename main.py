import os
import threading
import asyncio
import requests
import edge_tts
from flask import Flask
from google import genai
from moviepy.editor import VideoFileClip, AudioFileClip, TextClip, CompositeVideoClip
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# ---------------------------------------------------------
# ۱. بخش وب‌سرور (برای زنده نگه داشتن سرور در Render)
# ---------------------------------------------------------
web_app = Flask(__name__)

@web_app.route('/')
def home():
    return "ربات هوش مصنوعی فعال است!", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    web_app.run(host='0.0.0.0', port=port)

# ---------------------------------------------------------
# ۲. دریافت کلیدهای امنیتی از متغیرهای محیطی Render
# ---------------------------------------------------------
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
PEXELS_KEY = os.environ.get("PEXELS_API_KEY")
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

# ---------------------------------------------------------
# ۳. توابع هوش مصنوعی و ساخت ویدیو
# ---------------------------------------------------------
def generate_script(topic):
    client = genai.Client(api_key=GEMINI_KEY)
    prompt = f"یک سناریوی بسیار جذاب زیر ۳۰ ثانیه برای یوتیوب شورتس درباره '{topic}' بنویس. فقط متن گوینده را بفرست."
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=prompt
    )
    return response.text

async def generate_audio(text, output_file="voice.mp3"):
    communicate = edge_tts.Communicate(text, "fa-IR-FaridNeural")
    await communicate.save(output_file)
    return output_file

def download_bg_video(query="nature", output_file="bg.mp4"):
    headers = {"Authorization": PEXELS_KEY}
    url = f"https://api.pexels.com/videos/search?query={query}&orientation=portrait&per_page=1"
    res = requests.get(url, headers=headers).json()
    
    if res.get("videos"):
        video_url = res["videos"][0]["video_files"][0]["link"]
        video_data = requests.get(video_url).content
        with open(output_file, "wb") as f:
            f.write(video_data)
        return output_file
    return None

def assemble_short(script_text, audio_path, video_path, output_file="final_short.mp4"):
    audio = AudioFileClip(audio_path)
    video = VideoFileClip(video_path).subclip(0, audio.duration)
    video = video.resize(newsize=(1080, 1920))

    txt_clip = TextClip(script_text, fontsize=40, color='white', bg_color='black', size=(900, None), method='caption')
    txt_clip = txt_clip.set_position(('center', 'center')).set_duration(audio.duration)

    final_clip = CompositeVideoClip([video, txt_clip]).set_audio(audio)
    final_clip.write_videofile(output_file, fps=24, codec="libx264")
    return output_file

# ---------------------------------------------------------
# ۴. دستورات ربات تلگرام
# ---------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("سلام! برای ساخت ویدیو دستور زیر رو بفرست:\n/make موضوع ویدیو")

async def make_short(update: Update, context: ContextTypes.DEFAULT_TYPE):
    topic = " ".join(context.args)
    if not topic:
        await update.message.reply_text("لطفاً یک موضوع وارد کن. مثال:\n/make حقایق مریخ")
        return

    msg = await update.message.reply_text("⏳ در حال تولید متن، صدا و مونتاژ ویدیو...")

    try:
        # ۱. ساخت متن
        script = generate_script(topic)
        # ۲. ساخت صدا
        await generate_audio(script, "voice.mp3")
        # ۳. دانلود ویدیو
        download_bg_video(topic, "bg.mp4")
        # ۴. رندر نهایی
        final_video = assemble_short(script, "voice.mp3", "bg.mp4")

        # ارسال فایل ویدیو به چت تلگرام
        with open(final_video, 'rb') as video_file:
            await update.message.reply_video(
                video=video_file,
                caption=f"🎬 **ویدیو شما آماده شد!**\n\n📝 **سناریو:**\n{script}"
            )
    except Exception as e:
        await update.message.reply_text(f"❌ خطایی رخ داد: {str(e)}")

# ---------------------------------------------------------
# ۵. اجرای هم‌زمان وب‌سرور و ربات تلگرام
# ---------------------------------------------------------
if __name__ == '__main__':
    # روشن کردن وب سرور در یک مسیر جداگانه
    threading.Thread(target=run_flask, daemon=True).start()

    # روشن کردن ربات تلگرام
    bot_app = ApplicationBuilder().token(BOT_TOKEN).build()
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.run_polling()
