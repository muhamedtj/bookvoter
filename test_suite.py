import asyncio
import os
import sys
import unittest
from datetime import datetime

import database
import bot
import downloader
import i18n

class DummyButton:
    def __init__(self, text, callback_data):
        self.text = text
        self.callback_data = callback_data

class TestBookVoter(unittest.IsolatedAsyncioTestCase):
    def test_select_best_button_priority(self):
        # Test scenario: fb2, epub, mobi -> epub selected
        k1 = [[DummyButton("FB2", "dl_fb2"), DummyButton("EPUB", "dl_epub"), DummyButton("MOBI", "dl_mobi")]]
        btn1 = downloader.select_best_button(k1)
        self.assertEqual(btn1.text, "EPUB")

        # Test scenario: epub absent, fb2 present -> fb2 selected
        k2 = [[DummyButton("FB2", "dl_fb2"), DummyButton("PDF", "dl_pdf")]]
        btn2 = downloader.select_best_button(k2)
        self.assertEqual(btn2.text, "FB2")

    def test_search_parsing_intermediate_button_scenario(self):
        query_title = "Толкин"

        # 1. Message 1: Intermediate prompt with button repeating "Толкин"
        msg1_text = "Поиск по запросу: Толкин..."
        msg1_buttons = [[DummyButton("Толкин", "search_tolkien")]]

        res1 = downloader.parse_library_response(
            msg_text=msg1_text,
            reply_markup=msg1_buttons,
            query_title=query_title
        )
        self.assertEqual(res1, [], "Intermediate prompt button 'Толкин' must be ignored and return empty results")

        # Test pagination buttons in inline keyboard (e.g. -1-, 2, 3, 4>, 40>)
        pagination_markup = [
            [DummyButton("-1-", "page_1"), DummyButton("2", "page_2"), DummyButton("3", "page_3"), DummyButton("4>", "page_4"), DummyButton("40>", "page_40")]
        ]
        res_page = downloader.parse_library_response(
            msg_text="",
            reply_markup=pagination_markup,
            query_title=query_title
        )
        self.assertEqual(res_page, [], "Pagination buttons must be ignored and return empty results")

        # 2. Message 2: Full book card message
        msg2_text = (
            "Толкин и Великая война. На пороге Средиземья - ru\n"
            "Толкин – творец Средиземья\n"
            "Джон Гарт\n"
            "Скачать книгу: /download682541"
        )
        res2 = downloader.parse_library_response(
            msg_text=msg2_text,
            reply_markup=None,
            query_title=query_title
        )
        self.assertEqual(len(res2), 1)
        self.assertEqual(res2[0]["title"], "Толкин и Великая война. На пороге Средиземья")
        self.assertEqual(res2[0]["author"], "Джон Гарт")
        self.assertEqual(res2[0]["download_cmd"], "/download682541")
        self.assertNotEqual(res2[0]["title"], "Толкин")

    async def test_download_locks(self):
        lock1 = bot.get_download_lock(chat_id=100, book_id=1)
        lock2 = bot.get_download_lock(chat_id=100, book_id=1)
        self.assertIs(lock1, lock2)

        async with lock1:
            self.assertTrue(lock2.locked())

        self.assertFalse(lock2.locked())
    async def asyncSetUp(self):
        self.db_path = "test_run.sqlite"
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        await database.init_db(self.db_path)

    async def asyncTearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    async def test_welcome_keyboard_and_help(self):
        # Test keyboard button order
        kb = bot.get_welcome_keyboard("ru", "test_bot")
        inline_rows = kb.inline_keyboard
        self.assertEqual(len(inline_rows), 4)
        self.assertEqual(inline_rows[0][0].text, i18n.t("btn_how_it_works", "ru"))
        self.assertEqual(inline_rows[0][0].callback_data, "show_help")
        self.assertEqual(inline_rows[1][0].text, i18n.t("btn_rate_backlog", "ru"))
        self.assertEqual(inline_rows[1][0].url, "https://t.me/test_bot?start=rate_new")
        self.assertEqual(inline_rows[2][0].text, i18n.t("btn_open_control_panel", "ru"))
        self.assertEqual(inline_rows[2][0].callback_data, "open_control_panel")
        self.assertEqual(inline_rows[3][0].text, i18n.t("btn_report_error", "ru"))
        self.assertEqual(inline_rows[3][0].callback_data, "report_error")

        # Test help text content
        help_ru = i18n.t("how_it_works_text", "ru")
        self.assertIn("Как работает BookVoter", help_ru)
        self.assertIn("/suggest", help_ru)
        self.assertIn("Зал славы", help_ru)
        # Verify no mention of genres / genre rotation / Google Books
        self.assertNotIn("жанр", help_ru.lower())
        self.assertNotIn("google", help_ru.lower())

        help_en = i18n.t("how_it_works_text", "en")
        self.assertIn("How BookVoter Works", help_en)
        self.assertIn("/suggest", help_en)
        self.assertIn("Hall of Fame", help_en)
        self.assertNotIn("genre", help_en.lower())
        self.assertNotIn("google", help_en.lower())

    async def test_language_management(self):
        lang_default = await database.get_effective_language(self.db_path, chat_id=-1001)
        self.assertEqual(lang_default, "en")

        await database.register_or_update_chat(self.db_path, chat_id=-1001, title="Test Group")
        await database.set_chat_language(self.db_path, chat_id=-1001, language_code="ru")
        lang_ru = await database.get_effective_language(self.db_path, chat_id=-1001)
        self.assertEqual(lang_ru, "ru")

        self.assertIn("Добро пожаловать", i18n.t("welcome_msg", "ru"))
        self.assertIn("Welcome", i18n.t("welcome_msg", "en"))

    async def test_user_and_chat_management(self):
        u_id = await database.get_or_create_user(self.db_path, tg_id=12345, username="testuser", full_name="Test User")
        self.assertEqual(u_id, 1)

        u_id2 = await database.get_or_create_user(self.db_path, tg_id=12345, username="updated_user", full_name="Updated Name")
        self.assertEqual(u_id2, 1)

        await database.register_or_update_chat(self.db_path, chat_id=-100123, title="Book Club Chat")
        stats = await database.get_sys_stats(self.db_path)
        self.assertEqual(stats["total_active_chats"], 1)
        self.assertEqual(stats["total_unique_users"], 1)

    async def test_books_ratings_and_backlog_full(self):
        u_id = await database.get_or_create_user(self.db_path, tg_id=1001, username="alice", full_name="Alice Smith")
        b1_id = await database.add_book(self.db_path, chat_id=-100, title="Dune", author="Frank Herbert", genre="Sci-Fi", suggested_by_tg_id=1001)
        b2_id = await database.add_book(self.db_path, chat_id=-100, title="The Hobbit", author="J.R.R. Tolkien", genre="Fantasy", suggested_by_tg_id=1001)

        # Verify full backlog information query
        full_backlog = await database.get_backlog_books_full_info(self.db_path, chat_id=-100)
        self.assertEqual(len(full_backlog), 2)
        self.assertEqual(full_backlog[0]["suggestor_name"], "Alice Smith")

        # Test rating
        await database.save_backlog_rating(self.db_path, tg_id=1001, book_id=b1_id, score=9)

        # Detailed chat stats
        chat_stats = await database.get_chat_stats_detailed(self.db_path, chat_id=-100)
        self.assertEqual(chat_stats["backlog_count"], 2)
        self.assertEqual(len(chat_stats["top_contributors"]), 1)

        # Audit backlog activity (departed/inactive)
        audit = await database.audit_backlog_activity(self.db_path, chat_id=-100, active_tg_ids=[1001])
        # Alice is in active_tg_ids and has voted, so audit should be clean
        self.assertEqual(len(audit), 0)

        # Audit with departed user
        audit_departed = await database.audit_backlog_activity(self.db_path, chat_id=-100, active_tg_ids=[])
        self.assertEqual(len(audit_departed), 2)

    async def test_top_backlog_books_selection_logic(self):
        # Setup previously read/won book (its genre must NOT affect selection now)
        b0_id = await database.add_book(self.db_path, chat_id=-100, title="Foundation", author="Isaac Asimov", genre="Sci-Fi")
        await database.update_books_status(self.db_path, [b0_id], "won")

        # Setup backlog books
        b1_id = await database.add_book(self.db_path, chat_id=-100, title="Hyperion", author="Dan Simmons", genre="Sci-Fi")
        b2_id = await database.add_book(self.db_path, chat_id=-100, title="Name of the Wind", author="Patrick Rothfuss", genre="Fantasy")
        b3_id = await database.add_book(self.db_path, chat_id=-100, title="Dune", author="Frank Herbert", genre="Sci-Fi")
        b4_id = await database.add_book(self.db_path, chat_id=-100, title="1984", author="George Orwell", genre=None)

        u1 = await database.get_or_create_user(self.db_path, tg_id=101, username="user1")
        u2 = await database.get_or_create_user(self.db_path, tg_id=102, username="user2")

        # Ratings:
        # b1 (Hyperion, Sci-Fi): 10 & 8 -> Avg = 9.0
        # b2 (Name of the Wind, Fantasy): 8 & 8 -> Avg = 8.0
        # b3 (Dune, Sci-Fi): 8 & 8 -> Avg = 8.0
        # b4 (1984, None): No ratings -> Avg = 0.0

        await database.save_backlog_rating(self.db_path, tg_id=101, book_id=b1_id, score=10)
        await database.save_backlog_rating(self.db_path, tg_id=102, book_id=b1_id, score=8)

        await database.save_backlog_rating(self.db_path, tg_id=101, book_id=b2_id, score=8)
        await database.save_backlog_rating(self.db_path, tg_id=102, book_id=b2_id, score=8)

        await database.save_backlog_rating(self.db_path, tg_id=101, book_id=b3_id, score=8)
        await database.save_backlog_rating(self.db_path, tg_id=102, book_id=b3_id, score=8)

        top_books = await database.get_top_backlog_books_for_vote(self.db_path, chat_id=-100, limit=3)
        self.assertEqual(len(top_books), 3)

        # 1st book: b1 (avg=9.0) despite b0 genre being Sci-Fi!
        self.assertEqual(top_books[0]["id"], b1_id)
        self.assertEqual(top_books[0]["avg_score"], 9.0)

        # Tie-break check for avg=8.0 (b2_id vs b3_id sorted by id ASC):
        self.assertEqual(top_books[1]["id"], b2_id)
        self.assertEqual(top_books[2]["id"], b3_id)

        # Check limit=4 to verify zero-rating book included last
        all_top = await database.get_top_backlog_books_for_vote(self.db_path, chat_id=-100, limit=4)
        self.assertEqual(all_top[3]["id"], b4_id)
        self.assertEqual(all_top[3]["avg_score"], 0.0)

    async def test_superadmin_stats_detailed(self):
        u_id = await database.get_or_create_user(self.db_path, tg_id=2001, username="bob")
        b_id = await database.add_book(self.db_path, chat_id=-200, title="1984", author="George Orwell", genre="Dystopia", suggested_by_tg_id=2001)
        await database.save_backlog_rating(self.db_path, tg_id=2001, book_id=b_id, score=10)

        sa_stats = await database.get_superadmin_stats_detailed(self.db_path, days=30)
        self.assertIn("total_voters", sa_stats)
        self.assertEqual(sa_stats["total_voters"], 1)
        self.assertEqual(len(sa_stats["top_books"]), 1)

    async def test_post_reading_ratings_and_hof(self):
        u1_id = await database.get_or_create_user(self.db_path, tg_id=3001, username="user1")
        u2_id = await database.get_or_create_user(self.db_path, tg_id=3002, username="user2")
        u3_id = await database.get_or_create_user(self.db_path, tg_id=3003, username="user3")

        b_id = await database.add_book(self.db_path, chat_id=-300, title="1984", author="George Orwell", genre="Dystopia")
        await database.update_books_status(self.db_path, [b_id], "won")

        # Save post read ratings
        await database.save_read_rating(self.db_path, tg_id=3001, book_id=b_id, score=10)
        await database.save_read_rating(self.db_path, tg_id=3002, book_id=b_id, score=8)
        await database.save_read_rating(self.db_path, tg_id=3003, book_id=b_id, score=None) # Didn't read

        res = await database.update_hall_of_fame_rating(self.db_path, b_id, chat_id=-300)
        self.assertEqual(res["avg_rating"], 9.0)
        self.assertEqual(res["votes_count"], 2)

        # Test min_votes threshold filtering in get_hall_of_fame_detailed
        hof_data = await database.get_hall_of_fame_detailed(self.db_path, chat_id=-300, min_votes=3)
        self.assertEqual(len(hof_data["qualified"]), 0)
        self.assertEqual(len(hof_data["low_votes"]), 1)

        hof_data_2 = await database.get_hall_of_fame_detailed(self.db_path, chat_id=-300, min_votes=2)
        self.assertEqual(len(hof_data_2["qualified"]), 1)
        self.assertEqual(hof_data_2["qualified"][0]["avg_score"], 9.0)
        # Check weighted rating calculation
        self.assertIn("weighted_rating", hof_data_2["qualified"][0])

        # Test soft delete / hide from Hall of Fame
        hidden = await database.hide_book_from_hall_of_fame(self.db_path, b_id, chat_id=-300)
        self.assertTrue(hidden)

        hof_data_3 = await database.get_hall_of_fame_detailed(self.db_path, chat_id=-300, min_votes=2)
        self.assertEqual(len(hof_data_3["qualified"]), 0)
        self.assertEqual(len(hof_data_3["low_votes"]), 0)

if __name__ == "__main__":
    unittest.main()
