import os
import telebot
import time
import threading
import requests
import json
from datetime import datetime, timedelta, timezone as tz
from flask import Flask, request
import google.generativeai as genai

# Фикс для Python 3.12+
import pkgutil
if not hasattr(pkgutil, 'get_loader'):
    import importlib
    pkgutil.get_loader = lambda name: importlib.util.find_spec(name)

# ===== КОНФИГУРАЦИЯ =====
TOKEN = os.environ.get('TELEGRAM_TOKEN', '')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '')
ADMIN_ID = 5852338439
GROUP_ID = -5263534968
STATIC_URL = 'https://swill-ai-bot.onrender.com'
MINSK = tz(timedelta(hours=3))

# Gemini
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-1.5-flash')

bot = telebot.TeleBot(TOKEN, threaded=False)
app = Flask(__name__)

# ===== БАЗА =====
banned = []
user_stats = {}
user_chats = {}

def load_data():
    global banned
    banned = []
    try:
        chat = bot.get_chat(GROUP_ID)
        if chat.pinned_message and chat.pinned_message.text:
            data = json.loads(chat.pinned_message.text)
            banned = [str(b) for b in data.get('banned', [])]
    except Exception as e:
        print(f'Load error: {e}')

def save_data():
    try:
        data = {'banned': banned}
        text = json.dumps(data, ensure_ascii=False)
        chat = bot.get_chat(GROUP_ID)
        if chat.pinned_message:
            try:
                bot.unpin_chat_message(GROUP_ID, chat.pinned_message.message_id)
            except:
                pass
        msg = bot.send_message(GROUP_ID, text)
        bot.pin_chat_message(GROUP_ID, msg.message_id, disable_notification=True)
    except Exception as e:
        print(f'Save error: {e}')

load_data()

# ===== УСТАНОВКА КОМАНД =====
def set_commands():
    bot.set_my_commands([
        telebot.types.BotCommand('start', '🚀 Запуск'),
        telebot.types.BotCommand('newchat', '🆕 Новый чат'),
    ])
    bot.set_my_commands([
        telebot.types.BotCommand('start', '🚀 Запуск'),
        telebot.types.BotCommand('newchat', '🆕 Новый чат'),
        telebot.types.BotCommand('stats', '📊 Статистика'),
        telebot.types.BotCommand('users', '👥 Пользователи'),
        telebot.types.BotCommand('ban', '⛔ Забанить'),
        telebot.types.BotCommand('unban', '✅ Разбанить'),
        telebot.types.BotCommand('broadcast', '📢 Рассылка'),
    ], scope=telebot.types.BotCommandScopeChat(ADMIN_ID))

# ===== ПИНГ =====
def self_ping():
    while True:
        time.sleep(540)
        try:
            requests.get(STATIC_URL, timeout=5)
        except:
            pass

threading.Thread(target=self_ping, daemon=True).start()

# ===== СБРОС today В 00:00 =====
def reset_daily():
    while True:
        time.sleep(3600)
        now = datetime.now(MINSK)
        today_str = now.strftime('%d.%m.%Y')
        for uid in user_stats:
            if user_stats[uid].get('date') != today_str:
                user_stats[uid]['today'] = 0
                user_stats[uid]['date'] = today_str

threading.Thread(target=reset_daily, daemon=True).start()

# ===== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =====
def get_username(uid):
    try:
        user = bot.get_chat(uid)
        return f"@{user.username}" if user.username else f"ID:{uid}"
    except:
        return f"ID:{uid}"

def update_stats(uid, stat_type):
    if uid not in user_stats:
        user_stats[uid] = {'total': 0, 'today': 0, 'text': 0, 'images': 0, 'photo_analysis': 0, 'date': datetime.now(MINSK).strftime('%d.%m.%Y')}
    user_stats[uid]['total'] += 1
    user_stats[uid]['today'] += 1
    if stat_type in user_stats[uid]:
        user_stats[uid][stat_type] += 1

def log_to_group(uid, prompt, response_text=None, image_analysis=False):
    name = get_username(uid)
    time_str = datetime.now(MINSK).strftime('%H:%M %d.%m.%Y')
    
    if image_analysis:
        log_text = f"👤 {name} ({uid})\n📷 Тип: Анализ фото\n📥 Запрос: {prompt[:200]}\n📤 Ответ: {response_text[:200] if response_text else '...'}\n🕐 {time_str}"
    else:
        log_text = f"👤 {name} ({uid})\n📝 Тип: Текст\n📥 Запрос: {prompt[:200]}\n📤 Ответ: {response_text[:200] if response_text else '...'}\n🕐 {time_str}"
    
    try:
        bot.send_message(GROUP_ID, log_text)
    except Exception as e:
        print(f'Log error: {e}')

def ask_gemini(uid, prompt, image=None):
    try:
        content = [prompt]
        if image:
            content.append(image)
        
        response = model.generate_content(content)
        return response.text, None
    except Exception as e:
        return f"Ошибка: {str(e)[:500]}", None

