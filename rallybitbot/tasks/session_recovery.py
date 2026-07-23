from __future__ import annotations

from discord.ext import tasks

_bot = None


@tasks.loop(minutes=1)
async def session_recovery_loop() -> None:
    """Retry any recovery ledger that is not represented in memory.

    Startup recovery normally handles everything immediately. This watchdog
    covers temporary Discord/API permission errors without requiring another
    manual service restart.
    """
    if _bot is None or _bot.is_closed():
        return

    try:
        from commands.activity import resume_active_checks
        await resume_active_checks(_bot)
    except Exception as exc:
        print(f"[SESSION WATCHDOG] Activity recovery retry failed: {exc}")

    try:
        from commands.quizzes import resume_active_quizzes
        await resume_active_quizzes(_bot)
    except Exception as exc:
        print(f"[SESSION WATCHDOG] Quiz recovery retry failed: {exc}")

    try:
        from commands.community import resume_active_pulses
        await resume_active_pulses(_bot)
    except Exception as exc:
        print(f"[SESSION WATCHDOG] Pulse recovery retry failed: {exc}")


@session_recovery_loop.before_loop
async def before_session_recovery() -> None:
    if _bot is not None:
        await _bot.wait_until_ready()


def setup_session_recovery(bot) -> None:
    global _bot
    _bot = bot
    if not session_recovery_loop.is_running():
        session_recovery_loop.start()
