"""What to tell the person when something inside Sthanik fails in a way nobody planned for.

An unhandled error used to reach the page as a bare "500", and the plan said "Could not create the file (500)". One cause was real and
common: the program's own folder was replaced by an update while it was running, and the next library it needed (the PowerPoint one, say)
was read from the new file at the old file's positions: "zlib.error: unknown compression method". The fix for that is to quit and open
Sthanik again, so that is what the message says.
"""

from __future__ import annotations

import errno
import zlib

CHANGED = "Sthanik's program files changed while it was running (an update?). Quit Sthanik and open it again."


def explain(exc: BaseException) -> str:
    """One sentence for the page: what happened and what to do about it. Never a traceback or a path."""
    chain = [exc]
    while chain[-1].__cause__ or chain[-1].__context__:
        nxt = chain[-1].__cause__ or chain[-1].__context__
        if nxt in chain:
            break
        chain.append(nxt)
    for e in chain:
        if isinstance(e, zlib.error) or (isinstance(e, (ImportError, ModuleNotFoundError)) and "No module named" in str(e)):
            return CHANGED
        if isinstance(e, OSError) and e.errno == errno.ENOSPC:
            return "The disk is full, so the file could not be written. Free some space and try again."
        if isinstance(e, PermissionError):
            return "Sthanik is not allowed to write there. Check the permissions of its data folder."
    return f"Something went wrong inside Sthanik ({type(exc).__name__}). If it happens again, quit Sthanik and open it again."
