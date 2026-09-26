"""
Logging setup shared by every module.

Two sinks: the console (INFO) and data/agent.log (INFO), so a judge or a
clinician can replay exactly what the agent did. Function calls and every
Gemma call (model, prompt, options, raw output) are logged at INFO, following
the project's "log everything the model sees and says" rule.
"""

import functools
import logging

from . import config


def get_logger(name: str) -> logging.Logger:
    """Return a module logger wired to the console and the agent log file."""
    root = logging.getLogger("sahayak")
    if not root.handlers:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        for handler in (logging.StreamHandler(), logging.FileHandler(config.LOG_PATH)):
            handler.setFormatter(fmt)
            root.addHandler(handler)
        root.setLevel(logging.INFO)
    return root.getChild(name)


def logged(logger: logging.Logger):
    """Decorator that logs a function call with its (truncated) arguments."""
    def wrap(fn):
        @functools.wraps(fn)
        def inner(*args, **kwargs):
            shown = [repr(a)[:200] for a in args] + [f"{k}={v!r}"[:200] for k, v in kwargs.items()]
            logger.info("call %s(%s)", fn.__name__, ", ".join(shown))
            return fn(*args, **kwargs)
        return inner
    return wrap
