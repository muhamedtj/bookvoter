from typing import Dict, Any

STRINGS: Dict[str, Dict[str, str]] = {
    "unknown_author": {
        "en": "Unknown Author",
        "ru": "Неизвестный автор"
    },
    "select_language_prompt": {
        "en": "🌐 **Please select your language / Пожалуйста, выберите язык:**",
        "ru": "🌐 **Пожалуйста, выберите язык / Please select your language:**"
    },
    "language_selected": {
        "en": "✅ Language set to **English**!",
        "ru": "✅ Язык установлен на **Русский**!"
    },
    "welcome_msg": {
        "en": (
            "📚 **BookVoter**\n\n"
            "Choose your next book with your club.\n\n"
            "Suggest books, rate interest, vote, and give final ratings after reading.\n\n"
            "📖 Suggest a book:\n"
            "`/suggest Book title`"
        ),
        "ru": (
            "📚 **BookVoter**\n\n"
            "Выбирайте следующую книгу вместе с клубом.\n\n"
            "Предлагайте книги, оценивайте интерес, голосуйте и выставляйте итоговые оценки после чтения.\n\n"
            "📖 Предложить книгу:\n"
            "`/suggest Название книги`"
        )
    },
    # Buttons
    "btn_how_it_works": {
        "en": "ℹ️ How it works",
        "ru": "ℹ️ Как работает бот"
    },
    "btn_rate_books": {
        "en": "⭐ Rate books",
        "ru": "⭐ Оценить книги"
    },
    "btn_open_control_panel": {
        "en": "⚙️ Control panel",
        "ru": "⚙️ Панель управления"
    },
    "btn_report_error": {
        "en": "🐛 Report an error",
        "ru": "🐛 Сообщить об ошибке"
    },
    "btn_start_vote": {
        "en": "🎲 Start voting",
        "ru": "🎲 Начать голосование"
    },
    "btn_finish_vote_early": {
        "en": "⏹ Finish voting early",
        "ru": "⏹ Завершить голосование досрочно"
    },
    "btn_finish_reading": {
        "en": "🏆 Finish reading",
        "ru": "🏆 Завершить чтение"
    },
    "btn_group_stats": {
        "en": "📊 Group stats",
        "ru": "📊 Статистика группы"
    },
    "btn_audit_backlog": {
        "en": "🔍 Audit backlog",
        "ru": "🔍 Проверить бэклог"
    },
    "btn_delete_book": {
        "en": "🗑 Delete book",
        "ru": "🗑 Удалить книгу"
    },
    "btn_back": {
        "en": "⬅️ Back",
        "ru": "⬅️ Назад"
    },
    "btn_back_to_panel": {
        "en": "⬅️ Back to panel",
        "ru": "⬅️ Назад в панель"
    },
    "btn_did_not_read": {
        "en": "Didn't read",
        "ru": "Не читал"
    },
    "error_reported_thanks": {
        "en": "✅ Thank you. The error report has been sent.",
        "ru": "✅ Спасибо. Сообщение об ошибке отправлено."
    },
    "control_panel_dm_notice": {
        "en": "⚙️ The Control Panel is available in the book club group chat. Open the group and type `/bookvoter`.",
        "ru": "⚙️ Панель управления доступна в группе книжного клуба. Откройте группу и вызовите `/bookvoter`."
    },
    "suggest_usage": {
        "en": "Please specify a book title, e.g.: `/suggest Dune`",
        "ru": "Пожалуйста, укажите название книги, например: `/suggest Дюна`"
    },
    "searching_google_books": {
        "en": "🔎 Searching for the book...",
        "ru": "🔎 Ищем книгу..."
    },
    "google_books_api_error": {
        "en": "Could not connect to the book library database. Please try again later.",
        "ru": "Не удалось связаться с базой данных книг библиотеки. Попробуйте позже."
    },
    "no_valid_books_found": {
        "en": "Could not find a book matching your query. Please try refining the title.",
        "ru": "Не удалось найти книгу по вашему запросу. Попробуйте уточнить название."
    },
    "book_already_exists": {
        "en": "⚠️ This book is already in the club list!",
        "ru": "⚠️ Эта книга уже есть в списке клуба!"
    },
    "select_matching_book": {
        "en": "Select the matching edition from search results:",
        "ru": "Выберите подходящее издание из результатов поиска:"
    },
    "selection_expired": {
        "en": "Selection expired or invalid. Please run /suggest again.",
        "ru": "Выбор истек или недействителен. Пожалуйста, запустите /suggest снова."
    },
    "book_added_confirmation_exact": {
        "en": "✅ Book **«{title}»** ({author}) added to backlog.",
        "ru": "✅ Книга «**{title}**» ({author}) добавлена в бэклог."
    },
    "help_msg": {
        "en": (
            "ℹ️ **How BookVoter Works**\n\n"
            "1. **Suggest a book**\n"
            "   In the group chat, type:\n"
            "   `/suggest Book Title`\n\n"
            "2. **Select the matching edition**\n"
            "   The bot will display search results. Tap the right book to add it to the club's backlog.\n\n"
            "3. **Rate your interest**\n"
            "   Open a private chat with the bot and rate books from 1 to 10.\n"
            "   The higher your score, the more you want to read the book.\n"
            "   Your interest ratings remain private to you.\n\n"
            "4. **Vote for the next book**\n"
            "   Books with the highest interest ratings enter the next vote.\n"
            "   An administrator starts the vote, and members choose the next book for the club.\n"
            "   Voting lasts up to 24 hours and may end earlier if one book receives more than 50% of the votes.\n\n"
            "5. **Read and rate**\n"
            "   After the vote, the bot delivers the book file to the group.\n"
            "   When the club finishes reading, rate the book from 1 to 10 — or mark that you didn't read it.\n\n"
            "🏆 Read books enter the **Hall of Fame** with the club's final score.\n\n"
            "If you notice an issue, tap \"Report an error\"."
        ),
        "ru": (
            "ℹ️ **Как работает BookVoter**\n\n"
            "1. **Предложите книгу**\n"
            "   В группе напишите:\n"
            "   `/suggest Название книги`\n\n"
            "2. **Выберите подходящее издание**\n"
            "   Бот покажет найденные варианты. Нажмите на нужную книгу — она попадёт в общий список клуба.\n\n"
            "3. **Оцените интерес к книгам**\n"
            "   Перейдите в личные сообщения с ботом и поставьте книгам оценку от 1 до 10.\n"
            "   Чем выше ваша оценка, тем сильнее вы хотите прочитать книгу.\n"
            "   Ваши оценки интереса видите только вы.\n\n"
            "4. **Голосуйте за следующую книгу**\n"
            "   Книги с самыми высокими оценками интереса попадают в следующее голосование.\n"
            "   Администратор запускает голосование, и участники выбирают следующую книгу клуба.\n"
            "   Голосование длится до 24 часов и может завершиться раньше, если одна книга получит больше половины голосов.\n\n"
            "5. **Читайте и оценивайте**\n"
            "   После выбора бот отправит книгу в группу.\n"
            "   Когда клуб закончит чтение, поставьте книге итоговую оценку от 1 до 10 — или отметьте, что не читали её.\n\n"
            "🏆 Прочитанные книги попадают в **Зал славы** клуба с итоговой оценкой.\n\n"
            "Если заметили проблему, нажмите «Сообщить об ошибке»."
        )
    },
    "click_below_to_rate": {
        "en": "Click below to rate unrated books in private messages!",
        "ru": "Нажмите ниже, чтобы оценить книги в личных сообщениях!"
    },
    "all_caught_up_rating": {
        "en": "🎉 **All caught up!** You have rated all available backlog books for your groups.",
        "ru": "🎉 **Всё оценено!** Вы оценили все доступные книги из бэклога ваших групп."
    },
    "rate_prompt_group": {
        "en": "📚 **Group:** {chat_title}\n\n📖 **Title:** {title}\n✍️ **Author:** {author}\n\nHow strongly do you want to read this book?",
        "ru": "📚 **Группа:** {chat_title}\n\n📖 **Название:** {title}\n✍️ **Автор:** {author}\n\nНасколько сильно вы хотите прочитать эту книгу?"
    },
    "saved_score_cb": {
        "en": "Saved score: {score}/10",
        "ru": "Сохранена оценка: {score}/10"
    },
    "backlog_list_header": {
        "en": "📚 **Club Backlog**\n\n",
        "ru": "📚 **Бэклог клуба**\n\n"
    },
    "backlog_item_format": {
        "en": "{idx}. **{title}** — {author}\n   ⭐ {score}/10 · 👤 {suggestor}\n\n",
        "ru": "{idx}. **{title}** — {author}\n   ⭐ {score}/10 · 👤 {suggestor}\n\n"
    },
    "backlog_empty": {
        "en": "📜 Backlog is currently empty. Add books using `/suggest`!",
        "ru": "📜 Бэклог пока пуст. Добавьте книги с помощью `/suggest`!"
    },
    "only_admins_allowed": {
        "en": "⚠️ Only group administrators can perform this action.",
        "ru": "⚠️ Только администраторы группы могут выполнять это действие."
    },
    # Admin Panel States
    "admin_panel_title": {
        "en": "⚙️ **BookVoter Control Panel**",
        "ru": "⚙️ **Панель управления BookVoter**"
    },
    "admin_state_ready": {
        "en": "⚙️ **BookVoter Control Panel**\n\n📚 You can choose the next book.",
        "ru": "⚙️ **Панель управления BookVoter**\n\n📚 Можно выбрать следующую книгу."
    },
    "admin_state_voting": {
        "en": "⚙️ **BookVoter Control Panel**\n\n🗳 Voting for the next book is currently in progress.",
        "ru": "⚙️ **Панель управления BookVoter**\n\n🗳 Сейчас идёт голосование за следующую книгу."
    },
    "admin_state_reading": {
        "en": "⚙️ **BookVoter Control Panel**\n\n📖 Currently reading: **{title}** — {author}",
        "ru": "⚙️ **Панель управления BookVoter**\n\n📖 Сейчас читаем: **{title}** — {author}"
    },
    "audit_title": {
        "en": "🔍 **Backlog Activity Audit Report:**\n\n",
        "ru": "🔍 **Отчет проверки активности бэклога:**\n\n"
    },
    "audit_clean": {
        "en": "✅ **Audit clean!** All backlog books were suggested by active, participating members.",
        "ru": "✅ **Проверка чистая!** Все книги в бэклоге предложены активными участниками группы."
    },
    "reason_departed": {
        "en": "Suggested by departed member",
        "ru": "Предложено вышедшим участником"
    },
    "reason_inactive": {
        "en": "Suggested by inactive voter",
        "ru": "Предложено неактивным участником"
    },
    "filter_all_time": {
        "en": "All Time",
        "ru": "За все время"
    },
    "filter_30_days": {
        "en": "Last 30 Days",
        "ru": "За 30 дней"
    },
    "select_book_to_delete": {
        "en": "🗑 **Select a book to delete from backlog:**",
        "ru": "🗑 **Выберите книгу для удаления из бэклога:**"
    },
    "no_books_to_delete": {
        "en": "❌ No books found in backlog to delete.",
        "ru": "❌ В бэклоге нет книг для удаления."
    },
    "book_deleted_success": {
        "en": "✅ Book **{title}** has been deleted.",
        "ru": "✅ Книга **{title}** была успешно удалена."
    },
    "vote_in_progress_err": {
        "en": "⚠️ A vote is already in progress for this group!",
        "ru": "⚠️ В этой группе уже идет голосование!"
    },
    "no_backlog_books_err": {
        "en": "There are currently no books in the backlog for voting.",
        "ru": "В бэклоге пока нет книг для голосования."
    },
    "poll_question": {
        "en": "🗳 Vote for the next book to read!",
        "ru": "🗳 Голосуйте за следующую книгу для чтения!"
    },
    "vote_started_msg": {
        "en": "🗳 Voting has started.",
        "ru": "🗳 Голосование началось."
    },
    "no_active_vote_err": {
        "en": "⚠️ No active vote found to finish.",
        "ru": "⚠️ Нет активного голосования для завершения."
    },
    "voting_ended_title": {
        "en": "🏆 **Next book selected**\n\n**{title}** — {author}",
        "ru": "🏆 **Выбрана следующая книга**\n\n**{title}** — {author}"
    },
    "active_reading_in_progress_err": {
        "en": "⚠️ Cannot start a new vote while **{title}** is currently being read! Finish reading it first.",
        "ru": "⚠️ Нельзя начать новое голосование, пока читается книга **{title}**! Сначала завершите её чтение."
    },
    "vote_canceled_no_votes": {
        "en": "⚠️ Vote ended with 0 votes! Voting has been canceled and books reverted to backlog.",
        "ru": "⚠️ Никто не проголосовал! Голосование отменено, книги возвращены в бэклог."
    },
    "book_file_caption": {
        "en": "📚 Here is your book: **{title}**",
        "ru": "📚 Ваша книга: **{title}**"
    },
    "file_not_found_in_lib": {
        "en": "⚠️ Book selected, but the file could not be retrieved automatically.",
        "ru": "⚠️ Книга выбрана, но файл не удалось получить автоматически."
    },
    "file_download_err": {
        "en": "⚠️ Book selected, but the file could not be retrieved automatically.",
        "ru": "⚠️ Книга выбрана, но файл не удалось получить автоматически."
    },
    "no_active_reading_err": {
        "en": "No currently active book to finish.",
        "ru": "Нет читаемой книги для завершения."
    },
    "added_to_hof_cb": {
        "en": "Reading finished! Rating card sent to chat.",
        "ru": "Чтение завершено! Карточка оценки отправлена в чат."
    },
    "finish_reading_card_title": {
        "en": "📖 **{title}** — {author}\n\nHow do you rate this book?",
        "ru": "📖 **{title}** — {author}\n\nКак вы оцениваете книгу?"
    },
    "saved_read_score_cb": {
        "en": "Your score {score}/10 accepted!",
        "ru": "Ваша оценка {score} принята!"
    },
    "saved_did_not_read_cb": {
        "en": "Recorded: Didn't read",
        "ru": "Зафиксировано: Не читал"
    },
    "vote_feedback_msg": {
        "en": "Thank you! Your rating is accepted.\n\nCurrent average score for **{title}**: **{avg_rating}** (based on {count} votes).",
        "ru": "Спасибо! Ваша оценка принята.\n\nСредний балл по книге **{title}** сейчас: **{avg_rating}** (на основе {count} голосов)."
    },
    "hof_header": {
        "en": "🏆 **Hall of Fame**\n\n",
        "ru": "🏆 **Зал славы**\n\n"
    },
    "hof_low_votes_header": {
        "en": "\n\n⚠️ **Low Votes (< {min_votes} votes):**\n",
        "ru": "\n\n⚠️ **Мало оценок (менее {min_votes} голосов):**\n"
    },
    "hof_empty": {
        "en": "🏆 Hall of Fame is currently empty.",
        "ru": "🏆 Зал славы пока пуст."
    },
    "hof_item_format": {
        "en": "{idx}. **{title}** — {author}\n   ⭐ {wr} · 👥 {count}",
        "ru": "{idx}. **{title}** — {author}\n   ⭐ {wr} · 👥 {count}"
    },
    "btn_hof_delete_book": {
        "en": "🗑 Delete Book from Hall of Fame",
        "ru": "🗑 Удалить книгу из Зала славы"
    },
    "select_hof_book_to_delete": {
        "en": "🗑 **Select a book to remove from Hall of Fame:**",
        "ru": "🗑 **Выберите книгу для удаления из Зала славы:**"
    },
    "hof_confirm_delete_prompt": {
        "en": "❓ Are you sure you want to remove **{title}** from the Hall of Fame?",
        "ru": "❓ Вы уверены, что хотите удалить книгу **{title}** из Зала славы?"
    },
    "btn_confirm_yes": {
        "en": "✅ Yes, delete",
        "ru": "✅ Да, удалить"
    },
    "btn_confirm_cancel": {
        "en": "❌ Cancel",
        "ru": "❌ Отмена"
    },
    "hof_deleted_success": {
        "en": "✅ Book **{title}** removed from Hall of Fame.",
        "ru": "✅ Книга **{title}** была удалена из Зала славы."
    },
    "group_stats_text": {
        "en": (
            "📊 **Group Statistics ({filter_label})**\n\n"
            "• Backlog Books: **{backlog_count}**\n"
            "• Completed Books: **{done_count}**\n"
            "• Avg Desire Rating: **{avg_club_rating}/10**\n\n"
            "🏆 **Top Contributors:**\n{contributors_str}\n\n"
            "🗳️ **Top Voters:**\n{voters_str}"
        ),
        "ru": (
            "📊 **Статистика группы ({filter_label})**\n\n"
            "• Книг в бэклоге: **{backlog_count}**\n"
            "• Прочитано книг: **{done_count}**\n"
            "• Средний рейтинг желания: **{avg_club_rating}/10**\n\n"
            "🏆 **Топ авторов предложений:**\n{contributors_str}\n\n"
            "🗳️ **Самые активные голосующие:**\n{voters_str}"
        )
    },
    "sys_stats_text": {
        "en": (
            "🌐 **Global Superadmin Dashboard ({filter_label})**\n\n"
            "• Total Active Groups: **{total_active_chats}**\n"
            "• Total Unique Voters: **{total_voters}**\n"
            "• Total Suggested Books: **{total_books_suggested}**\n\n"
            "🔥 **Top Books by Wish Score:**\n{top_books_str}"
        ),
        "ru": (
            "🌐 **Глобальная панель супер-админа ({filter_label})**\n\n"
            "• Всего активных групп: **{total_active_chats}**\n"
            "• Уникальных проголосовавших: **{total_voters}**\n"
            "• Всего предложено книг: **{total_books_suggested}**\n\n"
            "🔥 **Топ книг по рейтингу желания:**\n{top_books_str}"
        )
    }
}


def t(key: str, lang: str = "en", **kwargs: Any) -> str:
    """Fetch localized string by key and language code."""
    lang_code = lang.lower() if lang else "en"
    if lang_code not in ("en", "ru"):
        lang_code = "en"

    template = STRINGS.get(key, {}).get(lang_code) or STRINGS.get(key, {}).get("en") or key
    if kwargs:
        try:
            return template.format(**kwargs)
        except Exception:
            return template
    return template
