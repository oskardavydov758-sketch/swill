import os
import telebot
import time
import threading
import requests
import json
import re
import base64
from datetime import datetime, timedelta, timezone as tz
from flask import Flask, request
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold

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

MODELS_MENU = {
    'models/gemini-2.0-flash': '⚡ Flash',
    'models/gemini-2.0-flash-lite': '🪶 Lite',
    'models/gemini-2.5-flash': '🚀 2.5 Flash',
    'models/gemini-flash-latest': '📦 Auto',
}

AVAILABLE_MODELS = []
try:
    for m in genai.list_models():
        if 'generateContent' in m.supported_generation_methods:
            AVAILABLE_MODELS.append(m.name)
except:
    pass

CURRENT_MODEL = 'models/gemini-2.0-flash'
if CURRENT_MODEL not in AVAILABLE_MODELS and AVAILABLE_MODELS:
    CURRENT_MODEL = AVAILABLE_MODELS[0]

model = genai.GenerativeModel(CURRENT_MODEL)
print(f"Используемая модель: {CURRENT_MODEL}")

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
        telebot.types.BotCommand('models', '🤖 Сменить модель'),
        telebot.types.BotCommand('current', '📋 Текущая модель'),
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

def download_telegram_photo(file_id):
    """Скачивает фото из Telegram и возвращает словарь для Gemini"""
    file_info = bot.get_file(file_id)
    downloaded = bot.download_file(file_info.file_path)
    return {'mime_type': 'image/jpeg', 'data': downloaded}

def extract_image_from_response(response):
    """Пытается достать картинку из ответа Gemini ВСЕМИ возможными способами"""
    
    # Способ 1: inline_data (стандартный)
    try:
        for candidate in response.candidates:
            for part in candidate.content.parts:
                if hasattr(part, 'inline_data') and part.inline_data:
                    print("✅ Картинка найдена: inline_data")
                    return part.inline_data.data, 'inline_data'
    except Exception as e:
        print(f"Способ 1 (inline_data) не сработал: {e}")
    
    # Способ 2: Ищем base64 в тексте
    try:
        text = response.text
        base64_pattern = r'data:image/[^;]+;base64,([A-Za-z0-9+/=]+)'
        match = re.search(base64_pattern, text)
        if match:
            print("✅ Картинка найдена: base64 в тексте")
            return base64.b64decode(match.group(1)), 'base64_text'
    except Exception as e:
        print(f"Способ 2 (base64 в тексте) не сработал: {e}")
    
    # Способ 3: Ищем Markdown-ссылку на картинку ![image](url)
    try:
        text = response.text
        markdown_pattern = r'!\[.*?\]\((https?://[^\s\)]+\.(?:png|jpg|jpeg|gif|webp)[^\s\)]*)\)'
        match = re.search(markdown_pattern, text, re.IGNORECASE)
        if match:
            url = match.group(1)
            print(f"✅ Картинка найдена: Markdown URL {url[:50]}...")
            try:
                resp = requests.get(url, timeout=10)
                if resp.status_code == 200:
                    return resp.content, 'markdown_url'
            except Exception as e:
                print(f"Не удалось скачать URL: {e}")
    except Exception as e:
        print(f"Способ 3 (Markdown URL) не сработал: {e}")
    
    # Способ 4: Ищем прямую ссылку на изображение в тексте
    try:
        text = response.text
        url_pattern = r'(https?://[^\s]+\.(?:png|jpg|jpeg|gif|webp|svg)[^\s]*)'
        match = re.search(url_pattern, text, re.IGNORECASE)
        if match:
            url = match.group(1)
            print(f"✅ Картинка найдена: прямая ссылка {url[:50]}...")
            try:
                resp = requests.get(url, timeout=10)
                if resp.status_code == 200:
                    return resp.content, 'direct_url'
            except Exception as e:
                print(f"Не удалось скачать URL: {e}")
    except Exception as e:
        print(f"Способ 4 (прямая ссылка) не сработал: {e}")
    
    # Способ 5: Ищем JSON с base64 (формат Venus/DALL-E)
    try:
        text = response.text
        json_pattern = r'"b64_json"\s*:\s*"([A-Za-z0-9+/=]+)"'
        match = re.search(json_pattern, text)
        if match:
            print("✅ Картинка найдена: JSON b64_json")
            return base64.b64decode(match.group(1)), 'json_b64'
    except Exception as e:
        print(f"Способ 5 (JSON b64_json) не сработал: {e}")
    
    # Способ 6: Парсим action_input JSON (формат DALL-E промта)
    try:
        text = response.text
        json_pattern = r'"image_url"\s*:\s*"([^"]+)"'
        match = re.search(json_pattern, text)
        if match:
            url = match.group(1)
            print(f"✅ Картинка найдена: JSON image_url {url[:50]}...")
            try:
                resp = requests.get(url, timeout=10)
                if resp.status_code == 200:
                    return resp.content, 'json_image_url'
            except Exception as e:
                print(f"Не удалось скачать JSON URL: {e}")
    except Exception as e:
        print(f"Способ 6 (JSON image_url) не сработал: {e}")
    
    # Способ 7: Ищем любой base64 в ответе (последняя надежда)
    try:
        text = response.text
        base64_pattern = r'([A-Za-z0-9+/]{100,}={0,2})'
        matches = re.findall(base64_pattern, text)
        for match in matches:
            try:
                decoded = base64.b64decode(match)
                # Проверяем что это похоже на картинку (первые байты)
                if decoded[:4] in [b'\xff\xd8\xff', b'\x89PNG', b'GIF8', b'RIFF']:
                    print("✅ Картинка найдена: сырой base64")
                    return decoded, 'raw_base64'
            except:
                continue
    except Exception as e:
        print(f"Способ 7 (сырой base64) не сработал: {e}")
    
    print("❌ Картинка не найдена ни одним способом")
    return None, None

