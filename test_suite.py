import asyncio
import os
import sys
import unittest
from datetime import datetime

import database
import bot
import i18n

class TestBookVoter(unittest.IsolatedAsyncioTestCase):
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

    async def test_books_and_ratings(self):
        u_id = await database.get_or_create_user(self.db_path, tg_id=1001, username="alice")
        b1_id = await database.add_book(self.db_path, chat_id=-100, title="Dune", author="Frank Herbert", genre="Sci-Fi", suggested_by_tg_id=1001)
        b2_id = await database.add_book(self.db_path, chat_id=-100, title="The Hobbit", author="J.R.R. Tolkien", genre="Fantasy", suggested_by_tg_id=1001)

        # Test duplicate book check
        exists = await database.is_book_exists(self.db_path, chat_id=-100, title="Dune", author="Frank Herbert")
        self.assertTrue(exists)
        not_exists = await database.is_book_exists(self.db_path, chat_id=-100, title="1984", author="George Orwell")
        self.assertFalse(not_exists)

        unrated = await database.get_unrated_backlog_books_for_user(self.db_path, tg_id=1001)
        self.assertEqual(len(unrated), 2)

        await database.save_backlog_rating(self.db_path, tg_id=1001, book_id=b1_id, score=9)
        unrated2 = await database.get_unrated_backlog_books_for_user(self.db_path, tg_id=1001)
        self.assertEqual(len(unrated2), 1)

        top_books = await database.get_top_backlog_books_for_vote(self.db_path, chat_id=-100, limit=2)
        self.assertEqual(top_books[0]["id"], b1_id)
        self.assertEqual(top_books[0]["avg_score"], 9.0)

        # Test book deletion
        deleted = await database.delete_book(self.db_path, b1_id, chat_id=-100)
        self.assertTrue(deleted)
        backlog = await database.get_backlog_books_for_chat(self.db_path, chat_id=-100)
        self.assertEqual(len(backlog), 1)
        self.assertEqual(backlog[0]["id"], b2_id)

    async def test_genre_rotation(self):
        b0_id = await database.add_book(self.db_path, chat_id=-100, title="Foundation", author="Isaac Asimov", genre="Sci-Fi")
        await database.update_books_status(self.db_path, [b0_id], "won")

        b1_id = await database.add_book(self.db_path, chat_id=-100, title="Hyperion", author="Dan Simmons", genre="Sci-Fi")
        b2_id = await database.add_book(self.db_path, chat_id=-100, title="Name of the Wind", author="Patrick Rothfuss", genre="Fantasy")

        top = await database.get_top_backlog_books_for_vote(self.db_path, chat_id=-100, limit=1)
        self.assertEqual(top[0]["id"], b2_id)

    async def test_google_books_api(self):
        results = await bot.fetch_google_books("Python Programming", "en")
        if results is not None:
            for r in results:
                self.assertIsNotNone(r["title"])
                self.assertIsNotNone(r["author"])
                self.assertNotIn(r["author"].lower(), ["unknown author", "неизвестный автор"])

if __name__ == "__main__":
    unittest.main()
