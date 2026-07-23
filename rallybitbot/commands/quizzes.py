from __future__ import annotations

import asyncio
import random
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import discord
from discord import app_commands

from config.config import ACTIVE_QUIZZES_FILE, QUIZ_HISTORY_FILE, QUIZ_SETTINGS_FILE, QUIZ_STATS_FILE
from core.checks import bot_can_run
from core.logging import log_action_to_channel, log_server_event
from storage.json_store import load_json, save_json

BRAND_COLOUR = 0x5865F2
SUCCESS_COLOUR = 0x57F287
WARNING_COLOUR = 0xFEE75C

# A built-in question bank keeps quizzes dependable even when external APIs are down.
QUESTION_BANK: list[dict[str, Any]] = [
    {"category": "general", "question": "Which instrument usually has 88 keys?", "choices": ["Violin", "Piano", "Trumpet", "Flute"], "answer": 1, "explanation": "A standard modern piano has 88 keys."},
    {"category": "general", "question": "What colour do you get by mixing blue and yellow paint?", "choices": ["Purple", "Orange", "Green", "Pink"], "answer": 2, "explanation": "Blue and yellow pigments combine to make green."},
    {"category": "general", "question": "How many sides does a hexagon have?", "choices": ["Five", "Six", "Seven", "Eight"], "answer": 1, "explanation": "The prefix hex- means six."},
    {"category": "general", "question": "Which month has an extra day during a leap year?", "choices": ["January", "February", "March", "December"], "answer": 1, "explanation": "February has 29 days in a leap year."},
    {"category": "science", "question": "What gas do plants absorb from the atmosphere?", "choices": ["Oxygen", "Hydrogen", "Carbon dioxide", "Helium"], "answer": 2, "explanation": "Plants use carbon dioxide during photosynthesis."},
    {"category": "science", "question": "Which planet is known for its prominent rings?", "choices": ["Mars", "Saturn", "Venus", "Mercury"], "answer": 1, "explanation": "Saturn has the Solar System's most visible ring system."},
    {"category": "science", "question": "What is H2O commonly called?", "choices": ["Salt", "Water", "Oxygen", "Sugar"], "answer": 1, "explanation": "H2O is the chemical formula for water."},
    {"category": "science", "question": "Which part of a cell contains most of its genetic material?", "choices": ["Nucleus", "Cell wall", "Cytoplasm", "Membrane"], "answer": 0, "explanation": "In eukaryotic cells, most DNA is stored in the nucleus."},
    {"category": "science", "question": "What force pulls objects toward Earth?", "choices": ["Magnetism", "Friction", "Gravity", "Electricity"], "answer": 2, "explanation": "Gravity attracts objects with mass toward one another."},
    {"category": "geography", "question": "What is the capital city of Japan?", "choices": ["Kyoto", "Osaka", "Tokyo", "Sapporo"], "answer": 2, "explanation": "Tokyo is Japan's capital."},
    {"category": "geography", "question": "Which ocean lies between Africa and Australia?", "choices": ["Atlantic", "Indian", "Arctic", "Southern"], "answer": 1, "explanation": "The Indian Ocean separates much of Africa from Australia."},
    {"category": "geography", "question": "Mount Everest is part of which mountain range?", "choices": ["Andes", "Alps", "Himalayas", "Rockies"], "answer": 2, "explanation": "Mount Everest is in the Himalayas."},
    {"category": "geography", "question": "Which country has a maple leaf on its flag?", "choices": ["Canada", "Norway", "Finland", "Austria"], "answer": 0, "explanation": "Canada's flag features a red maple leaf."},
    {"category": "gaming", "question": "In chess, which piece moves in an L-shape?", "choices": ["Bishop", "Rook", "Knight", "Queen"], "answer": 2, "explanation": "The knight moves two squares one way and one square sideways."},
    {"category": "gaming", "question": "What does NPC usually stand for in games?", "choices": ["New Player Character", "Non-Player Character", "Network Play Controller", "Next Play Challenge"], "answer": 1, "explanation": "NPC means non-player character."},
    {"category": "gaming", "question": "Which company created the Nintendo Switch?", "choices": ["Sony", "Microsoft", "Nintendo", "Sega"], "answer": 2, "explanation": "The Switch is a Nintendo console."},
    {"category": "gaming", "question": "In a standard deck, how many playing cards are there without jokers?", "choices": ["48", "50", "52", "54"], "answer": 2, "explanation": "A standard deck contains 52 cards."},
    {"category": "internet", "question": "What does URL stand for?", "choices": ["Universal Route Link", "Uniform Resource Locator", "User Reference List", "Unified Response Language"], "answer": 1, "explanation": "URL stands for Uniform Resource Locator."},
    {"category": "internet", "question": "Which symbol is used in every standard email address?", "choices": ["#", "@", "&", "%"], "answer": 1, "explanation": "The @ symbol separates the mailbox name from its domain."},
    {"category": "internet", "question": "What does HTML primarily describe?", "choices": ["A webpage's structure", "A database engine", "An image format", "A Wi-Fi standard"], "answer": 0, "explanation": "HTML defines the structure and meaning of webpage content."},
    {"category": "internet", "question": "Which protocol is the secure version of HTTP?", "choices": ["FTP", "SMTP", "HTTPS", "SSH-D"], "answer": 2, "explanation": "HTTPS protects HTTP traffic using encryption."},
    {"category": "discord", "question": "What is a collection of channels and members called on Discord?", "choices": ["Server", "Thread", "Stage", "Forum post"], "answer": 0, "explanation": "Discord communities are organised into servers."},
    {"category": "discord", "question": "Which Discord feature keeps a focused conversation inside a channel?", "choices": ["Thread", "Role", "Webhook", "Emoji"], "answer": 0, "explanation": "Threads create focused sub-conversations inside channels."},
    {"category": "discord", "question": "What can server roles commonly control?", "choices": ["Permissions", "Internet speed", "Screen brightness", "Account passwords"], "answer": 0, "explanation": "Roles are commonly used to organise members and grant permissions."},
    {"category": "logic", "question": "What number comes next: 2, 4, 8, 16, ...?", "choices": ["18", "24", "30", "32"], "answer": 3, "explanation": "Each number is doubled, so 16 becomes 32."},
    {"category": "logic", "question": "If all Bloops are Razzies and all Razzies are Lazzies, every Bloop is a...?", "choices": ["Lazzy", "Maybe Lazzy", "Not Lazzy", "None"], "answer": 0, "explanation": "The relationship is transitive: every Bloop is a Lazzy."},
    {"category": "logic", "question": "Which value is the odd one out?", "choices": ["4", "9", "16", "20"], "answer": 3, "explanation": "4, 9 and 16 are perfect squares; 20 is not."},
]

