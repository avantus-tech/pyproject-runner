r"""Parse an environment file or string.

Load environment variables from a file or string, expanding variables as
needed. The syntax is similar to bash syntax, but simplified and relaxed.

Each variable assignment must start on a new line and include a variable
name followed by an equal (=) and then an optional value. White space
before and after the name and equal and at the end of the line are
ignored. Values may be optionally quoted to preserve leading and trailing
spaces. Variables may be unset by excluding the value.

Values may include other variables using bash-like variable substitution:
$name or ${name}. Unless escaped, variable expansion will occur in
unquoted, double-quoted ("), and triple double-quoted values (\"""). Any
character, including quotes and newlines, may be escaped using a
backslash (\\).

Like bash, variable substitution in single-quoted (') and triple single-
quoted (''') values are not expanded and backslash escapes are ignored.

Line comments begin at an unquoted and unescaped hash/pound (#) at the
beginning of a line or after white space and continue to the end of the
line. Comments are discarded by the parser.

Syntax:
    expression        ::=  (assignment | comment | ws*) "\n"
    assignment        ::=  ws* name ws* "=" ws* value (ws+ comment)?
    name              ::=  (letter | "_") (letter | digit | "_")*
    comment           ::=  "#" not-newline*
    value             ::=  (double-quoted | single-quoted | unquoted)*
    double-quoted     ::=  double-quote (not-double-quote | escaped)* double-quote
    single-quoted     ::=  single-quote not-single-quote* single-quote
    unquoted          ::=  (not-quote | escaped)+
    escaped           ::=  "\" any-character
    not-newline       ::=  any-character - "\n"
    not-double-quote  ::=  any-character - double-quote
    not-single-quote  ::=  any-character - single-quote
    not-quote         ::=  any-character - (double-quote | single-quote | "\n" | "\" | "#")
    letter            ::=  "A"..."Z" | "a"..."z"
    digit             ::=  "0"..."9"
    ws                ::=  " " | "\t" | "\r" | "\f" | "\v"
    double-quote      ::=  '"' | '\"""'
    single-quote      ::=  "'" | "'''"
    any-character     ::=  ? Any Printable Character ?
"""

from __future__ import annotations

import collections
from collections.abc import Iterator, Mapping
import dataclasses
import os
import re
from typing import TYPE_CHECKING, Any, Final, Literal, cast

if TYPE_CHECKING:
    from typing_extensions import Self

__all__ = "evaluate", "expand"


_UPPERCASE_ENV: Final = os.name == "nt"


TokenType = Literal["ASSIGN", "COMMENT", "DQUOTE", "ESCAPE",
                    "NEWLINE", "SQUOTE", "TEXT", "WS"]


WS: Final = r"[ \t\r\f\v]"  # White space minus newline
TOKENS: Final[tuple[tuple[TokenType, str], ...]] = (
    ("ESCAPE", r"\\(?s:.)"),
    ("ASSIGN", "="),
    ("COMMENT", "#"),
    ("DQUOTE", '"""|"'),
    ("NEWLINE", "\n"),
    ("SQUOTE", "'''|'"),
    ("WS", r"[ \t\r\f\v]+"),  # Whitespace minus newline
)
SPLIT_RE: Final = re.compile("|".join(f"(?P<{kind}>{pattern})"
                                      for kind, pattern in TOKENS),
                             re.MULTILINE)
EXPAND_RE: Final = re.compile(r"\$(\{)?(?P<name>(?ai:[a-z_][a-z0-9_]*))(?(1)})", re.MULTILINE)


class Fragment(str):
    """String fragment of variable assignment value."""

    __slots__ = "expandable", "whitespace"

    expandable: bool
    whitespace: bool

    def __new__(cls, value: Any, *, expandable: bool = False, whitespace: bool = False) -> Self:
        obj = super().__new__(cls, value)
        obj.expandable = expandable
        obj.whitespace = whitespace
        return obj

    def expand(self, env: Mapping[str, str | None]) -> str:
        """Expand variable substitutions in string fragment.

        Expands occurrences of substitutions of the form $name and ${name}
        to values from env, or to the empty string if not found. Only
        objects with a truthy .expandable attribute are expanded.
        """
        def repl(match: re.Match[str]) -> str:
            name = match.group("name")
            if _UPPERCASE_ENV:
                name = name.upper()
            return env.get(name) or ""

        if self.expandable:
            return EXPAND_RE.sub(repl, self)
        return str(self)


@dataclasses.dataclass(slots=True)
class Token:
    """Represents a token in the parsed string."""

    type: TokenType
    value: str
    _: dataclasses.KW_ONLY
    line: int
    column: int


def tokenize(text: str) -> Iterator[Token]:
    """Iterate tokens in a string."""
    kind: TokenType
    line = 1
    # Because the text can be a string with embedded newlines, line numbers
    # must be tracked based on character position in the string.
    line_start = 0
    previous_end = 0

    for match in SPLIT_RE.finditer(text):
        kind = cast(TokenType, match.lastgroup)
        value = match.group(0)
        start = match.start()
        column = start - line_start
        if start > previous_end:
            # Yield any text between matches
            yield Token("TEXT", text[previous_end:start],
                        line=line, column=previous_end - line_start)
        previous_end = match.end()
        yield Token(kind, value, line=line, column=column)
        if kind == "NEWLINE":
            line += 1
            line_start = start

    if previous_end < len(text):
        # Yield any text after the last match
        yield Token("TEXT", text[previous_end:],
                    line=line, column=previous_end - line_start)


