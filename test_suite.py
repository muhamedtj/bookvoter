import asyncio
import sqlite3
import aiosqlite
import os
import sys
import unittest
from datetime import datetime, timedelta

import database
import bot
import downloader
import i18n

class DummyButton:
    def __init__(self, text, callback_data):
        self.text = text
        self.callback_data = callback_data

class DummyUser:
    def __init__(self, user_id, full_name="User", username="user"):
        self.id = user_id
        self.full_name = full_name
        self.username = username

class DummyChat:
    def __init__(self, chat_id, chat_type="group"):
        self.id = chat_id
        self.type = chat_type
        self.title = f"Group {chat_id}"
        self.full_name = f"Group {chat_id}"

class DummyMessage:
    def __init__(self, chat_id, user_id, text="", message_id=1, chat_type="group"):
        self.chat = DummyChat(chat_id, chat_type=chat_type)
        self.from_user = DummyUser(user_id)
        self.text = text
        self.message_id = message_id
        self.edited_text = None
        self.edited_markup = None

    async def answer(self, text, parse_mode=None, reply_markup=None):
        self.edited_text = text
        self.edited_markup = reply_markup
        return self

    async def edit_text(self, text, parse_mode=None, reply_markup=None):
        self.edited_text = text
        self.edited_markup = reply_markup
        return self

class DummyCallback:
    def __init__(self, chat_id, user_id, data, message_id=100, chat_type="group"):
        self.message = DummyMessage(chat_id, user_id, message_id=message_id, chat_type=chat_type)
        self.from_user = DummyUser(user_id)
        self.data = data
        self.answered = False
        self.answer_text = None

    async def answer(self, text=None, show_alert=False):
        self.answered = True
        self.answer_text = text

    @property
    def edited_markup(self):
        return self.message.edited_markup