CATEGORIES = sorted({q["category"] for q in QUESTION_BANK})
active_quizzes: dict[int, "QuizSession"] = {}


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _guild_quiz_settings(guild_id: int) -> dict[str, Any]:
    all_settings = load_json(QUIZ_SETTINGS_FILE) or {}
    defaults = {
        "enabled": False,
        "channel_id": None,
        "interval_hours": 12,
        "category": "mixed",
        "duration_seconds": 30,
        "ping_role_id": None,
        "last_run": "2000-01-01T00:00:00+00:00",
    }
    saved = all_settings.get(str(guild_id), {})
    if isinstance(saved, dict):
        defaults.update(saved)
    return defaults


def _save_guild_quiz_settings(guild_id: int, settings: dict[str, Any]) -> None:
    all_settings = load_json(QUIZ_SETTINGS_FILE) or {}
    all_settings[str(guild_id)] = settings
    save_json(QUIZ_SETTINGS_FILE, all_settings)


def _choose_question(category: str) -> dict[str, Any]:
    category = (category or "mixed").lower().strip()
    candidates = QUESTION_BANK if category == "mixed" else [q for q in QUESTION_BANK if q["category"] == category]
    return dict(random.choice(candidates or QUESTION_BANK))


def _quiz_stats_for(guild_id: int) -> tuple[dict[str, Any], dict[str, Any]]:
    all_stats = load_json(QUIZ_STATS_FILE) or {}
    guild_stats = all_stats.setdefault(str(guild_id), {})
    return all_stats, guild_stats


def _active_quiz_store() -> dict[str, Any]:
    data = load_json(ACTIVE_QUIZZES_FILE) or {}
    return data if isinstance(data, dict) else {}


def _save_active_quiz(session: "QuizSession", phase: str = "active", reason: str | None = None) -> None:
    """Persist a live quiz after every meaningful state change.

    The file is intentionally separate from quiz history. It is a short-lived
    recovery ledger and is deleted as soon as the round is safely finalised.
    """
    data = _active_quiz_store()
    data[str(session.guild.id)] = {
        "schema": 2,
        "phase": phase,
        "reason": reason,
        "guild_id": session.guild.id,
        "channel_id": session.channel.id,
        "message_id": session.message.id if session.message else None,
        "quiz_id": session.quiz_id,
        "question": session.question,
        "duration": session.duration,
        "starter_id": session.starter.id,
        "automatic": session.automatic,
        "started_at": session.started_at,
        "started_epoch": session.started_epoch,
        "end_time": session.end_time,
        "answers": {str(uid): answer for uid, answer in session.answers.items()},
        "notified_role_id": session.notified_role_id,
    }
    save_json(ACTIVE_QUIZZES_FILE, data)


