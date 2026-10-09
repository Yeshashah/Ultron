"""Local command registry. Commands run before anything is sent to the AI service.

All input (typed, transcribed, or AI-generated) is treated as untrusted: commands only ever map to
a fixed set of safe actions, and consequential actions require explicit confirmation.
"""

from __future__ import annotations

import ast
import logging
import operator
import random
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from services.application_service import ApplicationError, ApplicationService, describe
from services.notes_service import NotesError, NotesService
from services.web_service import WebService, WebServiceError, normalize_url, resolve_site

logger = logging.getLogger("ultron.commands")

ASSISTANT_NAME = "Ultron"
PENDING_TIMEOUT_SECONDS = 120

EXIT_ACTION = "exit"
STOP_SPEAKING_ACTION = "stop_speaking"
CLEAR_ACTION = "clear"

JOKES = [
    "Why do programmers prefer dark mode? Because light attracts bugs.",
    "I told my computer I needed a break, and it said: no problem, I'll go to sleep.",
    "Why did the developer go broke? Because he used up all his cache.",
    "There are 10 kinds of people in the world: those who understand binary and those who don't.",
    "Why was the math book sad? It had too many problems.",
    "What do you call a fake noodle? An impasta.",
    "Why don't scientists trust atoms? Because they make up everything.",
    "A SQL query walks into a bar, walks up to two tables and asks: may I join you?",
    "Why did the scarecrow win an award? Because he was outstanding in his field.",
    "How many programmers does it take to change a light bulb? None, that's a hardware problem.",
]

FACTS = [
    "Octopuses have three hearts.",
    "A day on Venus is longer than its year.",
    "Botanically speaking, bananas are berries but strawberries are not.",
    "Sharks have existed for longer than trees.",
    "Light from the Sun takes about 8 minutes and 20 seconds to reach Earth.",
    "The Python programming language is named after Monty Python, not the snake.",
    "A group of flamingos is called a flamboyance.",
    "In 1947, engineers found an actual moth stuck in a relay of the Harvard Mark II computer, "
    "a famous early example of a computer 'bug'.",
    "Honey stored properly can last for thousands of years without spoiling.",
    "The Eiffel Tower can grow by around 15 centimetres in summer because metal expands in the heat.",
]

HELP_TEXT = (
    "Here's what I can do:\n"
    "• Basics: hello, who are you, what time is it, what's the date, tell me a joke, random fact\n"
    "• Web: open Google / YouTube / GitHub / Gmail, open example.com, search Google for <topic>\n"
    "• Apps: open Notepad, Calculator, File Explorer, Command Prompt, VS Code{custom}\n"
    "• Notes: take a note <text>, list my notes, read note 2, read my last note, delete note 2\n"
    "• Maths: calculate 12 times 8, what is 15 percent of 200, square root of 81\n"
    "• Control: stop (stop speaking), clear conversation, exit\n"
    "• Anything else — questions, coding help, study help, summaries — goes to the AI (needs an API key)."
)

YES_WORDS = {"yes", "yeah", "yep", "yup", "confirm", "sure", "do it", "go ahead", "yes please", "ok", "okay",
             "yes do it", "yes delete it", "affirmative"}
NO_WORDS = {"no", "nope", "cancel", "never mind", "nevermind", "don't", "dont", "stop", "no thanks", "abort"}

NUMBER_WORDS = {
    "one": 1, "first": 1, "two": 2, "second": 2, "to": 2, "too": 2, "three": 3, "third": 3, "four": 4,
    "for": 4, "fourth": 4, "five": 5, "fifth": 5, "six": 6, "sixth": 6, "seven": 7, "seventh": 7,
    "eight": 8, "eighth": 8, "nine": 9, "ninth": 9, "ten": 10, "tenth": 10,
}


@dataclass
class CommandResult:
    text: str
    speech: str | None = None  # None means "speak the same text"; "" means "say nothing"
    action: str | None = None
    handled: bool = True
    command: str = ""

    @property
    def spoken_text(self) -> str:
        return self.text if self.speech is None else self.speech