def ask_gemini(uid, prompt, image_data=None):
    """Запрос к Gemini с авто-перебором моделей"""
    global model, CURRENT_MODEL
    
    fallback_models = [m for m in MODELS_MENU if m != CURRENT_MODEL and m in AVAILABLE_MODELS]
    models_to_try = [CURRENT_MODEL] + fallback_models
    
    for model_name in models_to_try:
        try:
            current_model = genai.GenerativeModel(model_name)
            
            content = [prompt]
            if image_data:
                content.append(image_data)
            
            response = current_model.generate_content(content)
            
            if model_name != CURRENT_MODEL:
                CURRENT_MODEL = model_name
                model = current_model
                print(f"Переключились на {CURRENT_MODEL}")
            
            # Извлекаем картинку всеми способами
            image_bytes, image_method = extract_image_from_response(response)
            
            # Извлекаем текст
            result_text = None
            try:
                result_text = response.text
            except:
                pass
            
            # Если нашли картинку через URL/JSON — текст мог содержать JSON
            # Очищаем текст от технического мусора
            if image_bytes and result_text:
                # Убираем JSON блоки из текста
                result_text = re.sub(r'\{[^}]*"action"[^}]*\}', '', result_text)
                result_text = re.sub(r'```json.*?```', '', result_text, flags=re.DOTALL)
                result_text = result_text.strip()
                if not result_text or len(result_text) < 10:
                    result_text = None
            
            return result_text, image_bytes
        
        except Exception as e:
            error_str = str(e)
            if '429' in error_str or 'quota' in error_str.lower() or 'exceeded' in error_str.lower():
                print(f"Квота исчерпана для {model_name}, пробую следующую...")
                continue
            else:
                return f"Ошибка: {error_str[:500]}", None
    
    return "⚠️ Все модели исчерпали квоту. Попробуйте позже или смените модель через /models", None

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

