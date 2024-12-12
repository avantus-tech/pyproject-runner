# ruff: noqa: N806
"""Tests for the environment module."""

from collections.abc import Callable
import functools
import string
from typing import Any, Literal

import hypothesis
from hypothesis import strategies as st
import pytest

from pyproject_runner import environment

AdjEnv = Callable[[dict[str, str]], dict[str, str]]
TokenPair = tuple[str, environment.TokenType]
TokenPairs = list[TokenPair]
TokenList = st.SearchStrategy[TokenPairs]


@pytest.fixture(params=[False, True])
def adjust_env_case(monkeypatch: pytest.MonkeyPatch,
                    request: pytest.FixtureRequest) -> AdjEnv:
    """Fixture to test environment with and without Windows case adjustment."""
    monkeypatch.setattr(environment, "_UPPERCASE_ENV", request.param)
    if request.param:
        def _adjust_env_case(env: dict[str, str]) -> dict[str, str]:
            return {k.upper(): v for k, v in env.items()}
    else:
        def _adjust_env_case(env: dict[str, str]) -> dict[str, str]:
            return env
    return _adjust_env_case


@pytest.mark.parametrize("expandable", [False, True])
@pytest.mark.parametrize(("init_value", "expected"), [
    ("This $variable is ${unknown}.", "This  is ."),  # Unknown
    ("Some $foo is ${path}.", "Some bar is /a/b/file.txt."),  # Valid
    ("$user", "John Doe"),
    ("${user}s", "John Does"),
    (r"Some \$foo is $\{path}.", "Some \\bar is $\\{path}."),
    ("${incomplete", "${incomplete"),
    (r"${escaped\}", "${escaped\\}"),
])
def test_fragment(*, init_value: Any, expected: str,
                  expandable: bool, adjust_env_case: AdjEnv) -> None:
    """Test fragment expansion."""
    env = adjust_env_case({
        "foo": "bar",
        "path": "/a/b/file.txt",
        "user": "John Doe",
    })
    fragment = environment.Fragment(init_value, expandable=expandable)
    assert str(fragment) == str(init_value)
    assert fragment.expandable is expandable
    if expandable:
        assert fragment.expand(env) == expected
    else:
        assert fragment.expand(env) == str(init_value)