NOT_HANDLED = CommandResult(text="", handled=False)


@dataclass
class PendingAction:
    kind: str  # "confirm" or "input"
    callback: Callable[..., CommandResult]
    created: float = field(default_factory=time.monotonic)


Handler = Callable[[re.Match, str], "CommandResult | None"]


@dataclass
class Command:
    name: str
    patterns: list[re.Pattern]
    handler: Handler


class CommandRegistry:
    """Ordered list of regex-triggered commands. The first handler returning a result wins."""

    def __init__(self) -> None:
        self._commands: list[Command] = []

    def register(self, name: str, *patterns: str) -> Callable[[Handler], Handler]:
        compiled = [re.compile(p, re.IGNORECASE) for p in patterns]

        def decorator(handler: Handler) -> Handler:
            self._commands.append(Command(name, compiled, handler))
            return handler

        return decorator

    def add(self, name: str, patterns: list[str], handler: Handler) -> None:
        self.register(name, *patterns)(handler)

    @property
    def names(self) -> list[str]:
        return [c.name for c in self._commands]

    def dispatch(self, text: str) -> CommandResult | None:
        for command in self._commands:
            for pattern in command.patterns:
                match = pattern.search(text)
                if not match:
                    continue
                result = command.handler(match, text)
                if result is not None:
                    result.command = result.command or command.name
                    return result
        return None


_WAKE = re.compile(r"^(hey |ok |okay |hi )?ultron[\s,.:!-]*", re.IGNORECASE)
_POLITE_PREFIX = re.compile(r"^(please |kindly |can you |could you |would you |will you )+", re.IGNORECASE)
_POLITE_SUFFIX = re.compile(r"[\s,]+(please|for me|ultron)$", re.IGNORECASE)


def normalize(text: str) -> str:
    text = " ".join((text or "").split())
    text = text.strip(" \t.?!")
    stripped = _WAKE.sub("", text)
    text = stripped or text
    for _ in range(2):
        text = _POLITE_PREFIX.sub("", text).strip()
        text = _POLITE_SUFFIX.sub("", text).strip(" .?!,")
    return text


def parse_number(token: str) -> int | None:
    token = token.strip().lower().lstrip("#")
    if token.isdigit():
        return int(token)
    match = re.fullmatch(r"(\d+)(st|nd|rd|th)", token)
    if match:
        return int(match.group(1))
    return NUMBER_WORDS.get(token)


# ---------------------------------------------------------------- safe arithmetic

_BIN_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow,
}
_UNARY_OPS = {ast.USub: operator.neg, ast.UAdd: operator.pos}
_ARITHMETIC_CHARS = re.compile(r"^[\d\s.+\-*/()%]+$")


