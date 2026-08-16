import re
import json

def parse_library_response_blocks(raw_text: str, query_title: str) -> list[dict]:
    blocks = []
    # Split text by blank lines
    raw_blocks = re.split(r"\n\s*\n", raw_text)
    for block in raw_blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        # Skip header/footer lines or prompt messages
        filtered_lines = [
            l for l in lines
            if not any(w in l.lower() for w in [
                "/start", "добро пожаловать", "приветствую", "найдено:",
                "мы нашли именно то", "книга по вашему запросу", "поиск...", "идет поиск"
            ])
        ]
        if not filtered_lines:
            continue

        # Find download command in block
        dl_cmd = ""
        non_cmd_lines = []
        for l in filtered_lines:
            cmd_m = re.search(r"/(?:download|get|dl|d)_[a_zA_Z0_9_]+|/download\d+", l)
            if cmd_m:
                dl_cmd = cmd_m.group(0)
            else:
                non_cmd_lines.append(l)

        if non_cmd_lines:
            raw_title = non_cmd_lines[0]
            # Strip language tags like '- ru', '– ru', '[ru]', '(ru)', '- litres', etc.
            clean_title = re.sub(r"\s*[-–—]?\s*(?:ru|en|litres|pdf|epub)\b.*$", "", raw_title, flags=re.IGNORECASE).strip()
            if not clean_title:
                clean_title = raw_title

            author = ""
            if len(non_cmd_lines) >= 3:
                author = non_cmd_lines[-1]
            elif len(non_cmd_lines) == 2:
                author = non_cmd_lines[1]
            elif " — " in clean_title or " - " in clean_title:
                parts = re.split(r"\s+[—\-]\s+", clean_title, 1)
                if len(parts) == 2:
                    clean_title, author = parts[0].strip(), parts[1].strip()

            # Clean up author if it contains unwanted status text
            if author and (author.startswith("(") or "скачать" in author.lower() or "найдено" in author.lower()):
                author = ""

            blocks.append({
                "title": clean_title,
                "author": author if author else "Unknown Author",
                "genre": "",
                "download_cmd": dl_cmd,
                "raw_label": f"{clean_title} — {author}" if author else clean_title
            })

    return blocks

sample_text = """Найдено: 200 книг

Толкин и Великая война. На пороге Средиземья - ru
Толкин – творец Средиземья
Джон Гарт
Скачать книгу: /download682541

Джон Р. Р. Толкин. Письма - ru
Толкинистика на русском
Джон Рональд Руэл Толкин, Хамфри Карпентер
Скачать книгу: /download329616

Три цвета Джона Толкина - ru
Литература о жизни и творчестве Д.Р.Р. Толкиена
Александр Исаакович Мирер, Джон Рональд Руэл Толкин
Скачать книгу: /download302313

Толкин - ru
Жизнь замечательных людей (1542)
Сергей Владимирович Соловьев, Геннадий Мартович Прашкевич
Скачать книгу: /download429309

Толкин и толкинизм - взгляд справа - ru
Дарт Вальтамский
Скачать книгу: /download61929

🗿 Мы нашли именно то что вы искали 😉

— Книга по вашему запросу ✅"""

parsed = parse_library_response_blocks(sample_text, "Толкин")
print(json.dumps(parsed, ensure_ascii=False, indent=2))
