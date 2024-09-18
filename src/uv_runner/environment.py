"""Parse an environment file or string.

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
    expression        ::=  (assignment | comment | ws)* "\n"
    assignment        ::=  ws* name ws* "=" ws* value (ws+ comment)?
    name              ::=  (letter | "_") (letter | digit | "_")*
    value             ::=  (double-quoted | single-quoted | unquoted)*
    comment           ::=  "#" not-newline
    double-quoted     ::=  double-quote (not-double-quote | escaped)* double-quote
    single-quoted     ::=  single-quote not-single-quote* single-quote
    unquoted          ::=  (not-quote | escaped)+
    escaped           ::=  "\" any-character
    not-newline       ::=  any-character - "\n"
    not-double-quote  ::=  any-character - double-quote
    not-single-quote  ::=  any-character - single-quote
    not-quote         ::=  any-character - (double-quote | single-quote)
    letter            ::=  "A"..."Z" | "a"..."z"
    digit             ::=  "0"..."9"
    ws                ::=  " " | "\t" | "\r" | "\f" | "\v"
    double-quote      ::=  '"' | '\"""'
    single-quote      ::=  "'" | "'''"
    any-character     ::=  ? Any Printable Character ?
"""

from __future__ import annotations

import collections
import os
import re
from typing import Any, cast, Final, Iterator, Literal, Mapping, NamedTuple

__all__ = 'evaluate', 'expand'


_IS_WINDOWS: Final = os.name == 'nt'


TokenType = Literal['ASSIGN', 'COMMENT', 'DQUOTE', 'ESCAPE',
                    'NEWLINE', 'SQUOTE', 'TEXT']


WS: Final = r'[ \t\r\f\v]'  # White space minus newline
TOKENS: Final[tuple[tuple[TokenType, str], ...]] = (
    ('ASSIGN', fr'^{WS}*(?P<name>(?ai:[a-z_][a-z0-9_]*)){WS}*={WS}*'),
    ('COMMENT', fr'(?:^{WS}*|{WS}+|(?<={WS}|\n))#.*$'),
    ('DQUOTE', '"""|"'),
    ('ESCAPE', r'\\(?s:.)'),
    ('NEWLINE', fr'{WS}*\n'),
    ('SQUOTE', "'''|'"),
)
SPLIT_RE: Final = re.compile('|'.join(f'(?P<{kind}>{pattern})'
                                      for kind, pattern in TOKENS),
                             re.MULTILINE)
EXPAND_RE: Final = re.compile(r'\$(\{)?(?P<name>(?ai:[a-z_][a-z0-9_]*))(?(1)})',
                              re.MULTILINE)


class Fragment(str):
    """String fragment of variable assignment value."""

    __slots__ = 'expandable',

    expandable: bool

    def __new__(cls, value: Any, expandable: bool) -> Fragment:
        obj = super().__new__(cls, value)
        obj.expandable = expandable
        return obj

    def expand(self, env: Mapping[str, str | None]) -> str:
        """Expand variable substitutions in string fragment.

        Expands occurrences of substitutions of the form $name and ${name}
        to values from env, or to the empty string if not found. Only
        objects with a truthy .expandable attribute are expanded.
        """
        def repl(match: re.Match[str]) -> str:
            name = match.group('name')
            if _IS_WINDOWS:
                name = name.upper()
            return env.get(name) or ''

        if self.expandable:
            return EXPAND_RE.sub(repl, self)
        return str(self)


class Token(NamedTuple):
    """Represents a token in the parsed string."""
    type: TokenType
    value: str
    line: int
    column: int
    match: re.Match[str] | None


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
            yield Token('TEXT', text[previous_end:start], line, previous_end - line_start, None)
        previous_end = match.end()
        yield Token(kind, value, line, column, match)
        if kind == 'NEWLINE':
            line += 1
            line_start = start

    if previous_end < len(text):
        # Yield any text after the last match
        yield Token('TEXT', text[previous_end:], line, previous_end - line_start, None)


def parse(text: str) -> Iterator[tuple[str, Iterator[Fragment]]]:
    """Iterate variable assignments in a string.

    Yields 2-tuples of variable name and value fragments.
    """
    tokens = tokenize(text)

    def assignment_value() -> Iterator[Fragment]:
        """Parse the right-hand side of a variable assignment."""
        for token in tokens:
            match token:
                case Token('COMMENT' | 'NEWLINE'):
                    break  # discard
                case Token('ASSIGN', value):
                    # Treat assignments within assignments as un-expandable text
                    yield Fragment(value, False)
                case Token('TEXT', value):
                    yield Fragment(value, True)
                case Token('SQUOTE' | 'DQUOTE'):
                    yield from quoted(token)
                case Token('ESCAPE', value):
                    yield Fragment(value[1:], False)
                case _:
                    raise syntax_error('Unexpected token', token)

    def quoted(quote: Token) -> Iterator[Fragment]:
        """Parse a quoted fragment.

        Matches quotation marks while handling character escaping.
        """
        empty = True
        for token in tokens:
            match token:
                case Token('DQUOTE' | 'SQUOTE' as kind, value) if kind == quote.type and value == quote.value:
                    if empty:
                        yield Fragment('', False)  # Handle empty string
                    return  # End quoted string
                case Token('ESCAPE', value) if quote.type == 'SQUOTE':
                    if value[1:] == quote.value:
                        # Escaped single-quote (\') in single-quoted string should not be escaped.
                        # It instead ends the string with the backslash as the last character.
                        yield Fragment(value[:1], False)
                        return
                    # Return the unescaped string when in single-quotes
                    yield Fragment(value, False)
                case Token('ESCAPE', value):
                    # Return character after escape in double-quoted strings
                    yield Fragment(value[1:], False)
                case Token(_, value):
                    yield Fragment(value, quote.type == 'DQUOTE')
                case _:
                    raise syntax_error('Unexpected token', token)
            empty = False
        raise syntax_error('Unterminated quote', quote)

    def syntax_error(msg: str, token: Token) -> SyntaxError:
        """Build and return a SyntaxError exception instance."""
        error = SyntaxError(msg)
        error.text = text.splitlines(True)[token.line - 1]
        error.lineno = token.line
        error.offset = token.column
        error.end_offset = error.offset + len(token.value)
        error.print_file_and_line = True  # type: ignore[attr-defined]
        return error

    for token in tokens:
        match token:
            case Token('COMMENT' | 'NEWLINE'):
                pass  # Discard
            case Token('ASSIGN'):
                assert token.match
                yield token.match.group('name'), assignment_value()
            case _:
                raise syntax_error('Unexpected token', token)


def evaluate(text: str, env: Mapping[str, str | None]) -> dict[str, str | None]:
    """Parse text and return a dictionary of updates to env.

    env is unchanged. The returned dictionary includes values expanded
    from the updates dictionary and from env. Unset (or deleted)
    variables have values set to None. This way changes from text can be
    isolated from the original env.
    """
    updates: dict[str, str | None] = {}
    env = collections.ChainMap(updates, env)  # type: ignore[arg-type]
    for name, (*fragments,) in parse(text):
        if _IS_WINDOWS:
            name = name.upper()
        if not fragments:
            value: str | None = None
        else:
            value = ''.join(s.expand(env) for s in fragments)
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