def _remove_active_quiz(guild_id: int | str) -> None:
    data = _active_quiz_store()
    if data.pop(str(guild_id), None) is not None:
        save_json(ACTIVE_QUIZZES_FILE, data)


def _record_history(session: "QuizSession", correct_ids: list[int]) -> None:
    history = load_json(QUIZ_HISTORY_FILE) or {}
    entries = history.setdefault(str(session.guild.id), [])
    if any(str(entry.get("quiz_id")) == session.quiz_id for entry in entries if isinstance(entry, dict)):
        return
    entries.append({
        "quiz_id": session.quiz_id,
        "channel_id": session.channel.id,
        "question": session.question["question"],
        "category": session.question["category"],
        "correct_answer": session.question["choices"][session.question["answer"]],
        "responses": len(session.answers),
        "correct_responses": len(correct_ids),
        "started_at": session.started_at,
        "ended_at": _utc_iso(),
        "automatic": session.automatic,
    })
    history[str(session.guild.id)] = entries[-250:]
    save_json(QUIZ_HISTORY_FILE, history)


def _apply_quiz_stats_once(session: "QuizSession", correct_index: int) -> None:
    """Apply points exactly once, even if a restart interrupts finalisation."""
    all_stats, guild_stats = _quiz_stats_for(session.guild.id)
    processed = all_stats.setdefault("__processed_quizzes__", [])
    if not isinstance(processed, list):
        processed = []
        all_stats["__processed_quizzes__"] = processed
    if session.quiz_id in processed:
        return

    for user_id, answer in session.answers.items():
        uid = str(user_id)
        record = guild_stats.setdefault(uid, {
            "correct": 0,
            "answered": 0,
            "points": 0,
            "streak": 0,
            "best_streak": 0,
            "display_name": answer.get("name", uid),
        })
        record["answered"] = int(record.get("answered", 0)) + 1
        record["display_name"] = answer.get("name", record.get("display_name", uid))
        if int(answer.get("answer", -1)) == correct_index:
            speed_bonus = max(0, int(session.duration - float(answer.get("elapsed", session.duration))))
            record["correct"] = int(record.get("correct", 0)) + 1
            record["streak"] = int(record.get("streak", 0)) + 1
            record["best_streak"] = max(int(record.get("best_streak", 0)), record["streak"])
            record["points"] = int(record.get("points", 0)) + 100 + speed_bonus
        else:
            record["streak"] = 0

    all_stats[str(session.guild.id)] = guild_stats
    processed.append(session.quiz_id)
    # A large rolling journal is enough to make recovery idempotent without
    # letting metadata grow forever.
    all_stats["__processed_quizzes__"] = processed[-10000:]
    save_json(QUIZ_STATS_FILE, all_stats)


class AnswerButton(discord.ui.Button):
    def __init__(self, quiz_id: str, index: int, label: str):
        super().__init__(
            label=f"{chr(65 + index)}. {label}"[:80],
            style=discord.ButtonStyle.primary,
            row=index // 2,
            custom_id=f"rallybit:quiz:{quiz_id}:{index}",
        )
        self.answer_index = index

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, QuizView):
            return
        await view.session.submit_answer(interaction, self.answer_index)


class QuizView(discord.ui.View):
    def __init__(self, session: "QuizSession"):
        super().__init__(timeout=None)
        self.session = session
        for index, choice in enumerate(session.question["choices"]):
            self.add_item(AnswerButton(session.quiz_id, index, choice))

    def lock(self) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
                if getattr(item, "answer_index", -1) == self.session.question["answer"]:
                    item.style = discord.ButtonStyle.success