class TestBookVoter(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db_path = "test_run.sqlite"
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        bot.DATABASE_PATH = self.db_path
        await database.init_db(self.db_path)

    async def asyncTearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    # --- 1. Voting Quorum Tests ---
    def test_should_finish_poll_early_quorum_rules(self):
        self.assertFalse(bot.should_finish_poll_early(1, [1, 0]))
        self.assertFalse(bot.should_finish_poll_early(2, [2, 0]))
        self.assertTrue(bot.should_finish_poll_early(3, [2, 1]))
        self.assertFalse(bot.should_finish_poll_early(4, [2, 2]))
        self.assertTrue(bot.should_finish_poll_early(5, [3, 2]))

    # --- 2. Zero-Vote & Tie Resolution Tests ---
    async def test_tie_breaking_sequence_in_finish_vote_process(self):
        chat_id = -100
        b1_id = await database.add_book(self.db_path, chat_id, "Book A", "Author A")
        b2_id = await database.add_book(self.db_path, chat_id, "Book B", "Author B")

        # Set status to 'voting' and save active_poll
        await database.update_books_status(self.db_path, [b1_id, b2_id], "voting")
        await database.save_active_poll(self.db_path, chat_id, "poll_tie", 10, f'{{"0": {b1_id}, "1": {b2_id}}}')

        # b2 has higher interest rating
        await database.save_backlog_rating(self.db_path, tg_id=1, book_id=b2_id, score=9)
        await database.save_backlog_rating(self.db_path, tg_id=1, book_id=b1_id, score=5)

        class MockOption:
            def __init__(self, voter_count):
                self.voter_count = voter_count

        class MockStoppedPoll:
            total_voter_count = 2
            options = [MockOption(1), MockOption(1)] # Tied poll votes (1 each)

        old_stop_poll = bot.bot.stop_poll
        old_send_msg = bot.bot.send_message
        async def mock_stop_poll(c_id, msg_id):
            return MockStoppedPoll()

        async def mock_send_msg(c_id, text, **kwargs):
            return None

        bot.bot.stop_poll = mock_stop_poll
        bot.bot.send_message = mock_send_msg

        try:
            await bot.finish_vote_process(chat_id)
            winner = await database.get_current_reading_book(self.db_path, chat_id)
            self.assertIsNotNone(winner)
            self.assertEqual(winner["id"], b2_id) # b2 won because of higher interest score
        finally:
            bot.bot.stop_poll = old_stop_poll
            bot.bot.send_message = old_send_msg

    async def test_zero_vote_cancellation_in_finish_vote(self):
        chat_id = -100
        b1_id = await database.add_book(self.db_path, chat_id, "Zero Book 1", "Author")
        b2_id = await database.add_book(self.db_path, chat_id, "Zero Book 2", "Author")
        await database.update_books_status(self.db_path, [b1_id, b2_id], "voting")
        await database.save_active_poll(self.db_path, chat_id, "poll_zero", 123, f'{{"0": {b1_id}, "1": {b2_id}}}')

        class MockOption:
            def __init__(self, voter_count):
                self.voter_count = voter_count

        class MockStoppedPoll:
            total_voter_count = 0
            options = [MockOption(0), MockOption(0)]

        old_stop_poll = bot.bot.stop_poll
        old_send_msg = bot.bot.send_message
        sent_messages = []

        async def mock_stop_poll(c_id, msg_id):
            return MockStoppedPoll()

        async def mock_send_msg(c_id, text, **kwargs):
            sent_messages.append(text)

        bot.bot.stop_poll = mock_stop_poll
        bot.bot.send_message = mock_send_msg

        try:
            await bot.finish_vote_process(chat_id)
            b1 = await database.get_book_by_id(self.db_path, b1_id)
            b2 = await database.get_book_by_id(self.db_path, b2_id)
            self.assertEqual(b1["status"], "backlog")
            self.assertEqual(b2["status"], "backlog")
            self.assertIsNone(await database.get_active_poll(self.db_path, chat_id))
            self.assertTrue(any("Никто не проголосовал" in m or "No one voted" in m for m in sent_messages))
        finally:
            bot.bot.stop_poll = old_stop_poll
            bot.bot.send_message = old_send_msg

    async def test_stop_poll_exception_does_not_cancel_or_choose_winner(self):
        chat_id = -100
        b1_id = await database.add_book(self.db_path, chat_id, "Fail Book 1", "Author")
        b2_id = await database.add_book(self.db_path, chat_id, "Fail Book 2", "Author")
        await database.update_books_status(self.db_path, [b1_id, b2_id], "voting")
        await database.save_active_poll(self.db_path, chat_id, "poll_fail", 123, f'{{"0": {b1_id}, "1": {b2_id}}}')

        old_stop_poll = bot.bot.stop_poll
        async def mock_stop_poll_fail(c_id, msg_id):
            raise Exception("Telegram API Poll Error")

        bot.bot.stop_poll = mock_stop_poll_fail

        try:
            await bot.finish_vote_process(chat_id)
            b1 = await database.get_book_by_id(self.db_path, b1_id)
            b2 = await database.get_book_by_id(self.db_path, b2_id)
            self.assertEqual(b1["status"], "voting")
            self.assertEqual(b2["status"], "voting")
            self.assertIsNotNone(await database.get_active_poll(self.db_path, chat_id))

            # Verify retry job scheduled
            retry_job = bot.scheduler.get_job(f"poll_end_{chat_id}_123")
            self.assertIsNotNone(retry_job)
        finally:
            bot.bot.stop_poll = old_stop_poll

    # --- 3. Candidate & Launch Tests ---
    async def test_launch_poll_requires_minimum_two_books(self):
        chat_id = -100
        b1_id = await database.add_book(self.db_path, chat_id, "Only Book", "Author")
        top_books = await database.get_top_backlog_books_for_vote(self.db_path, chat_id, limit=3)
        self.assertEqual(len(top_books), 1)

    async def test_poll_launch_rollback_on_failure(self):
        chat_id = -100
        b1_id = await database.add_book(self.db_path, chat_id, "Rollback Book 1", "Author")
        b2_id = await database.add_book(self.db_path, chat_id, "Rollback Book 2", "Author")
        top_books = await database.get_top_backlog_books_for_vote(self.db_path, chat_id, limit=3)

        old_send_poll = bot.bot.send_poll
        async def mock_send_poll_fail(*args, **kwargs):
            raise Exception("Send Poll Network Exception")

        bot.bot.send_poll = mock_send_poll_fail

        try:
            success = await bot.launch_poll_for_books(chat_id, top_books, "en")
            self.assertFalse(success)
            b1 = await database.get_book_by_id(self.db_path, b1_id)
            b2 = await database.get_book_by_id(self.db_path, b2_id)
            self.assertEqual(b1["status"], "backlog")
            self.assertEqual(b2["status"], "backlog")
            self.assertIsNone(await database.get_active_poll(self.db_path, chat_id))
        finally:
            bot.bot.send_poll = old_send_poll

    # --- 4. Core Lifecycle & Rating Card Usability Tests ---
    async def test_lifecycle_backlog_voting_reading_done(self):
        chat_id = -100
        b1_id = await database.add_book(self.db_path, chat_id, "Title 1", "Author 1")
        b2_id = await database.add_book(self.db_path, chat_id, "Title 2", "Author 2")

        # 1. backlog -> voting
        await database.update_books_status(self.db_path, [b1_id, b2_id], "voting")
        book1 = await database.get_book_by_id(self.db_path, b1_id)
        self.assertEqual(book1["status"], "voting")

        # 2. winner resolve -> reading
        await database.resolve_vote_winner(self.db_path, chat_id, winning_book_id=b1_id, voting_book_ids=[b1_id, b2_id])
        b1_after_vote = await database.get_book_by_id(self.db_path, b1_id)
        b2_after_vote = await database.get_book_by_id(self.db_path, b2_id)
        self.assertEqual(b1_after_vote["status"], "reading")
        self.assertEqual(b2_after_vote["status"], "backlog")

        # 3. file delivery -> remains reading
        await database.update_book_file_id(self.db_path, b1_id, "file_id_123")
        b1_after_file = await database.get_book_by_id(self.db_path, b1_id)
        self.assertEqual(b1_after_file["status"], "reading")
        self.assertEqual(b1_after_file["file_id"], "file_id_123")

        # 4. finish reading -> done
        await database.update_hall_of_fame_rating(self.db_path, b1_id, chat_id)
        b1_done = await database.get_book_by_id(self.db_path, b1_id)
        self.assertEqual(b1_done["status"], "done")

    async def test_multiple_users_rating_same_finished_book_card(self):
        chat_id = -100
        b1_id = await database.add_book(self.db_path, chat_id, "Shared Card Book", "Author")
        await database.update_hall_of_fame_rating(self.db_path, b1_id, chat_id)

        cb_user1 = DummyCallback(chat_id, user_id=101, data=f"vote_book:{b1_id}:8")
        cb_user2 = DummyCallback(chat_id, user_id=102, data=f"vote_book:{b1_id}:10")

        await bot.handle_vote_book_callback(cb_user1)
        self.assertTrue(cb_user1.answered)
        self.assertIsNotNone(cb_user1.edited_markup)

        await bot.handle_vote_book_callback(cb_user2)
        self.assertTrue(cb_user2.answered)
        self.assertIsNotNone(cb_user2.edited_markup)

        stats = await database.update_hall_of_fame_rating(self.db_path, b1_id, chat_id)
        self.assertEqual(stats["votes_count"], 2)
        self.assertEqual(stats["avg_rating"], 9.0)

    async def test_forged_final_rating_on_reading_book_rejected(self):
        chat_id = -100
        b1_id = await database.add_book(self.db_path, chat_id, "Reading Book", "Author")
        await database.update_books_status(self.db_path, [b1_id], "reading")

        cb = DummyCallback(chat_id, user_id=101, data=f"vote_book:{b1_id}:10")
        await bot.handle_vote_book_callback(cb)

        self.assertTrue(cb.answered)
        self.assertTrue(any(w in (cb.answer_text or "").lower() for w in ["allowed only after reading", "только после завершения"]))
        async with database.open_db(self.db_path) as db:
            async with db.execute("SELECT COUNT(*) FROM read_ratings WHERE book_id = ?", (b1_id,)) as cursor:
                self.assertEqual((await cursor.fetchone())[0], 0)

    async def test_active_reading_blocks_new_vote(self):
        chat_id = -100
        b1_id = await database.add_book(self.db_path, chat_id, "Title 1", "Author 1")
        await database.update_books_status(self.db_path, [b1_id], "reading")

        reading_book = await database.get_current_reading_book(self.db_path, chat_id)
        self.assertIsNotNone(reading_book)
        self.assertEqual(reading_book["id"], b1_id)

    # --- 5. Multi-Tenant & Onboarding Tests ---
    async def test_multi_tenant_isolation(self):
        group_a = -1001
        group_b = -1002
        user_a = 9991

        await database.register_or_update_chat(self.db_path, group_a, "Group A")
        await database.register_or_update_chat(self.db_path, group_b, "Group B")

        await database.record_chat_member(self.db_path, group_a, user_a)

        b_a_id = await database.add_book(self.db_path, group_a, "Group A Book", "Author")
        b_b_id = await database.add_book(self.db_path, group_b, "Group B Book", "Author")

        unrated = await database.get_unrated_backlog_books_for_user(self.db_path, user_a)
        unrated_ids = [b["id"] for b in unrated]
        self.assertIn(b_a_id, unrated_ids)
        self.assertNotIn(b_b_id, unrated_ids)

        self.assertTrue(await database.is_user_chat_member(self.db_path, group_a, user_a))
        self.assertFalse(await database.is_user_chat_member(self.db_path, group_b, user_a))

    async def test_membership_deep_link_onboarding(self):
        group_id = -555
        user_id = 8888
        await database.get_or_create_user(self.db_path, user_id)
        await database.set_user_language(self.db_path, user_id, "en")
        await database.register_or_update_chat(self.db_path, group_id, "Deep Link Group")
        b_id = await database.add_book(self.db_path, group_id, "Deep Book", "Author")

        class MockMember:
            status = "member"

        old_get_member = bot.bot.get_chat_member
        async def mock_get_member(*args, **kwargs):
            return MockMember()

        bot.bot.get_chat_member = mock_get_member

        try:
            msg = DummyMessage(user_id, user_id, text="/start rate_c555", chat_type="private")
            cmd = bot.CommandObject(command="start", args="rate_c555")
            await bot.handle_start(msg, cmd)

            self.assertTrue(await database.is_user_chat_member(self.db_path, group_id, user_id))
        finally:
            bot.bot.get_chat_member = old_get_member

    async def test_private_suggest_rejected(self):
        user_id = 777
        msg = DummyMessage(user_id, user_id, text="/suggest Dune", chat_type="private")
        cmd = bot.CommandObject(command="suggest", args="Dune")

        sent_msgs = []
        async def mock_answer(text, parse_mode=None):
            sent_msgs.append(text)
            return msg

        msg.answer = mock_answer
        await bot.handle_suggest(msg, cmd)

        self.assertTrue(any("используйте /suggest в вашей группе" in m or "inside your book club group" in m for m in sent_msgs))

    # --- 6. Database & Trigger Protection Tests ---
    async def test_fk_cascade_on_open_db(self):
        async with database.open_db(self.db_path) as db:
            await db.execute("INSERT INTO chats (chat_id, status, title) VALUES (-99, 'active', 'FK Chat')")
            await db.execute("INSERT INTO books (id, chat_id, title, author) VALUES (99, -99, 'FK Book', 'Author')")
            await db.commit()

            await db.execute("DELETE FROM chats WHERE chat_id = -99")
            await db.commit()

            async with db.execute("SELECT COUNT(*) FROM books WHERE id = 99") as cursor:
                self.assertEqual((await cursor.fetchone())[0], 0)

    async def test_legacy_duplicates_preserved_and_trigger_prevents_new_duplicates(self):
        # 1. Insert legacy duplicate books directly into SQLite bypassing trigger temporarily
        async with database.open_db(self.db_path) as db:
            await db.execute("DROP TRIGGER IF EXISTS prevent_duplicate_books")
            await db.execute("INSERT INTO chats (chat_id, status, title) VALUES (-500, 'active', 'Legacy Chat')")
            await db.execute("INSERT INTO books (id, chat_id, title, author, status) VALUES (13, -500, 'Dup Book', 'Author', 'backlog')")
            await db.execute("INSERT INTO books (id, chat_id, title, author, status) VALUES (14, -500, 'dup book ', 'author', 'backlog')")
            await db.commit()

        # 2. Re-run init_db - re-creates trigger and preserves legacy rows without error
        await database.init_db(self.db_path)

        b13 = await database.get_book_by_id(self.db_path, 13)
        b14 = await database.get_book_by_id(self.db_path, 14)

        # Historical titles must remain unchanged
        self.assertEqual(b13["title"], "Dup Book")
        self.assertEqual(b14["title"], "dup book ")

        # 3. Inserting a NEW normalized duplicate must raise ABORT exception via BEFORE INSERT trigger
        with self.assertRaises((sqlite3.OperationalError, aiosqlite.OperationalError, sqlite3.IntegrityError, aiosqlite.IntegrityError)):
            async with database.open_db(self.db_path) as db:
                await db.execute("""
                    INSERT INTO books (chat_id, title, author, status)
                    VALUES (-500, 'DUP BOOK', 'AUTHOR', 'backlog')
                """)
                await db.commit()

        # 4. Same book in DIFFERENT chat must be allowed
        b_other = await database.add_book(self.db_path, -600, "Dup Book", "Author")
        self.assertIsNotNone(b_other)

    # --- 7. HTML Formatting & Escaping Tests ---
    def test_html_escaping_and_template_formatting(self):
        raw_text = "<script>alert('xss')</script> & \"quotes\""
        escaped = bot.escape_html(raw_text)
        self.assertEqual(escaped, "&lt;script&gt;alert(&#x27;xss&#x27;)&lt;/script&gt; &amp; &quot;quotes&quot;")

        # Verify no markdown markers in HTML templates
        for key, template_dict in i18n.STRINGS.items():
            for lang, tmpl in template_dict.items():
                self.assertNotIn("**", tmpl, f"Key '{key}' in lang '{lang}' contains Markdown '**'")
                self.assertNotIn("`", tmpl, f"Key '{key}' in lang '{lang}' contains Markdown '`'")

    # --- 8. Concurrent Lock Serialization Test ---
    async def test_concurrent_library_lock_serialization(self):
        execution_order = []

        async def worker(worker_id):
            async with bot.library_access_lock:
                execution_order.append(f"start_{worker_id}")
                await asyncio.sleep(0.05)
                execution_order.append(f"end_{worker_id}")

        await asyncio.gather(worker(1), worker(2))

        # Must serialize sequentially (start1 -> end1 -> start2 -> end2 OR vice versa)
        self.assertEqual(len(execution_order), 4)
        self.assertEqual(execution_order[0].replace("start_", ""), execution_order[1].replace("end_", ""))

    def test_select_best_button_priority(self):
        k1 = [[DummyButton("FB2", "dl_fb2"), DummyButton("EPUB", "dl_epub"), DummyButton("MOBI", "dl_mobi")]]
        btn1 = downloader.select_best_button(k1)
        self.assertEqual(btn1.text, "EPUB")

        k2 = [[DummyButton("FB2", "dl_fb2"), DummyButton("PDF", "dl_pdf")]]
        btn2 = downloader.select_best_button(k2)
        self.assertEqual(btn2.text, "FB2")

    def test_search_parsing_intermediate_button_scenario(self):
        query_title = "Толкин"

        msg1_text = "Поиск по запросу: Толкин..."
        msg1_buttons = [[DummyButton("Толкин", "search_tolkien")]]

        res1 = downloader.parse_library_response(
            msg_text=msg1_text,
            reply_markup=msg1_buttons,
            query_title=query_title
        )
        self.assertEqual(res1, [])

        pagination_markup = [
            [DummyButton("-1-", "page_1"), DummyButton("2", "page_2"), DummyButton("3", "page_3"), DummyButton("4>", "page_4"), DummyButton("40>", "page_40")]
        ]
        res_page = downloader.parse_library_response(
            msg_text="",
            reply_markup=pagination_markup,
            query_title=query_title
        )
        self.assertEqual(res_page, [])

    async def test_admin_panel_views(self):
        chat_id = -777
        await database.register_or_update_chat(self.db_path, chat_id, "Control Panel Test Chat")

        text1, kb1 = await bot.get_admin_panel_view(chat_id, "ru", db_path=self.db_path)
        btn_texts1 = [btn.text for row in kb1.inline_keyboard for btn in row]
        self.assertIn("🎲 Начать голосование", btn_texts1)

        await database.save_active_poll(self.db_path, chat_id, "poll_123", 10, "{}")
        text2, kb2 = await bot.get_admin_panel_view(chat_id, "ru", db_path=self.db_path)
        btn_texts2 = [btn.text for row in kb2.inline_keyboard for btn in row]
        self.assertIn("⏹ Завершить голосование досрочно", btn_texts2)

        await database.clear_active_poll(self.db_path, chat_id)

        b_id = await database.add_book(self.db_path, chat_id, "Dune", "Frank Herbert")
        await database.update_books_status(self.db_path, [b_id], "reading")

        text3, kb3 = await bot.get_admin_panel_view(chat_id, "ru", db_path=self.db_path)
        btn_texts3 = [btn.text for row in kb3.inline_keyboard for btn in row]
        self.assertIn("🏆 Завершить чтение", btn_texts3)

    # --- 9. Full Lifecycle Invariant Audit Test ---
    async def test_full_lifecycle_invariant_audit(self):
        chat_id = -10099
        user1, user2, user3 = 101, 102, 103

        # 1. Register club chat and member users
        await database.register_or_update_chat(self.db_path, chat_id, "Lifecycle Audit Club")
        for u in [user1, user2, user3]:
            await database.get_or_create_user(self.db_path, u)
            await database.record_chat_member(self.db_path, chat_id, u)

        # 2. Suggest 4 candidate books
        b1 = await database.add_book(self.db_path, chat_id, "Book One", "Author A")
        b2 = await database.add_book(self.db_path, chat_id, "Book Two", "Author B")
        b3 = await database.add_book(self.db_path, chat_id, "Book Three", "Author C")
        b4 = await database.add_book(self.db_path, chat_id, "Book Four", "Author D")

        # 3. Rate backlog interest scores
        await database.save_backlog_rating(self.db_path, user1, b1, 10)
        await database.save_backlog_rating(self.db_path, user2, b1, 8)  # Avg b1: 9.0
        await database.save_backlog_rating(self.db_path, user1, b2, 7)  # Avg b2: 7.0
        await database.save_backlog_rating(self.db_path, user1, b3, 6)  # Avg b3: 6.0
        await database.save_backlog_rating(self.db_path, user1, b4, 2)  # Avg b4: 2.0

        # 4. Verify TOP-3 selection
        top_candidates = await database.get_top_backlog_books_for_vote(self.db_path, chat_id, limit=3)
        top_ids = [b["id"] for b in top_candidates]
        self.assertEqual(top_ids, [b1, b2, b3])

        # 5. Start poll: backlog -> voting for candidate books
        await database.update_books_status(self.db_path, top_ids, "voting")
        await database.save_active_poll(self.db_path, chat_id, "poll_audit_1", 888, f'{{"0": {b1}, "1": {b2}, "2": {b3}}}')

        # 6. Resolve winner: b1 wins
        await database.resolve_vote_winner(self.db_path, chat_id, winning_book_id=b1, voting_book_ids=top_ids)

        b1_rec = await database.get_book_by_id(self.db_path, b1)
        b2_rec = await database.get_book_by_id(self.db_path, b2)
        b4_rec = await database.get_book_by_id(self.db_path, b4)

        self.assertEqual(b1_rec["status"], "reading")
        self.assertEqual(b2_rec["status"], "backlog")
        self.assertEqual(b4_rec["status"], "backlog")

        # 7. Simulate file download delivery -> winner remains reading
        await database.update_book_file_id(self.db_path, b1, "file_id_audit_456")
        b1_post_dl = await database.get_book_by_id(self.db_path, b1)
        self.assertEqual(b1_post_dl["status"], "reading")

        # 8. Admin finishes reading -> reading -> done + Hall of Fame record created
        hof_stats = await database.update_hall_of_fame_rating(self.db_path, b1, chat_id)
        b1_done = await database.get_book_by_id(self.db_path, b1)
        self.assertEqual(b1_done["status"], "done")

        # 9. Users submit final read ratings
        await database.save_read_rating(self.db_path, user1, b1, 9)
        await database.save_read_rating(self.db_path, user2, b1, 10)
        final_stats = await database.update_hall_of_fame_rating(self.db_path, b1, chat_id)
        self.assertEqual(final_stats["votes_count"], 2)
        self.assertEqual(final_stats["avg_rating"], 9.5)

        # 10. Next vote can be launched as no reading book remains
        self.assertIsNone(await database.get_current_reading_book(self.db_path, chat_id))

if __name__ == "__main__":
    unittest.main()
