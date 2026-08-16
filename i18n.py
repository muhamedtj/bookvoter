from typing import Dict, Any

STRINGS: Dict[str, Dict[str, str]] = {
    "unknown_author": {
        "en": "Unknown Author",
        "ru": "Неизвестный автор"
    },
    "general_genre": {
        "en": "General",
        "ru": "Без жанра"
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
            "📚 **Welcome to BookVoter Bot!**\n\n"
            "Commands:\n"
            "• `/suggest` [book title] - Suggest a book for your group backlog.\n"
            "• `/bookvoter` - Control panel (Start/Finish vote, Delete books, Stats).\n"
            "• `/language` - Change bot language.\n"
            "• Click on book rating links sent in groups to rate backlog books privately!"
        ),
        "ru": (
            "📚 **Добро пожаловать в BookVoter Bot!**\n\n"
            "Команды:\n"
            "• `/suggest` [название книги] - Предложить книгу в бэклог группы.\n"
            "• `/bookvoter` - Панель управления (Голосование, Завершение чтения, Удаление книг, Статистика).\n"
            "• `/language` - Сменить язык бота.\n"
            "• Нажимайте на ссылки оценки книг в группе, чтобы оценивать их в личных сообщениях!"
        )
    },
    "btn_open_control_panel": {
        "en": "⚙️ Open Control Panel",
        "ru": "⚙️ Открыть панель управления"
    },
    "suggest_usage": {
        "en": "Please specify a book title, e.g.: `/suggest Dune`",
        "ru": "Пожалуйста, укажите название книги, например: `/suggest Дюна`"
    },
    "searching_google_books": {
        "en": "Searching Google Books...",
        "ru": "Поиск в Google Books..."
    },
    "no_books_found": {
        "en": "No books found for your query. Please try a different title.",
        "ru": "По вашему запросу ничего не найдено. Попробуйте другое название."
    },
    "select_matching_book": {
        "en": "Select the matching book from Google Books results:",
        "ru": "Выберите подходящую книгу из результатов Google Books:"
    },
    "selection_expired": {
        "en": "Selection expired or invalid. Please run /suggest again.",
        "ru": "Выбор истек или недействителен. Пожалуйста, запустите /suggest снова."
    },
    "book_saved_cb": {
        "en": "Book saved to backlog!",
        "ru": "Книга сохранена в бэклог!"
    },
    "book_added_title": {
        "en": "✅ **Book Added to Backlog!**",
        "ru": "✅ **Книга добавлена в бэклог!**"
    },
    "book_field_title": {
        "en": "📖 **Title:** {title}",
        "ru": "📖 **Название:** {title}"
    },
    "book_field_author": {
        "en": "✍️ **Author:** {author}",
        "ru": "✍️ **Автор:** {author}"
    },
    "book_field_genre": {
        "en": "🏷️ **Genre:** {genre}",
        "ru": "🏷️ **Жанр:** {genre}"
    },
    "click_below_to_rate": {
        "en": "Click below to rate unrated books in private messages!",
        "ru": "Нажмите ниже, чтобы оценить книги в личных сообщениях!"
    },
    "btn_rate_backlog": {
        "en": "⭐ Rate Backlog Books",
        "ru": "⭐ Оценить книги из бэклога"
    },
    "all_caught_up_rating": {
        "en": "🎉 **All caught up!** You have rated all available backlog books for your groups.",
        "ru": "🎉 **Всё оценено!** Вы оценили все доступные книги из бэклога ваших групп."
    },
    "rate_prompt_group": {
        "en": "📚 **Group:** {chat_title}\n\n📖 **Title:** {title}\n✍️ **Author:** {author}\n🏷️ **Genre:** {genre}\n\nRate this book from **1** (lowest) to **10** (highest):",
        "ru": "📚 **Группа:** {chat_title}\n\n📖 **Название:** {title}\n✍️ **Автор:** {author}\n🏷️ **Жанр:** {genre}\n\nОцените эту книгу от **1** (минимум) до **10** (максимум):"
    },
    "saved_score_cb": {
        "en": "Saved score: {score}/10",
        "ru": "Сохранена оценка: {score}/10"
    },
    "only_admins_allowed": {
        "en": "⚠️ Only group administrators can perform this action.",
        "ru": "⚠️ Только администраторы группы могут выполнять это действие."
    },
    "admin_panel_title": {
        "en": "⚙️ **BookVoter Control Panel**",
        "ru": "⚙️ **Панель управления BookVoter**"
    },
    "btn_start_vote": {
        "en": "🎲 Start Next Book Vote",
        "ru": "🎲 Начать голосование за книгу"
    },
    "btn_finish_vote_early": {
        "en": "⏹️ Finish Current Vote Early",
        "ru": "⏹️ Завершить голосование досрочно"
    },
    "btn_finish_reading": {
        "en": "🏆 Finish Reading (Hall of Fame)",
        "ru": "🏆 Завершить чтение (Зал славы)"
    },
    "btn_delete_book": {
        "en": "🗑️ Delete Book from Backlog",
        "ru": "🗑️ Удалить книгу из бэклога"
    },
    "btn_group_stats": {
        "en": "📊 Group Stats",
        "ru": "📊 Статистика группы"
    },
    "btn_back_to_menu": {
        "en": "⬅️ Back to Menu",
        "ru": "⬅️ Назад в меню"
    },
    "select_book_to_delete": {
        "en": "🗑️ **Select a book to delete from backlog or active list:**",
        "ru": "🗑️ **Выберите книгу для удаления из бэклога или списков:**"
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
        "en": "❌ No backlog books available to start a vote. Suggest some books with `/suggest` first!",
        "ru": "❌ В бэклоге нет книг для голосования. Сначала предложите книги с помощью `/suggest`!"
    },
    "poll_question": {
        "en": "🗳️ Vote for the next book to read!",
        "ru": "🗳️ Голосуйте за следующую книгу для чтения!"
    },
    "vote_started_msg": {
        "en": "⏳ **Vote started!** The poll will automatically close in 24 hours.",
        "ru": "⏳ **Голосование начато!** Опрос автоматически закроется через 24 часа."
    },
    "no_active_vote_err": {
        "en": "⚠️ No active vote found to finish.",
        "ru": "⚠️ Нет активного голосования для завершения."
    },
    "voting_ended_title": {
        "en": "🏆 **Voting Ended!**\n\nThe winner is: **{title}** by **{author}**!\n📥 Fetching book file from library...",
        "ru": "🏆 **Голосование завершено!**\n\nПобедитель: **{title}** ({author})!\n📥 Ищем файл книги в библиотеке..."
    },
    "book_file_caption": {
        "en": "📚 Here is your book: **{title}**",
        "ru": "📚 Ваша книга: **{title}**"
    },
    "file_not_found_in_lib": {
        "en": "❌ File not found in library for **{title}**.",
        "ru": "❌ Файл книги **{title}** не найден в библиотеке."
    },
    "file_download_err": {
        "en": "❌ An error occurred while downloading **{title}**.",
        "ru": "❌ Произошла ошибка при скачивании **{title}**."
    },
    "no_active_reading_err": {
        "en": "No currently active book to finish.",
        "ru": "Нет читаемой книги для завершения."
    },
    "added_to_hof_cb": {
        "en": "Book added to Hall of Fame!",
        "ru": "Книга добавлена в Зал славы!"
    },
    "hof_title": {
        "en": "🏆 **Hall of Fame Updated!**\n\n",
        "ru": "🏆 **Зал славы обновлен!**\n\n"
    },
    "group_stats_text": {
        "en": (
            "📊 **Group Statistics**\n\n"
            "• Backlog Books: **{backlog_count}**\n"
            "• Completed Books: **{done_count}**\n"
            "• Active Raters: **{active_raters}**"
        ),
        "ru": (
            "📊 **Статистика группы**\n\n"
            "• Книг в бэклоге: **{backlog_count}**\n"
            "• Прочитано книг: **{done_count}**\n"
            "• Активных читателей: **{active_raters}**"
        )
    },
    "sys_stats_text": {
        "en": (
            "🌐 **Global System Metrics**\n\n"
            "• Total Active Groups: **{total_active_chats}**\n"
            "• Total Unique Internal Users: **{total_unique_users}**\n"
            "• Total Downloaded Books: **{total_downloaded_books}**"
        ),
        "ru": (
            "🌐 **Глобальные метрики системы**\n\n"
            "• Всего активных групп: **{total_active_chats}**\n"
            "• Уникальных пользователей: **{total_unique_users}**\n"
            "• Скачано книг: **{total_downloaded_books}**"
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