class QuizSession:
    def __init__(
        self,
        guild: discord.Guild,
        channel: discord.TextChannel,
        question: dict[str, Any],
        duration: int,
        starter: discord.abc.User,
        automatic: bool = False,
        *,
        resume_data: dict[str, Any] | None = None,
    ):
        self.guild = guild
        self.channel = channel
        self.question = dict(question)
        self.duration = int(duration)
        self.starter = starter
        self.automatic = bool(automatic)
        self.message: discord.Message | None = None
        self.finished = False
        self._finish_lock = asyncio.Lock()
        self._retry_task: asyncio.Task | None = None

        if resume_data:
            self.quiz_id = str(resume_data.get("quiz_id") or uuid.uuid4().hex[:8].upper())
            self.started_at = str(resume_data.get("started_at") or _utc_iso())
            try:
                self.started_epoch = float(resume_data.get("started_epoch"))
            except (TypeError, ValueError):
                try:
                    self.started_epoch = datetime.fromisoformat(self.started_at).timestamp()
                except (TypeError, ValueError):
                    self.started_epoch = time.time()
            try:
                self.end_time = float(resume_data.get("end_time"))
            except (TypeError, ValueError):
                self.end_time = self.started_epoch + self.duration
            saved_answers = resume_data.get("answers", {})
            self.answers = {
                int(uid): dict(answer)
                for uid, answer in saved_answers.items()
                if str(uid).isdigit() and isinstance(answer, dict)
            } if isinstance(saved_answers, dict) else {}
            try:
                role_id = resume_data.get("notified_role_id")
                self.notified_role_id = int(role_id) if role_id else None
            except (TypeError, ValueError):
                self.notified_role_id = None
        else:
            self.quiz_id = uuid.uuid4().hex[:8].upper()
            self.answers: dict[int, dict[str, Any]] = {}
            self.started_epoch = time.time()
            self.started_at = datetime.fromtimestamp(self.started_epoch, tz=timezone.utc).isoformat()
            self.end_time = self.started_epoch + self.duration
            self.notified_role_id: int | None = None

        self.view = QuizView(self)

    def question_embed(self) -> discord.Embed:
        remaining = max(0, round(self.end_time - time.time()))
        embed = discord.Embed(
            title=f"🧠 Community Quiz • {self.question['category'].title()}",
            description=f"## {self.question['question']}\nChoose an answer below. Your answer stays hidden until the round ends.",
            colour=BRAND_COLOUR,
        )
        embed.add_field(name="Time", value=f"`{remaining or self.duration} seconds`", inline=True)
        embed.add_field(name="Scoring", value="Correctness + response speed", inline=True)
        embed.add_field(name="Quiz ID", value=f"`{self.quiz_id}`", inline=True)
        embed.set_footer(text="Rallybit Community Quiz • One answer per member")
        return embed

    async def submit_answer(self, interaction: discord.Interaction, answer_index: int) -> None:
        if self.finished or time.time() >= self.end_time:
            if not self.finished:
                asyncio.create_task(self.finish("time"), name=f"rallybit-quiz-expire-{self.guild.id}")
            return await interaction.response.send_message("That quiz has already ended.", ephemeral=True)
        if interaction.user.bot:
            return await interaction.response.send_message("Bots cannot enter community quizzes.", ephemeral=True)
        if interaction.guild_id != self.guild.id or not interaction.message or (self.message and interaction.message.id != self.message.id):
            return await interaction.response.send_message("That quiz control is no longer valid.", ephemeral=True)
        if interaction.user.id in self.answers:
            old = int(self.answers[interaction.user.id]["answer"])
            return await interaction.response.send_message(f"Your answer is already locked as **{chr(65 + old)}**.", ephemeral=True)

        elapsed = max(0.0, min(float(self.duration), time.time() - self.started_epoch))
        self.answers[interaction.user.id] = {
            "answer": int(answer_index),
            "elapsed": elapsed,
            "name": interaction.user.display_name,
        }
        _save_active_quiz(self)
        await interaction.response.send_message(
            f"🔒 Answer **{chr(65 + answer_index)}** locked in • `{elapsed:.1f}s`",
            ephemeral=True,
        )

    def _result_embed(self, reason: str) -> tuple[discord.Embed, list[int]]:
        correct_index = int(self.question["answer"])
        correct_ids = [uid for uid, data in self.answers.items() if int(data.get("answer", -1)) == correct_index]
        correct_ids.sort(key=lambda uid: float(self.answers[uid].get("elapsed", self.duration)))

        winner_lines = []
        medals = ["🥇", "🥈", "🥉"]
        for position, uid in enumerate(correct_ids[:10]):
            elapsed = float(self.answers[uid].get("elapsed", self.duration))
            winner_lines.append(f"{medals[position] if position < 3 else '•'} <@{uid}> • `{elapsed:.1f}s`")

        ended_labels = {
            "stopped": "Stopped by staff",
            "message_missing": "Recovered after the original message disappeared",
            "restart_recovery": "Recovered after restart",
        }
        ended_label = ended_labels.get(reason, "Time is up")
        result = discord.Embed(
            title=f"🏁 Quiz Results • {ended_label}",
            description=(
                f"**Question:** {self.question['question']}\n"
                f"**Correct answer:** `{chr(65 + correct_index)}` — **{self.question['choices'][correct_index]}**\n"
                f"*{self.question.get('explanation', '')}*"
            ),
            colour=SUCCESS_COLOUR if correct_ids else WARNING_COLOUR,
        )
        result.add_field(name="Fastest correct answers", value="\n".join(winner_lines) or "Nobody selected the correct answer this round.", inline=False)
        result.add_field(name="Round summary", value=f"**Entries:** {len(self.answers)}\n**Correct:** {len(correct_ids)}\n**Quiz ID:** `{self.quiz_id}`", inline=True)
        result.set_footer(text="Use /quiz leaderboard to view the server rankings")
        return result, correct_ids

    async def _publish_and_cleanup(self, result: discord.Embed, reason: str) -> bool:
        try:
            if self.message is not None:
                await self.message.edit(embed=result, view=self.view)
            else:
                self.message = await self.channel.send(embed=result, view=self.view)
        except discord.NotFound:
            # The prompt was deleted. Post one recovered result if the channel remains.
            try:
                self.message = await self.channel.send(embed=result, view=self.view)
            except (discord.Forbidden, discord.HTTPException):
                _remove_active_quiz(self.guild.id)
                active_quizzes.pop(self.guild.id, None)
                return True
        except (discord.Forbidden, discord.HTTPException) as exc:
            log_server_event(self.guild.id, f"Quiz {self.quiz_id} final result could not be published yet: {exc}")
            _save_active_quiz(self, "finalizing", reason)
            return False

        _remove_active_quiz(self.guild.id)
        active_quizzes.pop(self.guild.id, None)
        log_server_event(self.guild.id, f"Quiz {self.quiz_id} ended ({reason}).")
        return True

    async def _retry_publish(self, reason: str) -> None:
        await asyncio.sleep(15)
        result, _ = self._result_embed(reason)
        if not await self._publish_and_cleanup(result, reason):
            self._retry_task = asyncio.create_task(self._retry_publish(reason), name=f"rallybit-quiz-finalize-{self.guild.id}")

    async def finish(self, reason: str = "time") -> None:
        async with self._finish_lock:
            if self.finished:
                return
            self.finished = True
            self.view.lock()
            _save_active_quiz(self, "finalizing", reason)

            result, correct_ids = self._result_embed(reason)
            correct_index = int(self.question["answer"])
            _apply_quiz_stats_once(self, correct_index)
            _record_history(self, correct_ids)

            if not await self._publish_and_cleanup(result, reason):
                if self._retry_task is None or self._retry_task.done():
                    self._retry_task = asyncio.create_task(self._retry_publish(reason), name=f"rallybit-quiz-finalize-{self.guild.id}")


