import json
import downloader

class DummyButton:
    def __init__(self, text, callback_data):
        self.text = text
        self.callback_data = callback_data

query = "Толкин"

# Scenario 1: Intermediate message with button "Толкин"
intermediate_text = "Поиск по запросу: Толкин..."
intermediate_markup = [[DummyButton("Толкин", "search_tolkien")]]

res_intermediate = downloader.parse_library_response(
    msg_text=intermediate_text,
    reply_markup=intermediate_markup,
    query_title=query
)
print("Intermediate response parse result:", res_intermediate)
assert res_intermediate == [], f"Expected [], got {res_intermediate}"

# Scenario 2: Pagination buttons message (e.g. -1-, 2, 3, 4>, 40>)
pagination_markup = [
    [DummyButton("-1-", "page_1"), DummyButton("2", "page_2"), DummyButton("3", "page_3"), DummyButton("4>", "page_4"), DummyButton("40>", "page_40")]
]
res_pagination = downloader.parse_library_response(
    msg_text="",
    reply_markup=pagination_markup,
    query_title=query
)
print("Pagination buttons parse result:", res_pagination)
assert res_pagination == [], f"Expected [], got {res_pagination}"

# Scenario 3: Full book card message
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

parsed = downloader.parse_library_response(msg_text=sample_text, query_title=query)
print("Full card parse result:")
print(json.dumps(parsed, ensure_ascii=False, indent=2))

assert len(parsed) == 5
assert parsed[0]["title"] == "Толкин и Великая война. На пороге Средиземья"
assert parsed[0]["author"] == "Джон Гарт"
assert parsed[0]["download_cmd"] == "/download682541"