def show_stats_page(chat_id, page, users, total):
    per_page = 4
    total_pages = (len(users) + per_page - 1) // per_page
    start = page * per_page
    end = start + per_page
    page_users = users[start:end]
    
    markup = telebot.types.InlineKeyboardMarkup(row_width=2)
    row = []
    for u in page_users:
        name = get_username(u)
        row.append(telebot.types.InlineKeyboardButton(name, callback_data=f'stats_user_{u}_{page}'))
        if len(row) == 2:
            markup.row(*row)
            row = []
    if row:
        markup.row(*row)
    
    nav = []
    if page > 0:
        nav.append(telebot.types.InlineKeyboardButton('◀️', callback_data=f'stats_page_{page-1}'))
    else:
        nav.append(telebot.types.InlineKeyboardButton('◀️', callback_data='noop'))
    if page < total_pages - 1:
        nav.append(telebot.types.InlineKeyboardButton('▶️', callback_data=f'stats_page_{page+1}'))
    else:
        nav.append(telebot.types.InlineKeyboardButton('▶️', callback_data='noop'))
    markup.row(*nav)
    
    bot.send_message(chat_id, f'📊 Всего запросов: {total}', reply_markup=markup)

# ===== КОМАНДЫ =====
@bot.message_handler(commands=['start'])
def start(message):
    uid = str(message.chat.id)
    if uid in banned:
        bot.send_message(uid, '⛔ Вы заблокированы администратором.')
        return
    
    bot.send_message(uid, '🚀 SWILL AI активирован.\nЗадайте вопрос текстом или отправьте фото для анализа.\n/newchat — начать новый чат.')

@bot.message_handler(commands=['newchat'])
def newchat(message):
    uid = str(message.chat.id)
    if uid in banned:
        bot.send_message(uid, '⛔ Вы заблокированы администратором.')
        return
    
    if uid not in user_chats:
        user_chats[uid] = {'active': 1, 'chats': {1: {'name': 'Основной', 'history': []}}}
    
    new_id = max(user_chats[uid]['chats'].keys()) + 1
    user_chats[uid]['chats'][new_id] = {'name': 'Основной', 'history': []}
    user_chats[uid]['active'] = new_id
    
    bot.send_message(uid, f'🆕 Новый чат создан (чат #{new_id}). Задайте вопрос.')

@bot.message_handler(commands=['stats'])
def stats_cmd(message):
    uid = str(message.chat.id)
    if uid != str(ADMIN_ID):
        return
    
    if not user_stats:
        bot.send_message(uid, '📊 Нет данных.')
        return
    
    total = sum(v['total'] for v in user_stats.values())
    total_text = sum(v.get('text', 0) for v in user_stats.values())
    total_images = sum(v.get('images', 0) for v in user_stats.values())
    total_photo = sum(v.get('photo_analysis', 0) for v in user_stats.values())
    
    summary = f"📊 Статистика SWILL AI:\n\n├— Всего запросов: {total}\n├— Текстовых: {total_text}\n├— Картинок: {total_images}\n├— Анализов фото: {total_photo}\n└— Активных юзеров: {len(user_stats)}"
    bot.send_message(uid, summary)
    
    users = list(user_stats.keys())
    show_stats_page(uid, 0, users, total)

@bot.message_handler(commands=['users'])
def users_cmd(message):
    uid = str(message.chat.id)
    if uid != str(ADMIN_ID):
        return
    
    if not user_stats:
        bot.send_message(uid, '👥 Нет пользователей.')
        return
    
    text = '👥 Пользователи бота:\n\n'
    for u, stats in sorted(user_stats.items(), key=lambda x: x[1]['total'], reverse=True):
        name = get_username(u)
        text += f'{name} ({u}) — {stats["total"]} запросов\n'
    
    bot.send_message(uid, text)

@bot.message_handler(commands=['ban'])
def ban_cmd(message):
    uid = str(message.chat.id)
    if uid != str(ADMIN_ID):
        return
    
    args = message.text.split()
    if len(args) < 2:
        bot.send_message(uid, 'Укажите ID или @username: /ban 123456789')
        return
    
    target = args[1].replace('@', '')
    found = None
    for u in user_stats:
        if u == target:
            found = u
            break
    if not found:
        for u in user_stats:
            try:
                user = bot.get_chat(u)
                if user.username and user.username.lower() == target.lower():
                    found = u
                    break
            except:
                pass
    
    if not found:
        bot.send_message(uid, '❌ Пользователь не найден.')
        return
    if found in banned:
        bot.send_message(uid, '❌ Уже забанен.')
        return
    
    banned.append(found)
    save_data()
    name = get_username(found)
    bot.send_message(GROUP_ID, f'⛔ {name}/{found} забанен')
    bot.send_message(uid, f'⛔ {name}/{found} забанен.')
    try:
        bot.send_message(found, '⛔ Вы заблокированы администратором.')
    except:
        pass

