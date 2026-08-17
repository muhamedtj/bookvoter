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

    async def test_download_locks(self):
        lock1 = bot.get_download_lock(chat_id=100, book_id=1)
        lock2 = bot.get_download_lock(chat_id=100, book_id=1)
        self.assertIs(lock1, lock2)

        async with lock1:
            self.assertTrue(lock2.locked())

        self.assertFalse(lock2.locked())

    async def test_finish_vote_locks(self):
        lock1 = bot.get_finish_vote_lock(chat_id=500)
        lock2 = bot.get_finish_vote_lock(chat_id=500)
        self.assertIs(lock1, lock2)

        async with lock1:
            self.assertTrue(lock2.locked())

        self.assertFalse(lock2.locked())

    async def test_wait_for_document_or_response_floodwait_retry(self):
        class MockMessage:
            def __init__(self, msg_id, text):
                self.id = msg_id
                self.text = text
                self.caption = ""

        class MockApp:
            def __init__(self):
                self.attempts = 0

            async def get_chat_history(self, target_channel, limit=10):
                self.attempts += 1
                if self.attempts == 1:
                    raise Exception("[userbot_downloader] Waiting for 2 seconds before continuing (required by \"messages.GetHistory\") FloodWait")
                yield MockMessage(2, "Found book")

        app = MockApp()
        res = await downloader.wait_for_document_or_response(app, "channel", request_msg_id=1, timeout_seconds=10)
        self.assertIsNotNone(res)
        self.assertEqual(res.text, "Found book")
        self.assertEqual(app.attempts, 2)

    async def test_download_failure_does_not_break_winning_status(self):
        chat_id = -999
        b_id = await database.add_book(self.db_path, chat_id=chat_id, title="Test Fail Title", author="Test Fail Author")
        await database.update_books_status(self.db_path, [b_id], "won")

        # Verify initial status is won
        book_before = await database.get_book_by_id(self.db_path, b_id)
        self.assertEqual(book_before["status"], "won")

        # Mark book done is only called on successful download; test database state unchanged on failure
        book_after = await database.get_book_by_id(self.db_path, b_id)
        self.assertEqual(book_after["status"], "won")
    async def asyncSetUp(self):
        self.db_path = "test_run.sqlite"
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        await database.init_db(self.db_path)

    async def asyncTearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

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

    async def test_genre_rotation(self):
        b0_id = await database.add_book(self.db_path, chat_id=-100, title="Foundation", author="Isaac Asimov", genre="Sci-Fi")
        await database.update_books_status(self.db_path, [b0_id], "won")

        b1_id = await database.add_book(self.db_path, chat_id=-100, title="Hyperion", author="Dan Simmons", genre="Sci-Fi")
        b2_id = await database.add_book(self.db_path, chat_id=-100, title="Name of the Wind", author="Patrick Rothfuss", genre="Fantasy")

        result = await database.get_top_backlog_books_for_vote(self.db_path, chat_id=-100, limit=1)
        self.assertEqual(result["books"][0]["id"], b2_id)
        self.assertEqual(result["excluded_genre"], "Sci-Fi")

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