def is_image_request(text):
    triggers = ['нарисуй', 'изобрази', 'сгенерируй', 'картинку', 'покажи', 'создай', 'нарисуйте', 'изобразите',
                'сгенерируйте', 'покажите', 'создайте', 'draw', 'generate', 'create', 'make image', 'picture']
    return any(word in text.lower() for word in triggers)

# ===== КОМАНДЫ =====
@bot.message_handler(commands=['start'])
def start(message):
    uid = str(message.chat.id)
    if uid in banned:
        bot.send_message(uid, '⛔ Вы заблокированы администратором.')
        return
    
    bot.send_message(uid, '🚀 SWILL AI активирован.\nЗадайте вопрос текстом или отправьте фото.\n/newchat — начать новый чат.')

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
    
    summary = f"📊 Статистика SWILL AI:\n\n├— Всего запросов: {total}\n├— Текстовых: {total_text}\n├— Генераций картинок: {total_images}\n├— Анализов фото: {total_photo}\n└— Активных юзеров: {len(user_stats)}"
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

@bot.message_handler(commands=['current'])
def current_cmd(message):
    uid = str(message.chat.id)
    if uid != str(ADMIN_ID):
        return
    
    model_name = CURRENT_MODEL.split('/')[-1] if '/' in CURRENT_MODEL else CURRENT_MODEL
    display = MODELS_MENU.get(CURRENT_MODEL, '❓ Неизвестная')
    
    fallback_models = [m for m in MODELS_MENU if m != CURRENT_MODEL and m in AVAILABLE_MODELS]
    
    text = f"📋 Текущая модель:\n\n{display} ({model_name})\n\n🔄 Резервные модели ({len(fallback_models)}):\n"
    for m in fallback_models[:3]:
        text += f"• {MODELS_MENU[m]}\n"
    
    bot.send_message(uid, text)

@bot.message_handler(commands=['models'])
def models_cmd(message):
    uid = str(message.chat.id)
    if uid != str(ADMIN_ID):
        return
    
    markup = telebot.types.InlineKeyboardMarkup(row_width=2)
    row = []
    for model_name, display_name in MODELS_MENU.items():
        if model_name in AVAILABLE_MODELS:
            emoji = "✅" if model_name == CURRENT_MODEL else "  "
            row.append(telebot.types.InlineKeyboardButton(
                f"{emoji} {display_name}", 
                callback_data=f'setmodel_{model_name}'
            ))
            if len(row) == 2:
                markup.row(*row)
                row = []
    if row:
        markup.row(*row)
    
    bot.send_message(uid, '🤖 Выберите модель:\n✅ — текущая модель', reply_markup=markup)

