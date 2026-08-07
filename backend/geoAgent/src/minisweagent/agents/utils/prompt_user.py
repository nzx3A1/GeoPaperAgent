"""Helpers for prompting the user interactively.

The session objects are constructed lazily so that importing this module
does not require a real interactive terminal. They fall back to a plain
text stdin reader when no TTY is available, which is enough for scripted
runs.
"""

from prompt_toolkit.formatted_text.html import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.shortcuts import PromptSession

from minisweagent import global_config_dir

_history = FileHistory(global_config_dir / "interactive_history.txt")


def _has_real_tty() -> bool:
    """True only when stdin/stdout are attached to a real TTY we can drive."""
    import os as _os
    import sys as _sys
    term = _os.environ.get("TERM", "")
    # Git Bash / MSYS / Cygwin report an xterm-style TERM but don't expose a
    # Win32 console buffer, which trips up prompt_toolkit. Treat those as
    # non-tty for our purposes.
    if term.lower().startswith(("xterm", "msys", "cygwin")):
        return False
    try:
        return (
            _sys.stdin is not None
            and _sys.stdin.isatty()
            and _sys.stdout is not None
            and _sys.stdout.isatty()
        )
    except (ValueError, OSError):
        return False


def _plain_input(prompt_text: str = "") -> str:
    """Fallback that just uses input() — works even without a tty."""
    import sys as _sys
    if prompt_text:
        _sys.stdout.write(prompt_text)
        _sys.stdout.flush()
    return input()


def _read_with_prompt_session(multiline: bool) -> str:
    """Use prompt_toolkit only when we actually have a real TTY."""
    if not _has_real_tty():
        raise RuntimeError("NO_TTY")
    session = PromptSession(history=_history, multiline=multiline)
    return session.prompt("")


# Module-level handle used by InteractiveAgent. Lazily instantiated and
# reused across calls so __init__ can stay cheap.
prompt_session = None


def _ensure_prompt_session() -> PromptSession:
    """Return the shared single-line PromptSession, creating it on first use."""
    global prompt_session
    if prompt_session is None:
        if not _has_real_tty():
            raise RuntimeError("NO_TTY")
        prompt_session = PromptSession(history=_history, multiline=False)
    return prompt_session


def _multiline_prompt() -> str:
    if not _has_real_tty():
        raise RuntimeError("NO_TTY")
    return _get_multiline_session().prompt(
        "",
        bottom_toolbar=HTML(
            "提交消息：<b fg='yellow' bg='black'>Esc 然后 Enter</b> | "
            "浏览历史记录：<b fg='yellow' bg='black'>上/下方向键</b> | "
            "搜索历史记录：<b fg='yellow' bg='black'>Ctrl+R</b>"
        ),
    )


def _get_multiline_session() -> PromptSession:
    return PromptSession(history=_history, multiline=True)