def parse(text: str) -> Iterator[tuple[str, list[Fragment]]]:
    """Iterate variable assignments in a string.

    Yields 2-tuples of variable name and value fragments.
    """
    tokens = tokenize(text)

    def assignment(token: Token) -> list[Fragment]:
        # Token is passed in just in case tokens is exhausted
        for token in tokens:  # noqa: PLR1704
            match token:
                case Token("WS"):
                    pass  # Discard
                case Token("ASSIGN"):
                    return assignment_value()
                case _:
                    break
        raise syntax_error("Expected '=' after variable name", token)

    def assignment_value() -> list[Fragment]:
        """Parse the right-hand side of a variable assignment."""
        fragments: list[Fragment] = []
        comment = False
        for token in tokens:
            match token:
                case Token("NEWLINE"):
                    break  # discard
                case _ if comment:
                    # Cannot escape a newline in a comment
                    if token.type == "ESCAPE" and token.value[-1] == "\n":
                        break
                case Token("COMMENT", value):
                    if fragments and fragments[-1].whitespace:
                        comment = True
                    else:
                        fragments.append(Fragment(value))
                case Token("WS", value):
                    fragments.append(Fragment(value, whitespace=True))
                case Token("NAME" | "ASSIGN", value):
                    # Treat assignments within assignments as un-expandable text
                    fragments.append(Fragment(value))
                case Token("TEXT", value):
                    fragments.append(Fragment(value, expandable=True))
                case Token("SQUOTE" | "DQUOTE"):
                    fragments += quoted(token)
                case Token("ESCAPE", value):
                    fragments.append(Fragment(value[1:]))
                case _:  # pragma: no cover
                    raise NotImplementedError(token)
        # Remove leading and trailing whitespace
        while fragments and fragments[0].whitespace:
            fragments.pop(0)
        while fragments and fragments[-1].whitespace:
            fragments.pop()
        return fragments

    def quoted(quote: Token) -> Iterator[Fragment]:
        """Parse a quoted fragment.

        Matches quotation marks while handling character escaping.
        """
        empty = True
        for token in tokens:
            match token:
                case Token("DQUOTE" | "SQUOTE" as kind, value) if (
                        kind == quote.type and value == quote.value):
                    if empty:
                        yield Fragment("")  # Handle empty string
                    return  # End quoted string
                case Token("ESCAPE", value) if quote.type == "SQUOTE":
                    if value[1:] == quote.value:
                        # Escaped single-quote (\') in single-quoted string should not be escaped.
                        # It instead ends the string with the backslash as the last character.
                        yield Fragment(value[:1])
                        return
                    # Unescaped string when in single-quotes
                    yield Fragment(value)
                case Token("ESCAPE", value):
                    # Character after escape in double-quoted strings
                    yield Fragment(value[1:])
                case Token(_, value):
                    yield Fragment(value, expandable=quote.type == "DQUOTE")
                case _:  # pragma: no cover
                    raise NotImplementedError(token)
            empty = False
        raise syntax_error("Expected a matching end quote", quote)

    def syntax_error(msg: str, token: Token) -> SyntaxError:
        """Build and return a SyntaxError exception instance."""
        error = SyntaxError(msg)
        error.text = text.splitlines(keepends=True)[token.line - 1]
        error.lineno = token.line
        error.offset = token.column + 1
        error.end_offset = error.offset + len(token.value)
        error.print_file_and_line = True  # type: ignore[assignment]
        return error

    comment = False
    for token in tokens:
        match token:
            case Token("NEWLINE"):
                comment = False  # Discard
            case _ if comment:
                # Cannot escape a newline in a comment
                if token.type == "ESCAPE" and token.value[-1] == "\n":
                    comment = False
            case Token("COMMENT"):
                comment = True  # Discard
            case Token("WS"):
                pass  # Discard
            case Token("TEXT", value) if value.isidentifier():
                # Pass a token that will be used if tokens is exhausted
                token = Token("TEXT", "", line=token.line, column=token.column + len(token.value))
                yield value, assignment(token)
            case _:
                raise syntax_error("Expected a variable assignment or comment", token)


def evaluate(text: str, env: Mapping[str, str | None]) -> dict[str, str | None]:
    """Parse text and return a dictionary of updates to env.

    env is unchanged. The returned dictionary includes values expanded
    from the updates dictionary and from env. Unset (or deleted)
    variables have values set to None. This way changes from text can be
    isolated from the original env.
    """
    updates: dict[str, str | None] = {}
    env = collections.ChainMap(updates, env)  # type: ignore[arg-type]
    for name, fragments in parse(text):
        if _UPPERCASE_ENV:
            name = name.upper()
        if not fragments:
            value: str | None = None
        else:
            value = "".join(s.expand(env) for s in fragments)
        updates[name] = value
    return updates


def expand(text: str, env: Mapping[str, str | None]) -> dict[str, str]:
    """Return an env updated by assignments in text.

    env is unchanged. The returned dictionary includes values from env
    with updates from text applied, including any unset or deleted
    variables.
    """
    updates: dict[str, str | None] = env | evaluate(text, env)  # type: ignore[operator]
    return {k: v for k, v in updates.items() if v is not None}


if __name__ == "__main__":

    import click

    @click.command(
        context_settings={
            "max_content_width": 120,
            "help_option_names": ["-h", "--help"],
        },
    )
    @click.argument("strings", nargs=-1)
    def main(strings: tuple[str, ...]) -> None:
        """Show the results of processing an environment file."""
        env = os.environ.copy()
        for text in strings:
            if text.startswith("@"):
                with click.open_file(text[1:], encoding="utf-8") as file:
                    text = file.read()
            updates = evaluate(text, env)
            for name, value in updates.items():
                if value is None:
                    click.echo(f"unset {name}")
                    env.pop(name, None)
                else:
                    click.echo(f"{name} = {value!r}")
                    env[name] = value

    main()