def _eval_node(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _UNARY_OPS[type(node.op)](_eval_node(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        left, right = _eval_node(node.left), _eval_node(node.right)
        if isinstance(node.op, ast.Pow) and (abs(right) > 100 or abs(left) > 1e6):
            raise ValueError("number too large")
        return _BIN_OPS[type(node.op)](left, right)
    raise ValueError("unsupported expression")


def spoken_math_to_expression(text: str) -> str | None:
    """Convert e.g. 'twelve times 8'-style input (digits only) into a Python arithmetic expression."""
    expr = text.lower().strip(" ?=")
    expr = re.sub(r"(square root|sqrt) of\s*([\d.]+)", r"(\2)**0.5", expr)
    expr = re.sub(r"([\d.]+)\s*(percent|%) of", r"(\1/100)*", expr)
    replacements = [
        (r"\bplus\b", "+"), (r"\bminus\b", "-"), (r"\b(times|multiplied by|x|into)\b", "*"),
        (r"\b(divided by|over)\b", "/"), (r"\bto the power of\b", "**"), (r"\bsquared\b", "**2"),
        (r"\bcubed\b", "**3"), (r"\bmod(ulo)?\b", "%"), (r"×", "*"), (r"÷", "/"), (r"\^", "**"),
    ]
    for pattern, repl in replacements:
        expr = re.sub(pattern, repl, expr)
    expr = expr.replace(",", "")
    if not _ARITHMETIC_CHARS.match(expr) or not re.search(r"\d", expr) or not re.search(r"[+\-*/%]", expr):
        return None
    return expr


def evaluate_expression(expr: str) -> float:
    if len(expr) > 200:
        raise ValueError("expression too long")
    return _eval_node(ast.parse(expr, mode="eval"))


def format_number(value: float) -> str:
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        value = int(value)
    if isinstance(value, float):
        return f"{value:.10g}"
    return f"{value:,}" if abs(value) >= 10_000 else str(value)


# ---------------------------------------------------------------- handler


class CommandHandler:
    def __init__(
        self,
        notes: NotesService,
        web: WebService,
        apps: ApplicationService,
        clock: Callable[[], datetime] = datetime.now,
        rng: random.Random | None = None,
    ) -> None:
        self.notes = notes
        self.web = web
        self.apps = apps
        self.clock = clock
        self.rng = rng or random.Random()
        self._pending: PendingAction | None = None
        self.registry = CommandRegistry()
        self._register_defaults()

    # -- public API ---------------------------------------------------------

    @property
    def has_pending(self) -> bool:
        return self._pending is not None

    def cancel_pending(self) -> None:
        self._pending = None

    def handle(self, raw_text: str) -> CommandResult:
        """Return a local result, or ``NOT_HANDLED`` (``handled=False``) to forward to the AI."""
        text = normalize(raw_text)
        if not text:
            return CommandResult("I didn't catch anything. Please try again.", command="empty")

        pending_result = self._resolve_pending(text, raw_text)
        if pending_result is not None:
            return pending_result

        try:
            result = self.registry.dispatch(text)
        except Exception:
            logger.exception("Command handler failed")
            return CommandResult("Sorry, something went wrong while running that command.", command="error")
        if result is None:
            return CommandResult(text="", handled=False)
        logger.info("Handled local command: %s", result.command)
        return result

    # -- pending confirmations / follow-ups --------------------------------------

    def _resolve_pending(self, text: str, raw_text: str) -> CommandResult | None:
        pending = self._pending
        if pending is None:
            return None
        self._pending = None
        if time.monotonic() - pending.created > PENDING_TIMEOUT_SECONDS:
            return None
        answer = text.lower().strip(" .!")
        if pending.kind == "confirm":
            if answer in YES_WORDS:
                return pending.callback()
            if answer in NO_WORDS:
                return CommandResult("Okay, cancelled. Nothing was changed.", command="cancel")
            return None  # anything else cancels silently and is handled as a new request
        if answer in NO_WORDS:
            return CommandResult("Okay, I won't save a note.", command="cancel")
        return pending.callback(raw_text)

    def _confirm(self, question: str, on_yes: Callable[[], CommandResult]) -> CommandResult:
        self._pending = PendingAction("confirm", on_yes)
        return CommandResult(f"{question} Say 'yes' to confirm or 'no' to cancel.")

    # -- registration ----------------------------------------------------------

    def _register_defaults(self) -> None:
        r = self.registry

        r.add("stop_speaking", [r"^(stop|stop (speaking|talking)|be quiet|quiet|silence|shut up|enough|hush)$"],
              lambda m, t: CommandResult("Okay, I've stopped.", speech="", action=STOP_SPEAKING_ACTION))
        r.add("exit", [r"^(exit|quit|goodbye|good bye|bye|bye bye|close|shut ?down|turn off)( ultron)?$",
                       r"^(exit|quit|close) (the )?(app|application|assistant|program)$"],
              lambda m, t: CommandResult("Goodbye! Ultron signing off.", action=EXIT_ACTION))
        r.add("clear", [r"^(clear|reset|wipe)( the| my)? (conversation|chat|history|screen)$"],
              lambda m, t: CommandResult("Conversation cleared.", speech="", action=CLEAR_ACTION))
        r.add("help", [r"^(help|commands|show commands|list commands|what can you do|what do you do|"
                       r"how do i use you|what are your (commands|features|capabilities))$"], self._help)
        r.add("identity", [r"^(who are you|what('s| is) your name|what are you|introduce yourself|your name|"
                           r"tell me about yourself|are you (an? )?(ai|robot|bot|assistant))$"], self._identity)
        r.add("greeting", [r"^(hi|hello|hey|hiya|howdy|greetings|yo|good (morning|afternoon|evening|day))"
                           r"( there)?$"], self._greeting)
        r.add("how_are_you", [r"^(how are you( doing)?( today)?|how's it going|how is it going|how do you do|"
                              r"how have you been|what's up|whats up|sup)$"],
              lambda m, t: CommandResult("I'm running smoothly and ready to help. How can I help you?"))
        r.add("thanks", [r"^(thanks|thank you|thank you so much|thanks a lot|cheers)$"],
              lambda m, t: CommandResult("You're welcome!"))
        r.add("time", [r"^(what('s| is) the (current )?time( now)?|what time is it( now)?|(tell me )?the time|"
                       r"current time|time( now)?|what's the time right now)$"], self._time)
        r.add("date", [r"^(what('s| is) (the |today's )?date( today)?|what day is (it|today)|today's date|"
                       r"(tell me )?the date|date|what is today|current date|what's today)$"], self._date)
        r.add("joke", [r"^(tell|say|give)( me)? (a |another |one more )?(funny )?joke$", r"^(joke|make me laugh)$"],
              lambda m, t: CommandResult(self.rng.choice(JOKES)))
        r.add("fact", [r"^(tell|give|say)( me)? (a |another )?(random |fun |interesting )?fact$",
                       r"^(random|fun|interesting) fact$"],
              lambda m, t: CommandResult("Here's a fact: " + self.rng.choice(FACTS)))

        # Notes (registered before generic "open"/"search" patterns).
        r.add("list_notes", [r"^(list|show|display|view|what are|open)( all)?( of)?( my)?( the)? notes$",
                             r"^(my notes|notes)$"], self._list_notes)
        r.add("read_note", [r"^read( me)?( my)?( the)? (?P<which>last|latest|recent|newest|most recent) note$",
                            r"^read( me)?( my)?( the)? note (number )?(?P<num>\S+)$",
                            r"^read( me)?( my)?( the)? (?P<num>\S+) note$"], self._read_note)
        r.add("read_all_notes", [r"^read( me)?( all)?( of)?( my)?( the)? notes$"], self._read_all_notes)
        r.add("delete_note", [r"^(delete|remove|erase)( my)?( the)? note (number )?(?P<num>\S+)$",
                              r"^(delete|remove|erase)( my)?( the)? (?P<which>last|latest) note$"],
              self._delete_note)
        r.add("create_note", [
            r"^(take|make|create|add|write|save|new)( a| an)?( new| quick)? note(s)?"
            r"(?:\s+(?:that|saying|says|to|about|of))?\s*[:,\-]?\s+(?P<content>.+)$",
            r"^note( that|:|,| down)\s*(?P<content>.+)$",
            r"^remember( that)?\s+(?P<content>.+)$",
            r"^(take|make|create|add|write|new)( a| an)?( new| quick)? note$",
        ], self._create_note)

        r.add("calculate", [r"^(calculate|compute|evaluate|solve|what('s| is)|how much is)\s+(?P<expr>.+)$",
                            r"^(?P<expr>(square root|sqrt) of\s*[\d.]+)$",
                            r"^(?P<expr>[\d\s.+\-*/()%^x×÷]+)$"], self._calculate)
        r.add("web_lookup", [r"^(look up|lookup|web search|search the web for|search the internet for)"
                             r"\s+(?P<query>.+)$"], self._web_lookup)
        r.add("google_search", [r"^search (for )?(?P<query>.+) on google$",
                                r"^(search on google|search google|google search|search|google)"
                                r"(\s+(for|about))?\s+(?P<query>.+)$"], self._google_search)
        r.add("open", [r"^(open|launch|go to|visit|start up)( the| my)?\s+(?P<target>.+?)"
                       r"( app| application| website| site| web ?site| in (the )?browser)?$"], self._open)

    # -- handlers ----------------------------------------------------------------

    def _help(self, m: re.Match, t: str) -> CommandResult:
        custom = [a for a in self.apps.custom_apps]
        extra = f", and your configured apps: {describe(custom)}" if custom else ""
        return CommandResult(
            HELP_TEXT.format(custom=extra),
            speech="I can tell the time and date, open websites and apps, take and read notes, do quick maths, "
                   "tell jokes, and answer questions with AI. The full list is on screen.",
        )

    def _identity(self, m: re.Match, t: str) -> CommandResult:
        return CommandResult(
            f"I'm {ASSISTANT_NAME}, your personal AI voice assistant. I can answer questions, open approved "
            "apps and websites, and manage your notes."
        )

    def _greeting(self, m: re.Match, t: str) -> CommandResult:
        hour = self.clock().hour
        part = "morning" if hour < 12 else "afternoon" if hour < 17 else "evening"
        return CommandResult(f"Good {part}! I'm {ASSISTANT_NAME}. How can I help you?")

    def _time(self, m: re.Match, t: str) -> CommandResult:
        return CommandResult(f"It's {self.clock().strftime('%I:%M %p').lstrip('0')}.")

    def _date(self, m: re.Match, t: str) -> CommandResult:
        now = self.clock()
        return CommandResult(f"Today is {now.strftime('%A')}, {now.day} {now.strftime('%B %Y')}.")

    def _calculate(self, m: re.Match, t: str) -> CommandResult | None:
        expr = spoken_math_to_expression(m.group("expr"))
        if expr is None:
            return None  # not arithmetic, e.g. "what is the capital of France"
        try:
            value = evaluate_expression(expr)
        except ZeroDivisionError:
            return CommandResult("That's a division by zero, which is undefined.")
        except (ValueError, SyntaxError, OverflowError, TypeError):
            return CommandResult("I couldn't work that out. Try something like 'calculate 12 times 8'.")
        return CommandResult(f"The answer is {format_number(value)}.")

    def _google_search(self, m: re.Match, t: str) -> CommandResult:
        query = m.group("query").strip()
        try:
            self.web.search_google(query)
        except WebServiceError as exc:
            return CommandResult(str(exc))
        return CommandResult(f"Searching Google for \"{query}\".", speech=f"Searching Google for {query}.")

    def _web_lookup(self, m: re.Match, t: str) -> CommandResult:
        query = m.group("query").strip()
        if not self.web.lookup_available:
            try:
                self.web.search_google(query)
            except WebServiceError as exc:
                return CommandResult(str(exc))
            return CommandResult(f"Web lookup isn't configured, so I opened Google results for \"{query}\".")
        try:
            answer = self.web.lookup(query)
        except WebServiceError as exc:
            return CommandResult(str(exc))
        if not answer:
            return CommandResult(f"I couldn't find a quick answer for \"{query}\". Try 'search Google for {query}'.")
        return CommandResult(f"{answer}\n(Source: DuckDuckGo)", speech=answer)

    def _open(self, m: re.Match, t: str) -> CommandResult:
        target = m.group("target").strip(" .")
        site = resolve_site(target)
        if site:
            return self._open_url(site, target.title())
        if self.apps.canonical_name(target):
            try:
                name = self.apps.open(target)
            except ApplicationError as exc:
                return CommandResult(str(exc))
            label = "VS Code" if name == "vs code" else name.title()
            return CommandResult(f"Opening {label}.")
        url = normalize_url(target)
        if url:
            return self._open_url(url, target)
        return CommandResult(
            f"I can't open \"{target}\". I can open these apps: {describe(self.apps.supported_apps())}; "
            "these websites: Google, YouTube, GitHub, Gmail; or any valid web address like example.com.",
            speech=f"Sorry, I can't open {target}. Say 'help' to see what I can open.",
        )

    def _open_url(self, url: str, label: str) -> CommandResult:
        try:
            self.web.open_url(url)
        except WebServiceError as exc:
            return CommandResult(str(exc))
        return CommandResult(f"Opening {label}.")

    # -- notes ---------------------------------------------------------------

    def _save_note(self, content: str) -> CommandResult:
        try:
            note = self.notes.add(content)
        except NotesError as exc:
            return CommandResult(str(exc))
        return CommandResult(f"Note saved: \"{note.text}\"", speech="Got it. I've saved your note.", command="create_note")

    def _create_note(self, m: re.Match, t: str) -> CommandResult:
        content = (m.groupdict().get("content") or "").strip()
        if content:
            return self._save_note(content)
        self._pending = PendingAction("input", self._save_note)
        return CommandResult("Sure. What should the note say?")

    def _list_notes(self, m: re.Match, t: str) -> CommandResult:
        try:
            notes = self.notes.list()
        except NotesError as exc:
            return CommandResult(str(exc))
        if not notes:
            return CommandResult("You don't have any notes yet. Say 'take a note' followed by what to remember.")
        shown = notes[-20:]
        offset = len(notes) - len(shown)
        lines = [
            f"{i}. [{n.created_display}] {n.text if len(n.text) <= 80 else n.text[:77] + '...'}"
            for i, n in enumerate(shown, start=offset + 1)
        ]
        header = f"You have {len(notes)} note{'s' if len(notes) != 1 else ''}:"
        return CommandResult(
            header + "\n" + "\n".join(lines),
            speech=f"{header.rstrip(':')}. Say 'read note' and a number to hear one.",
        )

    def _resolve_note_position(self, m: re.Match) -> tuple[int | None, str | None]:
        groups = m.groupdict()
        if groups.get("which"):
            try:
                count = len(self.notes.list())
            except NotesError as exc:
                return None, str(exc)
            return (count or None), (None if count else "You don't have any notes yet.")
        number = parse_number(groups.get("num") or "")
        if number is None:
            return None, "Which note? Say something like 'read note 2'."
        return number, None

    def _read_note(self, m: re.Match, t: str) -> CommandResult:
        position, error = self._resolve_note_position(m)
        if error:
            return CommandResult(error)
        try:
            note = self.notes.get(position)
        except NotesError as exc:
            return CommandResult(str(exc))
        if not note:
            return CommandResult(f"I couldn't find note {position}. Say 'list my notes' to see them.")
        return CommandResult(
            f"Note {position} ({note.created_display}):\n{note.text}",
            speech=f"Note {position}, saved {note.created_display}: {note.text}",
        )

    def _read_all_notes(self, m: re.Match, t: str) -> CommandResult:
        try:
            notes = self.notes.list()
        except NotesError as exc:
            return CommandResult(str(exc))
        if not notes:
            return CommandResult("You don't have any notes yet.")
        recent = notes[-5:]
        start = len(notes) - len(recent) + 1
        body = "\n".join(f"{i}. {n.text}" for i, n in enumerate(recent, start=start))
        spoken = " ".join(f"Note {i}: {n.text}." for i, n in enumerate(recent, start=start))
        prefix = f"Your {len(recent)} most recent notes:" if len(notes) > 5 else "Your notes:"
        return CommandResult(f"{prefix}\n{body}", speech=f"{prefix} {spoken}")

    def _delete_note(self, m: re.Match, t: str) -> CommandResult:
        position, error = self._resolve_note_position(m)
        if error:
            return CommandResult(error)
        try:
            note = self.notes.get(position)
        except NotesError as exc:
            return CommandResult(str(exc))
        if not note:
            return CommandResult(f"I couldn't find note {position}. Say 'list my notes' to see them.")
        preview = note.text if len(note.text) <= 60 else note.text[:57] + "..."

        def do_delete() -> CommandResult:
            try:
                deleted = self.notes.delete(note.id)
            except NotesError as exc:
                return CommandResult(str(exc))
            return CommandResult("Note deleted." if deleted else "That note was already gone.", command="delete_note")

        return self._confirm(f"Delete note {position}: \"{preview}\"? This can't be undone.", do_delete)
