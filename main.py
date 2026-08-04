import csv
import os
import re
import time
from datetime import datetime

import vk_api
from dotenv import load_dotenv

load_dotenv()

# ================= НАСТРОЙКИ =================
TOKEN = os.getenv('VK_TOKEN', '')

INPUT_FILE = 'links.txt'
OUTPUT_FILE = 'vk_posts.csv'

# VK API wall.getById — не более 100 постов за один запрос
API_BATCH_SIZE = 100
API_BATCH_DELAY = 0.35  # пауза между запросами (лимиты VK)
# =============================================


def init_vk():
    """Инициализация сессии ВК"""
    if not TOKEN:
        raise ValueError(
            'Токен не задан. Добавьте VK_TOKEN в файл .env '
            '(см. .env.example).'
        )
    vk_session = vk_api.VkApi(token=TOKEN)
    return vk_session.get_api()


def extract_post_id(url):
    """Извлекает owner_id и post_id из ссылки ВК"""
    match = re.search(r'wall(-?\d+)_(\d+)', url)
    if match:
        return f"{match.group(1)}_{match.group(2)}"
    return None


def parse_attachments(attachments):
    """Извлекает ссылки на фото, видео и документы из вложений"""
    if not attachments:
        return ""

    links = []
    for att in attachments:
        att_type = att['type']
        if att_type == 'photo':
            sizes = att['photo']['sizes']
            max_size = max(sizes, key=lambda x: x['width'] * x['height'])
            links.append(max_size['url'])
        elif att_type == 'video':
            links.append(
                f"video: https://vk.com/video{att['video']['owner_id']}_{att['video']['id']}"
            )
        elif att_type == 'link':
            links.append(att['link']['url'])
        elif att_type == 'doc':
            links.append(att['doc']['url'])

    return "; ".join(links)


def fetch_posts(vk, post_ids):
    """Загружает посты батчами по API_BATCH_SIZE (лимит VK API)."""
    all_posts = []
    total_batches = (len(post_ids) + API_BATCH_SIZE - 1) // API_BATCH_SIZE

    for batch_num, i in enumerate(range(0, len(post_ids), API_BATCH_SIZE), start=1):
        batch = post_ids[i:i + API_BATCH_SIZE]
        print(
            f"  Батч {batch_num}/{total_batches}: "
            f"посты {i + 1}–{i + len(batch)} из {len(post_ids)}"
        )

        response = vk.wall.getById(posts=','.join(batch))
        all_posts.extend(response)

        if batch_num < total_batches:
            time.sleep(API_BATCH_DELAY)

    return all_posts


def fetch_owner_names(vk, owner_ids):
    """Загружает имена пользователей и сообществ по owner_id."""
    names = {}
    user_ids = [oid for oid in owner_ids if oid > 0]
    group_ids = [abs(oid) for oid in owner_ids if oid < 0]

    for i in range(0, len(user_ids), 1000):
        batch = user_ids[i:i + 1000]
        users = vk.users.get(user_ids=','.join(map(str, batch)))
        for user in users:
            names[user['id']] = f"{user['first_name']} {user['last_name']}".strip()

    for i in range(0, len(group_ids), 500):
        batch = group_ids[i:i + 500]
        groups = vk.groups.getById(group_ids=','.join(map(str, batch)))
        for group in groups:
            names[-group['id']] = group['name']

        if i + 500 < len(group_ids):
            time.sleep(API_BATCH_DELAY)

    return names


def main():
    print("Инициализация VK API...")
    try:
        vk = init_vk()
    except ValueError as e:
        print(f"Ошибка: {e}")
        return

    try:
        with open(INPUT_FILE, 'r', encoding='utf-8') as f:
            urls = [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        print(f"Ошибка: Файл {INPUT_FILE} не найден. Создайте его и добавьте ссылки.")
        return

    post_ids = []
    for url in urls:
        pid = extract_post_id(url)
        if pid:
            post_ids.append(pid)
        else:
            print(f"⚠️ Не удалось распознать ссылку (это не пост?): {url}")

    if not post_ids:
        print("Нет валидных ссылок на посты.")
        return

    existing_ids = set()
    file_exists = os.path.isfile(OUTPUT_FILE) and os.path.getsize(OUTPUT_FILE) > 0

    if file_exists:
        with open(OUTPUT_FILE, 'r', encoding='utf-8-sig') as f:
            reader = csv.reader(f, delimiter=';')
            next(reader, None)
            for row in reader:
                if row:
                    existing_ids.add(row[0])
        print(f"📂 В файле уже есть {len(existing_ids)} постов.")

    new_post_ids = [pid for pid in post_ids if pid not in existing_ids]

    if not new_post_ids:
        print("✅ Все посты уже есть в файле. Ничего не делаю.")
        return

    skipped = len(post_ids) - len(new_post_ids)
    if skipped > 0:
        print(f"⏭️  Пропускаю {skipped} уже сохранённых постов.")

    print(f"🔄 Запрашиваю {len(new_post_ids)} новых постов...")

    all_posts = fetch_posts(vk, new_post_ids)

    owner_ids = {post['owner_id'] for post in all_posts}
    print(f"🔄 Загружаю имена профилей ({len(owner_ids)} шт.)...")
    owner_names = fetch_owner_names(vk, owner_ids)

    print("Сохраняю в CSV...")
    default_fieldnames = [
        'ID поста', 'Ссылка', 'Имя профиля', 'Дата публикации', 'Текст поста',
        'Краткая суть', 'Лайки', 'Просмотры', 'Репосты', 'Комментарии',
        'Вложения (ссылки на медиа)',
    ]

    fieldnames = default_fieldnames
    if file_exists:
        with open(OUTPUT_FILE, 'r', encoding='utf-8-sig', newline='') as f:
            reader = csv.reader(f, delimiter=';')
            header = next(reader, None)
            if header:
                fieldnames = header

    with open(OUTPUT_FILE, 'a', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=';', extrasaction='ignore')

        if not file_exists:
            writer.writeheader()

        for post in all_posts:
            post_id = f"{post['owner_id']}_{post['id']}"
            link = f"https://vk.com/wall{post_id}"
            profile_name = owner_names.get(post['owner_id'], '')
            date = datetime.fromtimestamp(post['date']).strftime('%Y-%m-%d %H:%M:%S')
            text = post.get('text', '').replace('\n', ' ').replace('\r', '')
            likes = post['likes']['count']
            views = post.get('views', {}).get('count', 0)
            reposts = post['reposts']['count']
            comments = post['comments']['count']
            attachments = parse_attachments(post.get('attachments', []))

            row = {
                'ID поста': post_id,
                'Ссылка': link,
                'Имя профиля': profile_name,
                'Дата публикации': date,
                'Текст поста': text,
                'Краткая суть': '',
                'Лайки': likes,
                'Просмотры': views,
                'Репосты': reposts,
                'Комментарии': comments,
                'Вложения (ссылки на медиа)': attachments,
            }
            writer.writerow(row)

    print(f"✅ Готово! Добавлено {len(all_posts)} новых постов.")
    print(f"📁 Всего в файле: {len(existing_ids) + len(all_posts)} постов.")


if __name__ == '__main__':
    main()
