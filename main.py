import os
import threading
import asyncio
import requests
import edge_tts
from flask import Flask
from google import genai
from moviepy import VideoFileClip, AudioFileClip, TextClip, CompositeVideoClip
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
# ۲. دریافت کلیدهای امنیتی از Environment Variables
# ---------------------------------------------------------
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
COVERR_KEY = os.environ.get("COVERR_API_KEY")
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

# ---------------------------------------------------------
# ۳. توابع هوش مصنوعی، دانلود و مونتاژ ویدیو
# ---------------------------------------------------------
def generate_script(topic):
    client = genai.Client(api_key=GEMINI_KEY)
    prompt = f"یک سناریوی بسیار جذاب زیر ۳۰ ثانیه برای یوتیوب شورتس درباره '{topic}' بنویس. فقط متن گوینده را بفرست."
    
    # استفاده از مدل فعال و رسمی gemini-1.5-flash
    response = client.models.generate_content(
        model='gemini-1.5-flash',
        contents=prompt
    )
    return response.text

async def generate_audio(text, output_file="voice.mp3"):
    communicate = edge_tts.Communicate(text, "fa-IR-FaridNeural")
    await communicate.save(output_file)
    return output_file

def download_bg_video_coverr(query="nature", output_file="bg.mp4"):
    url = f"https://api.coverr.co/videos?query={query}&urls=true"
    headers = {"Authorization": f"Bearer {COVERR_KEY}"}
    
    try:
        res = requests.get(url, headers=headers).json()
        if res.get("hits") and len(res["hits"]) > 0:
            video_url = res["hits"][0]["urls"]["mp4"]
        else:
            url_fallback = "https://api.coverr.co/videos?query=abstract&urls=true"
            res_fb = requests.get(url_fallback, headers=headers).json()
            video_url = res_fb["hits"][0]["urls"]["mp4"]
            
        video_data = requests.get(video_url).content
        with open(output_file, "wb") as f:
            f.write(video_data)
        return output_file
    except Exception as e:
        print(f"Error downloading video: {e}")
        return None

def assemble_short(script_text, audio_path, video_path, output_file="final_short.mp4"):
    audio = AudioFileClip(audio_path)
    
    video = VideoFileClip(video_path).subclipped(0, audio.duration)
    video = video.resized(new_size=(1080, 1920))

    txt_clip = TextClip(
        text=script_text, 
        font_size=40, 
        color='white', 
        bg_color='black', 
        size=(900, None), 
        method='caption'
    )
    txt_clip = txt_clip.with_position(('center', 'center')).with_duration(audio.duration)

    final_clip = CompositeVideoClip([video, txt_clip]).with_audio(audio)
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

    await update.message.reply_text("⏳ در حال تولید متن، صدا و مونتاژ ویدیو...")

    try:
        script = generate_script(topic)
        await generate_audio(script, "voice.mp3")
        download_bg_video_coverr(topic, "bg.mp4")
        final_video = assemble_short(script, "voice.mp3", "bg.mp4")

        with open(final_video, 'rb') as video_file:
            await update.message.reply_video(
                video=video_file,
                caption=f"🎬 **ویدیو شما آماده شد!**\n\n📝 **سناریو:**\n{script}"
            )
    except Exception as e:
        await update.message.reply_text(f"❌ خطایی رخ داد: {str(e)}")

# ---------------------------------------------------------
# ۵. اجرای برنامه
# ---------------------------------------------------------
if __name__ == '__main__':
    threading.Thread(target=run_flask, daemon=True).start()

    bot_app = ApplicationBuilder().token(BOT_TOKEN).build()
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(CommandHandler("make", make_short))
    bot_app.run_polling()