@bot.message_handler(commands=['unban'])
def unban_cmd(message):
    uid = str(message.chat.id)
    if uid != str(ADMIN_ID):
        return
    
    args = message.text.split()
    if len(args) < 2:
        bot.send_message(uid, 'Укажите ID или @username: /unban 123456789')
        return
    
    target = args[1].replace('@', '')
    found = None
    for u in banned:
        if u == target:
            found = u
            break
    if not found:
        for u in banned:
            try:
                user = bot.get_chat(u)
                if user.username and user.username.lower() == target.lower():
                    found = u
                    break
            except:
                pass
    
    if not found:
        bot.send_message(uid, '❌ Не найден в бане.')
        return
    
    banned.remove(found)
    save_data()
    name = get_username(found)
    bot.send_message(GROUP_ID, f'✅ {name}/{found} разбанен')
    bot.send_message(uid, f'✅ {name}/{found} разбанен.')
    try:
        bot.send_message(found, '✅ Вы разблокированы.')
    except:
        pass

@bot.message_handler(commands=['broadcast'])
def broadcast_cmd(message):
    uid = str(message.chat.id)
    if uid != str(ADMIN_ID):
        return
    
    text = message.text.replace('/broadcast', '').strip()
    if not text:
        bot.send_message(uid, 'Укажите текст рассылки: /broadcast Ваш текст')
        return
    
    sent = 0
    for u in user_stats:
        try:
            bot.send_message(u, text)
            sent += 1
        except:
            pass
    
    bot.send_message(uid, f'📢 Рассылка отправлена: {sent} пользователей.')

# ===== CALLBACKS (для /stats) =====
@bot.callback_query_handler(func=lambda call: True)
def callback(call):
    uid = str(call.message.chat.id)
    if uid in banned:
        bot.answer_callback_query(call.id, '⛔ Вы заблокированы.')
        return
    
    if call.data.startswith('stats_page_'):
        if uid != str(ADMIN_ID):
            return
        page = int(call.data.split('_')[2])
        users = list(user_stats.keys())
        total = sum(v['total'] for v in user_stats.values())
        show_stats_page(uid, page, users, total)
        bot.answer_callback_query(call.id)
        return
    
    if call.data.startswith('stats_user_'):
        if uid != str(ADMIN_ID):
            return
        parts = call.data.split('_')
        target_uid = parts[2]
        page = parts[3]
        stats = user_stats.get(target_uid, {})
        name = get_username(target_uid)
        chats_count = len(user_chats.get(target_uid, {}).get('chats', {}))
        
        text = f"📋 {name} ({target_uid})\n"
        text += f"├— Всего запросов: {stats.get('total', 0)}\n"
        text += f"├— Текстовых: {stats.get('text', 0)}\n"
        text += f"├— Картинок: {stats.get('images', 0)}\n"
        text += f"├— Анализов фото: {stats.get('photo_analysis', 0)}\n"
        text += f"├— За сегодня: {stats.get('today', 0)}\n"
        text += f"└— Чатов: {chats_count}"
        
        markup = telebot.types.InlineKeyboardMarkup()
        markup.add(telebot.types.InlineKeyboardButton('← Назад', callback_data=f'stats_back_{page}'))
        bot.send_message(uid, text, reply_markup=markup)
        bot.answer_callback_query(call.id)
        return
    
    if call.data.startswith('stats_back_'):
        if uid != str(ADMIN_ID):
            return
        page = int(call.data.split('_')[2])
        users = list(user_stats.keys())
        total = sum(v['total'] for v in user_stats.values())
        show_stats_page(uid, page, users, total)
        bot.answer_callback_query(call.id)
        return
    
    if call.data == 'noop':
        bot.answer_callback_query(call.id)
        return

# ===== ОСНОВНОЙ ОБРАБОТЧИК =====
@bot.message_handler(content_types=['text', 'photo'])
def handle_message(message):
    uid = str(message.chat.id)
    if uid in banned:
        bot.send_message(uid, '⛔ Вы заблокированы администратором.')
        return
    
    prompt = None
    image = None
    is_photo = False
    
    if message.photo:
        is_photo = True
        image = message.photo[-1]
        prompt = message.caption if message.caption else "Опиши что на фото"
    
    if message.text:
        prompt = message.text
    
    if not prompt:
        return
    
    msg = bot.reply_to(message, '💭 Думаю...')
    
    if is_photo:
        update_stats(uid, 'photo_analysis')
    else:
        update_stats(uid, 'text')
    
    response_text, _ = ask_gemini(uid, prompt, image)
    
    try:
        bot.delete_message(uid, msg.message_id)
    except:
        pass
    
    bot.send_message(uid, response_text[:4000] if response_text else "Не удалось получить ответ.")
    
    if is_photo:
        log_to_group(uid, prompt, response_text, image_analysis=True)
    else:
        log_to_group(uid, prompt, response_text, image_analysis=False)

# ===== FLASK =====
@app.route('/' + TOKEN, methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return '!', 200
    return 'Bad request', 400

@app.route('/')
def home():
    try:
        bot.remove_webhook()
        time.sleep(1)
        bot.set_webhook(STATIC_URL + '/' + TOKEN)
        set_commands()
        return 'Webhook set!', 200
    except Exception as e:
        return f'Error: {e}', 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