async def start_quiz(
    guild: discord.Guild,
    channel: discord.TextChannel,
    starter: discord.abc.User,
    category: str = "mixed",
    duration: int = 30,
    automatic: bool = False,
    ping_role: discord.Role | None = None,
) -> QuizSession:
    if guild.id in active_quizzes or str(guild.id) in _active_quiz_store():
        raise RuntimeError("A quiz is already active in this server.")
    if category != "mixed" and category not in CATEGORIES:
        category = "mixed"
    duration = max(15, min(120, int(duration)))
    session = QuizSession(guild, channel, _choose_question(category), duration, starter, automatic)
    active_quizzes[guild.id] = session
    try:
        content: str | None = None
        allowed_mentions = discord.AllowedMentions.none()
        if ping_role is not None and ping_role.guild.id == guild.id and not ping_role.is_default() and not ping_role.managed:
            permissions = channel.permissions_for(guild.me) if guild.me is not None else None
            can_ping_role = bool(ping_role.mentionable or (permissions and permissions.mention_everyone))
            if can_ping_role:
                content = f"{ping_role.mention} A new community quiz has started!"
                session.notified_role_id = ping_role.id
                allowed_mentions = discord.AllowedMentions(
                    everyone=False,
                    users=False,
                    roles=[ping_role],
                    replied_user=False,
                )
            else:
                log_server_event(
                    guild.id,
                    f"Quiz could not ping @{ping_role.name}; make the role mentionable or grant Rallybit Mention Everyone.",
                )
        session.message = await channel.send(
            content=content,
            embed=session.question_embed(),
            view=session.view,
            allowed_mentions=allowed_mentions,
        )
        _save_active_quiz(session)
    except Exception:
        active_quizzes.pop(guild.id, None)
        _remove_active_quiz(guild.id)
        raise

    async def countdown() -> None:
        await asyncio.sleep(max(0.0, session.end_time - time.time()))
        await session.finish("time")

    asyncio.create_task(countdown(), name=f"rallybit-quiz-{guild.id}-{session.quiz_id}")
    log_server_event(guild.id, f"Quiz {session.quiz_id} started in #{channel.name} ({category}, {duration}s).")
    return session


