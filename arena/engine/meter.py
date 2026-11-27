"""Deterministic compute metering for bot code (Battlecode-style "bytecode" limits).

How it works
------------
* Every bytecode instruction executed in *bot* code costs 1 unit. So does every
  instruction in the pure-Python parts of the whitelisted stdlib modules
  (``collections.Counter``, ``random.randint``, ``copy.deepcopy`` ...), so a bot can't
  hide work inside library code.
* Each ``RobotController`` call additionally costs a fixed amount (see API_COST).
* Implemented with PEP 669 ``sys.monitoring`` INSTRUCTION events, enabled *locally*
  on bot code objects only. Engine code is not instrumented and runs at full speed.
* Once the budget is exhausted, *every* further instruction raises ``BudgetExceeded``.
  This makes the limit unescapable: an ``except:`` / ``finally:`` handler cannot keep
  running, because its very first instruction raises again.
* Work inside C functions (``sorted`` of a huge list, big-int arithmetic) is invisible
  to the instruction counter, so a CPU-time watchdog (``ITIMER_PROF``) backs it up.
  When it fires, the budget becomes exhausted and the turn ends at the next
  instruction. This is the only non-deterministic part and only affects abusive bots.

Counts are deterministic for a given CPython version (bytecode differs between versions,
so pin the Python version for official tournaments).
"""

from __future__ import annotations

import signal
import sys
import threading
import types

INF = 1 << 62

OK = "ok"
BUDGET = "budget"
TIMEOUT = "timeout"
ERROR = "error"


class BudgetExceeded(BaseException):
    """Raised inside bot code when the turn's instruction budget is used up.

    Derives from BaseException and is not reachable from bot code, so ``except Exception``
    does not catch it (and even a bare ``except:`` can't continue, see module docstring).
    """


class StopTurn(BaseException):
    """Raised by the engine to end a turn early without it counting as an error
    (e.g. the squad running this turn merged into a bigger squad)."""


class Meter:
    def __init__(self) -> None:
        mon = sys.monitoring
        for tool in (4, 3, 1, 0, 2, 5):
            if mon.get_tool(tool) is None:
                break
        else:  # pragma: no cover
            raise RuntimeError("no free sys.monitoring tool id")
        mon.use_tool_id(tool, "arena-meter")
        self._tool = tool
        state = [0, INF]  # [used, limit]
        self._state = state

        def on_instruction(code, offset, _s=state, _exc=BudgetExceeded):
            _s[0] += 1
            if _s[0] > _s[1]:
                raise _exc

        mon.register_callback(tool, mon.events.INSTRUCTION, on_instruction)
        self._instrumented: dict = {}
        self.timed_out = False
        self._grace = 0.05
        self._timer_ok = hasattr(signal, "setitimer")
        if self._timer_ok:
            try:
                signal.signal(signal.SIGPROF, self._on_alarm)
            except ValueError:  # not in main thread
                self._timer_ok = False

    # ------------------------------------------------------------------ instrumentation

    def instrument_code(self, code: types.CodeType) -> None:
        """Count instructions in ``code`` and all code objects nested in it."""
        mon = sys.monitoring
        stack = [code]
        while stack:
            c = stack.pop()
            # Code objects compare by *value*: two teams loading identical source get
            # equal but distinct code objects, and events are set per object. Track identity.
            if id(c) in self._instrumented:
                continue
            self._instrumented[id(c)] = c  # keep it alive so the id is never reused
            mon.set_local_events(self._tool, c, mon.events.INSTRUCTION)
            stack.extend(k for k in c.co_consts if isinstance(k, types.CodeType))

    def instrument_module(self, module: types.ModuleType) -> None:
        """Count instructions in every pure-Python function/method defined in ``module``."""
        name = module.__name__
        seen: set = set()

        def visit(obj):
            if id(obj) in seen:
                return
            seen.add(id(obj))
            if isinstance(obj, types.FunctionType):
                if obj.__module__ == name:
                    self.instrument_code(obj.__code__)
            elif isinstance(obj, (classmethod, staticmethod)):
                visit(obj.__func__)
            elif isinstance(obj, property):
                for f in (obj.fget, obj.fset, obj.fdel):
                    if f is not None:
                        visit(f)
            elif isinstance(obj, type) and getattr(obj, "__module__", None) == name:
                for v in list(vars(obj).values()):
                    visit(v)

        for v in list(vars(module).values()):
            visit(v)

    # ------------------------------------------------------------------ accounting

    def charge(self, n: int) -> None:
        """Charge a fixed cost (used by RobotController methods before they act)."""
        s = self._state
        s[0] += n
        if s[0] > s[1]:
            raise BudgetExceeded

    @property
    def used(self) -> int:
        return self._state[0]

    @property
    def limit(self) -> int:
        return self._state[1]

    @property
    def remaining(self) -> int:
        s = self._state
        return max(0, s[1] - s[0])

    def _on_alarm(self, signum, frame) -> None:
        if not self.timed_out:
            # Stage 1: don't raise here (we might be inside engine code). Exhaust the
            # budget instead: the next bot instruction raises BudgetExceeded.
            self.timed_out = True
            self._state[1] = -1
            signal.setitimer(signal.ITIMER_PROF, self._grace)
        else:
            # Stage 2: still running a whole grace period later, so stage 1 could not
            # take effect. Interrupt directly as a last resort.
            raise BudgetExceeded

    def run(self, fn, args, budget: int, time_limit: float | None):
        """Run ``fn(*args)`` under ``budget`` instructions and ``time_limit`` CPU seconds.

        Returns ``(status, used, exception_or_None)`` where status is one of
        OK / BUDGET / TIMEOUT / ERROR.
        """
        s = self._state
        s[0] = 0
        s[1] = budget
        self.timed_out = False
        self._grace = max(0.05, time_limit or 0)
        timer = bool(self._timer_ok and time_limit and threading.current_thread() is threading.main_thread())
        if timer:
            signal.setitimer(signal.ITIMER_PROF, time_limit)
        exc = None
        try:
            fn(*args)
            status = OK
        except BudgetExceeded:
            status = BUDGET
        except StopTurn:
            status = OK
        except KeyboardInterrupt:
            raise
        except BaseException as e:  # noqa: BLE001 - anything the bot raised
            status = ERROR
            exc = e
        finally:
            if timer:
                signal.setitimer(signal.ITIMER_PROF, 0)
            used = s[0]
            s[0] = 0
            s[1] = INF
        if self.timed_out:
            status = TIMEOUT
        elif status == ERROR and used > budget:
            status = BUDGET  # an exception raised while unwinding from BudgetExceeded
        return status, min(used, budget), exc


METER = Meter()