def tokens() -> TokenList:
    """Return a hypothesis strategy to top fuzz parsing functions.

    Generates valid expressions for fuzzing the tokenizer/parser.
    """

    def limit_quotes(items: TokenPairs) -> bool:
        """Avoid ambiguous triple quotes."""
        previous_item: TokenPair = "\n", "NEWLINE"
        for i, item in enumerate(items):
            match previous_item, item:
                case ('"', "DQUOTE"), ('"', "DQUOTE") if i > 1 and items[i - 2] == item:
                    return False  # three single double-quotes in a row
                case ("'", "SQUOTE"), ("'", "SQUOTE") if i > 1 and items[i - 2] == item:
                    return False  # three single single-quotes in a row
                case ('"', "DQUOTE"), ('"""', "DQUOTE"):
                    return False  # A single before a triple double-quote
                case ("'", "SQUOTE"), ("'''", "SQUOTE"):
                    return False  # A single before a triple single-quote
            previous_item = item
        return True

    def merge_dups(items: TokenPairs) -> TokenPairs:
        """Merge adjacent TEXT and WS tokens in to a single token."""
        result: TokenPairs = []
        previous_item: TokenPair = "\n", "NEWLINE"
        for item in items:
            match previous_item, item:
                case (string1, type1), (string2, type2) if (
                        type1 == type2 and type1 in {"TEXT", "WS"}):
                    result.pop(-1)
                    item = string1 + string2, type1
            result.append(item)
            previous_item = item
        return result

    def quantify(strategy: st.SearchStrategy[TokenPairs]) -> Callable[..., TokenList]:
        """Provide regex-like quantifiers for strategy productions.

        This allows the productions below to closely match the grammar
        in the environment module.
        """
        def _quantify(how: Literal["?", "*", "+"] | None = None) -> TokenList:
            match how:
                case "?":
                    return st.just([]) | strategy
                case "*":
                    return st.just([]) | st.recursive(strategy, lambda x: concat(x, strategy))
                case "+":
                    return st.recursive(strategy, lambda x: concat(x, strategy))
                case None:
                    return strategy
                case _:
                    raise NotImplementedError(how)
        return _quantify

    def term(token_type: environment.TokenType, strategy: st.SearchStrategy[str]) -> TokenList:
        """Tag a terminal with its token type."""
        return strategy.map(lambda t: [(t, token_type)] if t else [])

    # Concatenates TokenPairs into a single list
    concat = functools.partial(
        st.builds, lambda *args: functools.reduce(list.__add__, args, []))

    # Reproduce the grammar in the environment module

    # Terminals:
    ESCAPE = term("ESCAPE", st.builds("\\{}".format, st.text(min_size=1, max_size=1)))
    ESCAPE_WO_NEWLINE = term("ESCAPE", st.builds("\\{}".format, st.text(
        st.characters(codec="utf-8", exclude_characters="\n"), min_size=1, max_size=1)))
    ASSIGN = term("ASSIGN", st.just("="))
    COMMENT = term("COMMENT", st.just("#"))
    DQUOTE = term("DQUOTE", st.just('"') | st.just('"""'))
    NEWLINE = term("NEWLINE", st.just("\n"))
    SQUOTE = term("SQUOTE", st.just("'") | st.just("'''"))

    NAME = term("TEXT", st.builds(
        str.__add__,
        st.text(string.ascii_letters + "_", min_size=1, max_size=1),
        st.text(string.ascii_letters + string.digits + "_"),
    ))
    TEXT = term("TEXT", st.text(
        st.characters(codec="utf-8", exclude_characters=' \t\r\f\v\n#=\\"\''), min_size=1))

    # Productions:
    def ws(how: Literal["?", "*", "+"] | None = None) -> st.SearchStrategy[TokenPairs]:
        """Generate a whitespace terminal according to the given quantifier."""
        kwargs = {
            "?": {"max_size": 1},
            "*": {},
            "+": {"min_size": 1},
            None: {"min_size": 1, "max_size": 1},
        }[how]
        return term("WS", st.text(" \t\r\f\v", **kwargs))

    _ = quantify
    not_quote = ASSIGN | TEXT | ws()
    not_single_quote = ESCAPE | ASSIGN | COMMENT | NEWLINE | DQUOTE | TEXT | ws()
    not_double_quote = ESCAPE | ASSIGN | COMMENT | NEWLINE | SQUOTE | TEXT | ws()
    not_newline = ESCAPE_WO_NEWLINE | ASSIGN | COMMENT | DQUOTE | SQUOTE | TEXT | ws()
    escaped = ESCAPE
    unquoted = _(not_quote | escaped)("+")
    single_quoted = st.one_of(
        st.builds(lambda x: [q := ("'", "SQUOTE"), *x, q], _(not_single_quote)("*")),
        st.builds(lambda x: [q := ("'''", "SQUOTE"), *x, q], _(not_single_quote)("*")),
    )
    double_quoted = st.one_of(
        st.builds(lambda x: [q := ('"', "DQUOTE"), *x, q], _(not_double_quote | escaped)("*")),
        st.builds(lambda x: [q := ('"""', "DQUOTE"), *x, q], _(not_double_quote | escaped)("*")),
    )
    value = _(double_quoted | single_quoted | unquoted)("*")
    comment = concat(COMMENT, _(not_newline)("*"))
    assignment = concat(ws("*"), NAME, ws("*"), ASSIGN, ws("*"), value,
                        _(concat(ws("+"), comment))("?"))
    expression = concat(assignment | comment | ws("*"), NEWLINE).map(
        merge_dups).filter(limit_quotes)

    return expression  # noqa: RET504