# ===== CALLBACKS =====
@bot.callback_query_handler(func=lambda call: True)
def callback(call):
    uid = str(call.message.chat.id)
    if uid in banned:
        bot.answer_callback_query(call.id, '⛔ Вы заблокированы.')
        return
    
    if call.data.startswith('setmodel_'):
        if uid != str(ADMIN_ID):
            bot.answer_callback_query(call.id, '❌ Только админ.')
            return
        
        model_name = call.data.replace('setmodel_', '')
        global CURRENT_MODEL, model
        
        CURRENT_MODEL = model_name
        model = genai.GenerativeModel(CURRENT_MODEL)
        
        display = MODELS_MENU.get(CURRENT_MODEL, model_name)
        
        markup = telebot.types.InlineKeyboardMarkup(row_width=2)
        row = []
        for mn, dn in MODELS_MENU.items():
            if mn in AVAILABLE_MODELS:
                emoji = "✅" if mn == CURRENT_MODEL else "  "
                row.append(telebot.types.InlineKeyboardButton(f"{emoji} {dn}", callback_data=f'setmodel_{mn}'))
                if len(row) == 2:
                    markup.row(*row)
                    row = []
        if row:
            markup.row(*row)
        
        try:
            bot.edit_message_text(
                f'✅ Модель изменена: {display}\n\n🤖 Выберите модель:',
                uid,
                call.message.message_id,
                reply_markup=markup
            )
        except:
            pass
        
        bot.send_message(GROUP_ID, f'🤖 Модель изменена: {display}')
        bot.answer_callback_query(call.id, f'✅ {display}')
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
        text += f"├— Генераций картинок: {stats.get('images', 0)}\n"
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
    image_data = None
    user_photo_file_id = None
    is_generate_request = False
    
    if message.photo:
        user_photo_file_id = message.photo[-1].file_id
        image_data = download_telegram_photo(user_photo_file_id)
        prompt = message.caption if message.caption else "Опиши что на фото"
        
        if message.caption and is_image_request(message.caption):
            is_generate_request = True
            msg = bot.reply_to(message, '🎨 Обрабатываю...')
            update_stats(uid, 'images')
        else:
            msg = bot.reply_to(message, '🔍 Анализирую...')
            update_stats(uid, 'photo_analysis')
    
    elif message.text:
        prompt = message.text
        
        if is_image_request(prompt):
            msg = bot.reply_to(message, '🎨 Генерирую...')
            update_stats(uid, 'images')
            is_generate_request = True
        else:
            msg = bot.reply_to(message, '💭 Думаю...')
            update_stats(uid, 'text')
    
    if not prompt:
        return
    
    response_text, response_image = ask_gemini(uid, prompt, image_data)
    
    try:
        bot.delete_message(uid, msg.message_id)
    except:
        pass
    
    if response_image:
        if response_text:
            bot.send_photo(uid, response_image, caption=response_text[:1000])
        else:
            bot.send_photo(uid, response_image)
    else:
        bot.send_message(uid, response_text[:4000] if response_text else "Не удалось получить ответ.")
    
    name = get_username(uid)
    time_str = datetime.now(MINSK).strftime('%H:%M %d.%m.%Y')
    
    if image_data and response_image:
        caption_before = f"👤 {name} ({uid})\n🎨 Тип: Генерация картинки (по фото)\n📥 Запрос: {prompt[:200]}\n🕐 {time_str}"
        try:
            bot.send_photo(GROUP_ID, user_photo_file_id, caption=caption_before)
        except:
            bot.send_message(GROUP_ID, f"{caption_before}\n[Фото не удалось переслать]")
        
        caption_after = f"👤 {name} ({uid})\n🖼 Результат генерации\n🕐 {time_str}"
        try:
            bot.send_photo(GROUP_ID, response_image, caption=caption_after)
        except:
            bot.send_message(GROUP_ID, f"{caption_after}\n[Результат не удалось отправить]")
    
    elif image_data and not response_image:
        caption = f"👤 {name} ({uid})\n📷 Тип: Анализ фото\n📥 Запрос: {prompt[:200]}\n📤 Ответ: {response_text[:200] if response_text else '...'}\n🕐 {time_str}"
        try:
            bot.send_photo(GROUP_ID, user_photo_file_id, caption=caption)
        except:
            bot.send_message(GROUP_ID, f"{caption}\n[Фото не удалось переслать]")
    
    elif not image_data and response_image:
        caption = f"👤 {name} ({uid})\n🎨 Тип: Генерация картинки\n📥 Запрос: {prompt[:200]}\n🖼 Сгенерировано\n🕐 {time_str}"
        try:
            bot.send_photo(GROUP_ID, response_image, caption=caption)
        except:
            bot.send_message(GROUP_ID, caption)
    
    else:
        log_text = f"👤 {name} ({uid})\n📝 Тип: Текст\n📥 Запрос: {prompt[:200]}\n📤 Ответ: {response_text[:200] if response_text else '...'}\n🕐 {time_str}"
        try:
            bot.send_message(GROUP_ID, log_text)
        except:
            pass

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