async def resume_active_quizzes(bot: discord.Client) -> int:
    """Restore quiz views, answers and timers from the recovery ledger."""
    data = _active_quiz_store()
    if not data:
        return 0
    pending = {
        gid: saved for gid, saved in data.items()
        if not str(gid).isdigit() or int(gid) not in active_quizzes
    }
    if not pending:
        return 0
    restored = 0
    print(f"🔄 [Resumer] Found {len(pending)} ongoing quiz session(s). Re-hydrating...")
    for gid_str, saved in list(pending.items()):
        if not isinstance(saved, dict):
            _remove_active_quiz(gid_str)
            continue
        try:
            guild = bot.get_guild(int(gid_str))
            if guild is None:
                _remove_active_quiz(gid_str)
                continue
            if guild.id in active_quizzes:
                continue
            channel = guild.get_channel(int(saved.get("channel_id", 0)))
            if not isinstance(channel, discord.TextChannel):
                _remove_active_quiz(gid_str)
                continue
            starter_id = int(saved.get("starter_id", bot.user.id if bot.user else 0))
            starter = guild.get_member(starter_id)
            if starter is None:
                starter = await bot.fetch_user(starter_id)
            question = saved.get("question")
            if not isinstance(question, dict) or not isinstance(question.get("choices"), list):
                _remove_active_quiz(gid_str)
                continue

            session = QuizSession(
                guild,
                channel,
                question,
                int(saved.get("duration", 30)),
                starter,
                bool(saved.get("automatic", False)),
                resume_data=saved,
            )
            active_quizzes[guild.id] = session

            message_id = saved.get("message_id")
            if message_id:
                try:
                    session.message = await channel.fetch_message(int(message_id))
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    session.message = None

            phase = str(saved.get("phase", "active"))
            remaining = session.end_time - time.time()
            if phase == "active" and remaining > 0 and session.message is None:
                try:
                    session.message = await channel.send(
                        content="♻️ Rallybit restored this quiz after a restart. Existing answers and the original closing time were preserved.",
                        embed=session.question_embed(),
                        view=session.view,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                    _save_active_quiz(session)
                except (discord.Forbidden, discord.HTTPException) as exc:
                    active_quizzes.pop(guild.id, None)
                    print(f"[RESUME QUIZ] Could not recreate quiz {session.quiz_id}: {exc}")
                    continue
            if phase == "active" and remaining > 0 and session.message is not None:
                bot.add_view(session.view, message_id=session.message.id)
                try:
                    await session.message.edit(view=session.view)
                except discord.HTTPException:
                    pass

                async def countdown(target: QuizSession = session) -> None:
                    await asyncio.sleep(max(0.0, target.end_time - time.time()))
                    await target.finish("time")

                asyncio.create_task(countdown(), name=f"rallybit-resume-quiz-{guild.id}-{session.quiz_id}")
            else:
                reason = str(saved.get("reason") or ("message_missing" if session.message is None else "restart_recovery"))
                asyncio.create_task(session.finish(reason), name=f"rallybit-finalize-quiz-{guild.id}-{session.quiz_id}")

            restored += 1
            print(f" ✅ Resumed quiz {session.quiz_id}: {guild.name}")
        except Exception as exc:
            active_quizzes.pop(int(gid_str), None) if str(gid_str).isdigit() else None
            print(f"[RESUME QUIZ] Could not restore {gid_str}: {exc}")

    return restored


def setup_quiz_commands(tree: app_commands.CommandTree) -> None:
    quiz = app_commands.Group(name="quiz", description="Community quizzes, schedules and rankings.")

    @quiz.command(name="start", description="Start a random community quiz in this channel.")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_messages=True)
    @app_commands.describe(
        category="mixed, general, science, geography, gaming, internet, discord or logic",
        duration="How long members have to answer (15–120 seconds).",
        notify_role="Ping the configured quiz role for this manual round",
    )
    async def quiz_start(
        interaction: discord.Interaction,
        category: str = "mixed",
        duration: app_commands.Range[int, 15, 120] = 30,
        notify_role: bool = False,
    ):
        can_run, reason, _ = bot_can_run(interaction)
        if not can_run:
            return await interaction.response.send_message(reason, ephemeral=True)
        category = category.lower().strip()
        if category != "mixed" and category not in CATEGORIES:
            return await interaction.response.send_message(f"Unknown category. Choose `mixed` or: {', '.join(CATEGORIES)}.", ephemeral=True)
        if interaction.guild.id in active_quizzes or str(interaction.guild.id) in _active_quiz_store():
            return await interaction.response.send_message("A community quiz is already active in this server.", ephemeral=True)
        if not isinstance(interaction.channel, discord.TextChannel):
            return await interaction.response.send_message("Quizzes must be started in a server text channel.", ephemeral=True)
        permissions = interaction.channel.permissions_for(interaction.guild.me)
        if not (permissions.send_messages and permissions.embed_links):
            return await interaction.response.send_message("Rallybit needs **Send Messages** and **Embed Links** in this channel.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        ping_role = None
        if notify_role:
            settings = _guild_quiz_settings(interaction.guild.id)
            try:
                ping_role = interaction.guild.get_role(int(settings.get("ping_role_id"))) if settings.get("ping_role_id") else None
            except (TypeError, ValueError):
                ping_role = None
        session = await start_quiz(
            interaction.guild,
            interaction.channel,
            interaction.user,
            category,
            duration,
            ping_role=ping_role,
        )
        await log_action_to_channel(interaction.guild, interaction.user, "quiz start", f"Started quiz `{session.quiz_id}` in {interaction.channel.mention}.", interaction.channel)
        ping_note = f" and notified {ping_role.mention}" if ping_role is not None and session.notified_role_id == ping_role.id else ""
        await interaction.followup.send(
            f"✅ Quiz `{session.quiz_id}` started in {interaction.channel.mention}{ping_note}.",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @quiz.command(name="stop", description="End the active quiz and reveal its results now.")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_messages=True)
    async def quiz_stop(interaction: discord.Interaction):
        session = active_quizzes.get(interaction.guild.id)
        if not session:
            return await interaction.response.send_message("There is no active quiz in this server.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        await session.finish("stopped")
        await interaction.followup.send("🛑 The active quiz was ended and its results were revealed.", ephemeral=True)

    @quiz.command(name="setup", description="Configure recurring automatic quizzes.")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(
        channel="Channel for automatic quizzes",
        interval_hours="Hours between quizzes",
        category="Question category",
        duration="Answer time in seconds",
        ping_role="Optional role to ping when an automatic quiz starts",
    )
    async def quiz_setup(
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        interval_hours: app_commands.Range[int, 1, 168] = 12,
        category: str = "mixed",
        duration: app_commands.Range[int, 15, 120] = 30,
        ping_role: discord.Role | None = None,
    ):
        category = category.lower().strip()
        if category != "mixed" and category not in CATEGORIES:
            return await interaction.response.send_message(f"Unknown category. Choose `mixed` or: {', '.join(CATEGORIES)}.", ephemeral=True)
        perms = channel.permissions_for(interaction.guild.me)
        if not (perms.view_channel and perms.send_messages and perms.embed_links):
            return await interaction.response.send_message("Rallybit needs View Channel, Send Messages and Embed Links in that channel.", ephemeral=True)
        if ping_role is not None:
            if ping_role.is_default():
                return await interaction.response.send_message("Choose a normal server role instead of `@everyone`.", ephemeral=True)
            if ping_role.managed:
                return await interaction.response.send_message("Discord-managed integration roles cannot be used for quiz notifications.", ephemeral=True)
            if not ping_role.mentionable and not perms.mention_everyone:
                return await interaction.response.send_message(
                    f"{ping_role.mention} is not mentionable. Make the role mentionable or grant Rallybit **Mention @everyone, @here and All Roles** in {channel.mention}.",
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
        settings = _guild_quiz_settings(interaction.guild.id)
        settings.update({
            "channel_id": channel.id,
            "interval_hours": interval_hours,
            "category": category,
            "duration_seconds": duration,
        })
        if ping_role is not None:
            settings["ping_role_id"] = ping_role.id
        _save_guild_quiz_settings(interaction.guild.id, settings)
        try:
            configured_role = interaction.guild.get_role(int(settings["ping_role_id"])) if settings.get("ping_role_id") else None
        except (TypeError, ValueError):
            configured_role = None
        embed = discord.Embed(title="🧠 Automatic quiz schedule saved", colour=BRAND_COLOUR)
        embed.add_field(name="Channel", value=channel.mention, inline=True)
        embed.add_field(name="Interval", value=f"Every {interval_hours} hour(s)", inline=True)
        embed.add_field(name="Category", value=category.title(), inline=True)
        embed.add_field(name="Answer time", value=f"{duration} seconds", inline=True)
        embed.add_field(name="Ping role", value=configured_role.mention if configured_role else "Disabled", inline=True)
        embed.add_field(name="Status", value="Enabled" if settings.get("enabled") else "Paused — use `/quiz auto enabled:true`", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @quiz.command(name="auto", description="Enable or pause recurring automatic quizzes.")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    async def quiz_auto(interaction: discord.Interaction, enabled: bool):
        settings = _guild_quiz_settings(interaction.guild.id)
        if enabled and not settings.get("channel_id"):
            return await interaction.response.send_message("Run `/quiz setup` first so Rallybit knows where to post quizzes.", ephemeral=True)
        settings["enabled"] = enabled
        if enabled:
            # Allow the first scheduled quiz to start during the next scheduler pass.
            settings["last_run"] = "2000-01-01T00:00:00+00:00"
        _save_guild_quiz_settings(interaction.guild.id, settings)
        await interaction.response.send_message(f"{'▶️ Automatic quizzes enabled.' if enabled else '⏸️ Automatic quizzes paused.'}", ephemeral=True)

    @quiz.command(name="pingrole", description="Set or clear the role pinged by automatic quizzes.")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(role="Role to ping, or leave empty to disable quiz pings")
    async def quiz_pingrole(interaction: discord.Interaction, role: discord.Role | None = None):
        settings = _guild_quiz_settings(interaction.guild.id)
        if role is None:
            settings["ping_role_id"] = None
            _save_guild_quiz_settings(interaction.guild.id, settings)
            return await interaction.response.send_message("🔕 Automatic quiz role pings disabled.", ephemeral=True)
        if role.is_default():
            return await interaction.response.send_message("Choose a normal server role instead of `@everyone`.", ephemeral=True)
        if role.managed:
            return await interaction.response.send_message("Discord-managed integration roles cannot be used for quiz notifications.", ephemeral=True)
        channel = interaction.guild.get_channel(int(settings.get("channel_id"))) if settings.get("channel_id") else None
        if not isinstance(channel, discord.TextChannel):
            return await interaction.response.send_message("Run `/quiz setup` first so Rallybit knows which channel will host automatic quizzes.", ephemeral=True)
        perms = channel.permissions_for(interaction.guild.me)
        if not role.mentionable and not perms.mention_everyone:
            return await interaction.response.send_message(
                f"{role.mention} is not mentionable. Make it mentionable or grant Rallybit **Mention @everyone, @here and All Roles** in {channel.mention}.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        settings["ping_role_id"] = role.id
        _save_guild_quiz_settings(interaction.guild.id, settings)
        await interaction.response.send_message(
            f"🔔 Automatic quizzes will now ping {role.mention} in {channel.mention}.",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @quiz.command(name="settings", description="View this server's automatic quiz configuration.")
    @app_commands.guild_only()
    async def quiz_settings(interaction: discord.Interaction):
        settings = _guild_quiz_settings(interaction.guild.id)
        channel = f"<#{settings['channel_id']}>" if settings.get("channel_id") else "Not configured"
        embed = discord.Embed(title="🧠 Quiz settings", colour=BRAND_COLOUR)
        embed.add_field(name="Automatic quizzes", value="Enabled" if settings.get("enabled") else "Paused", inline=True)
        embed.add_field(name="Channel", value=channel, inline=True)
        embed.add_field(name="Interval", value=f"Every {settings.get('interval_hours', 12)} hour(s)", inline=True)
        embed.add_field(name="Category", value=str(settings.get("category", "mixed")).title(), inline=True)
        embed.add_field(name="Answer time", value=f"{settings.get('duration_seconds', 30)} seconds", inline=True)
        ping_role = f"<@&{settings['ping_role_id']}>" if settings.get("ping_role_id") else "Disabled"
        embed.add_field(name="Ping role", value=ping_role, inline=True)
        embed.add_field(name="Available categories", value=", ".join(["mixed", *CATEGORIES]), inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @quiz.command(name="leaderboard", description="Show this server's top community quiz players.")
    @app_commands.guild_only()
    async def quiz_leaderboard(interaction: discord.Interaction):
        all_stats = load_json(QUIZ_STATS_FILE) or {}
        guild_stats = all_stats.get(str(interaction.guild.id), {})
        ranked = sorted(guild_stats.items(), key=lambda item: (int(item[1].get("points", 0)), int(item[1].get("correct", 0))), reverse=True)
        if not ranked:
            return await interaction.response.send_message("No quiz results have been recorded in this server yet.")
        lines = []
        for index, (uid, data) in enumerate(ranked[:10], 1):
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(index, f"**#{index}**")
            answered = max(1, int(data.get("answered", 0)))
            accuracy = round(int(data.get("correct", 0)) / answered * 100)
            lines.append(f"{medal} <@{uid}> • **{int(data.get('points', 0)):,} pts** • {accuracy}% accurate")
        embed = discord.Embed(title=f"🧠 {interaction.guild.name} Quiz League", description="\n".join(lines), colour=BRAND_COLOUR)
        caller = guild_stats.get(str(interaction.user.id), {})
        embed.set_footer(text=f"Your points: {int(caller.get('points', 0)):,} • Best streak: {int(caller.get('best_streak', 0))}")
        await interaction.response.send_message(embed=embed)

    tree.add_command(quiz)