@hypothesis.given(tokens())
def test_tokenize(expected_tokens: list[tuple[str, str]]) -> None:
    """Fuzz the tokenize() function with valid expressions."""
    expr = "".join(text for text, _ in expected_tokens)
    for token, (expected_value, expected_type) in zip(
            environment.tokenize(expr), expected_tokens, strict=True):
        assert token.type == expected_type
        assert token.value == expected_value


@hypothesis.given(st.builds(lambda toks: "".join(s for s, _ in toks), tokens()))
@hypothesis.example(" ")
@hypothesis.example("A=\\\nB=2")
@hypothesis.example('A=" #" #')
@hypothesis.example("A=$foo=bar")
def test_parse(expr: str) -> None:
    """Fuzz the parse() function with valid expressions."""
    tuple(environment.parse(expr))


@pytest.mark.parametrize(("text", "error"), [
    ("ABC", "Expected '=' after variable name"),
    ("= XYZ", "Expected a variable assignment or comment"),
    ("ABC XYZ", "Expected '=' after variable name"),
    ('ABC="123', "Expected a matching end quote"),
    ('ABC=123"', "Expected a matching end quote"),
])
def test_parse_invalid(text: str, error: str) -> None:
    """Test that parse() fails with known bad expressions."""
    with pytest.raises(SyntaxError, match=error):
        tuple(environment.parse(text))


@hypothesis.given(st.text())
def test_parse_not_implemented_error(text: str) -> None:
    """Check that parse() never raises NotImplementedError."""
    try:
        tuple(environment.parse(text))
    except NotImplementedError:
        raise AssertionError("parse() raised NotImplementedError")
    except SyntaxError:
        pass


def test_expand(adjust_env_case: AdjEnv) -> None:
    """Test the expand() function with an example env string."""
    input_text = r'''
# comment
first=1st
# another comment

  # and here too
second="2"nd
empty=
empty = 
empty = # with comment
 multiline = "one
 two =
    three
    " # with a comment
bad_comment = this is some stuff# with a bad comment
good_comment=this is some stuff # with a good comment
unterminated_quote=this isn't complete

until=it's terminated here

escaped_dquote="this \" is escaped"
escaped_squote='this \' does not work
mixed="this is quoted" in 'multiple ways'
blah="one
two
three
"
trailer =  some text here  # trailer

triple_dquote="""
this has "triple" 'quotes'
"""
empty_string=""
foo=\
bar=43
some=this is the $PATH
none=this $does not ${exist}
quoted_comment = " # this is not a comment" # but this is
  # newlines end comments, even if escaped \
not_set = # here too \

escaped = \$do $\{not} ${expand\}
''' """
triple_squote=''' #
# keep this
ignore ', ", and \\ in here
also ignore $PATH expansion
'''
     """  # noqa: ISC001, W291
    input_env = adjust_env_case({
        "PATH": "/usr/bin:/bin",
        "empty": "some",
    })
    expected_env = adjust_env_case({
        "PATH": "/usr/bin:/bin",
        "bad_comment": "this is some stuff# with a bad comment",
        "blah": "one\ntwo\nthree\n",
        "empty_string": "",
        "escaped": "$do ${not} ${expand}",
        "escaped_dquote": 'this " is escaped',
        "escaped_squote": "this \\ does not work",
        "first": "1st",
        "foo": "\nbar=43",
        "good_comment": "this is some stuff",
        "mixed": "this is quoted in multiple ways",
        "multiline": "one\n two =\n    three\n    ",
        "none": "this  not ",
        "quoted_comment": " # this is not a comment",
        "second": "2nd",
        "some": "this is the /usr/bin:/bin",
        "trailer": "some text here",
        "triple_dquote": '\nthis has "triple" \'quotes\'\n',
        "triple_squote": ' #\n# keep this\nignore \', ", and \\ in here\n'
                         'also ignore $PATH expansion\n',
        "unterminated_quote": "this isnt complete\n\nuntil=its terminated here",
    })
    output_env = environment.expand(input_text, input_env)
    assert output_env == expected_env
