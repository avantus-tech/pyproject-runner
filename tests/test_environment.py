import functools
import re
import string
from typing import Any, Callable, Iterator, Literal, no_type_check

import hypothesis
from hypothesis import strategies as st
import pytest

from pyproject_runner import environment


AdjEnv = Callable[[dict[str, str]], dict[str, str]]
TokenPair = tuple[str, environment.TokenType]
TokenPairs = list[TokenPair]
TokenList = st.SearchStrategy[TokenPairs]


@pytest.fixture(params=[False, True])
def adjust_env_case(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> Iterator[AdjEnv]:
    monkeypatch.setattr(environment, '_UPPERCASE_ENV', request.param)
    if request.param:
        def _adjust_env_case(env: dict[str, str]) -> dict[str, str]:
            return {k.upper(): v for k, v in env.items()}
    else:
        def _adjust_env_case(env: dict[str, str]) -> dict[str, str]:
            return env
    yield _adjust_env_case


@pytest.mark.parametrize('expandable', (False, True))
@pytest.mark.parametrize('init_value, expected', (
    ('This $variable is ${unknown}.', 'This  is .'),  # Unknown
    ('Some $foo is ${path}.', 'Some bar is /a/b/file.txt.'),  # Valid
    ('$user', 'John Doe'),
    ('${user}s', 'John Does'),
    (r'Some \$foo is $\{path}.', 'Some \\bar is $\\{path}.'),
    ('${incomplete', '${incomplete'),
    (r'${escaped\}', '${escaped\\}'),
))
def test_fragment(init_value: Any, expected: str, expandable: bool, adjust_env_case: AdjEnv) -> None:
    env = adjust_env_case({
        'foo': 'bar',
        'path': '/a/b/file.txt',
        'user': 'John Doe',
    })
    fragment = environment.Fragment(init_value, expandable)
    assert str(fragment) == str(init_value)
    assert fragment.expandable is expandable
    if expandable:
        assert fragment.expand(env) == expected
    else:
        assert fragment.expand(env) == str(init_value)


@no_type_check
def tokens() -> TokenList:
    def limit_quotes(items: TokenPairs) -> bool:
        previous_item: TokenPair = '\n', 'NEWLINE'
        for i, item in enumerate(items):
            match previous_item, item:
                case ('"', 'DQUOTE'), ('"', 'DQUOTE') if i > 1 and items[i - 2] == item:
                    return False  # three single double-quotes in a row
                case ("'", 'SQUOTE'), ("'", 'SQUOTE') if i > 1 and items[i - 2] == item:
                    return False  # three single single-quotes in a row
                case ('"', 'DQUOTE'), ('"""', 'DQUOTE'):
                    return False  # A single next to a triple double-quote
                case ("'", 'SQUOTE'), ("'''", 'SQUOTE'):
                    return False  # A single next to a triple single-quote
            previous_item = item
        return True

    def merge_dups(items: TokenPairs) -> TokenPairs:
        result: TokenPairs = []
        previous_item: TokenPair = '\n', 'NEWLINE'
        for item in items:
            match previous_item, item:
                case (string1, type1), (string2, type2) if type1 == type2 and type1 in ['TEXT', 'WS']:
                    result.pop(-1)
                    item = string1 + string2, type1
            result.append(item)
            previous_item = item
        return result

    def quantify(strategy: Callable[[], TokenList]) -> Callable[..., TokenList]:
        def _quantify(how: Literal['?', '*', '+'] | None = None) -> TokenList:
            match how:
                case '?':
                    return st.just([]) | strategy()
                case '*':
                    return st.just([]) | st.recursive(s := strategy(), lambda x: concat(x, s))
                case '+':
                    return st.recursive(s := strategy(), lambda x: concat(x, s))
                case None:
                    return strategy()
                case _:
                    raise NotImplementedError(how)
        return _quantify

    def token(type: environment.TokenType, strategy: Callable[[], st.SearchStrategy[str]]) -> Callable[..., TokenList]:
        return quantify(lambda: strategy().map(lambda t: [(t, type)]))

    name_re = re.compile(r'(?ai:(?:^|(?<=\s|\n))([a-z_][a-z0-9_]*))')

    @st.composite
    def TEXT(draw: Callable[[st.SearchStrategy[str]], str]) -> TokenPairs:
        string = draw(st.text(
            st.characters(codec='utf-8', exclude_characters=' \t\r\f\v\n#=\\"\''), min_size=1))
        return [(s, 'NAME' if i % 2 else 'TEXT')
                for i, s in enumerate(name_re.split(string)) if s]

    concat = functools.partial(st.builds, lambda *args: functools.reduce(lambda x, y: x + y, args, []))

    # Reproduces the grammar in the environment module
    ESCAPE = token('ESCAPE', lambda: st.builds('\\{}'.format, st.text(min_size=1, max_size=1)))
    ASSIGN = token('ASSIGN', lambda: st.just('='))
    COMMENT = token('COMMENT', lambda: st.just('#'))
    DQUOTE = token('DQUOTE', lambda: st.just('"') | st.just('"""'))
    NEWLINE = token('NEWLINE', lambda: st.just('\n'))
    SQUOTE = token('SQUOTE', lambda: st.just("'") | st.just("'''"))
    WS = lambda how=None: st.text(' \t\r\f\v', **{
        '?': {'max_size': 1},
        '*': {},
        '+': {'min_size': 1},
        None: {'min_size': 1, 'max_size': 1},
    }[how]).map(lambda t: [(t, 'WS')] if t else [])

    NAME = token('TEXT', lambda: st.builds(
        str.__add__,
        st.text(string.ascii_letters + '_', min_size=1, max_size=1),
        st.text(string.ascii_letters + string.digits + '_'),
    ))
    TEXT = token('TEXT', lambda: st.text(
        st.characters(codec='utf-8', exclude_characters=' \t\r\f\v\n#=\\"\''), min_size=1))


    _ = quantify
    not_quote = lambda: ASSIGN() | COMMENT() | TEXT() | WS()
    not_single_quote = _(lambda: ESCAPE() | ASSIGN() | COMMENT() | NEWLINE() | DQUOTE() | TEXT() | WS())
    not_double_quote = lambda: ESCAPE() | ASSIGN() | COMMENT() | NEWLINE() | SQUOTE() | TEXT() | WS()
    not_newline = _(lambda: ESCAPE() | ASSIGN() | COMMENT() | DQUOTE() | SQUOTE() | TEXT() | WS())
    escaped = ESCAPE
    unquoted = lambda: _(lambda: not_quote() | ESCAPE())('+')
    single_quoted = lambda: st.one_of(
        st.builds(lambda x: [q := ("'", 'SQUOTE'), *x, q], not_single_quote('*')),
        st.builds(lambda x: [q := ("'''", 'SQUOTE'), *x, q], not_single_quote('*')),
    )
    double_quoted = lambda: st.one_of(
        st.builds(lambda x: [q := ('"', 'DQUOTE'), *x, q], _(lambda: not_double_quote() | escaped())('*')),
        st.builds(lambda x: [q := ('"""', 'DQUOTE'), *x, q], _(lambda: not_double_quote() | escaped())('*')),
    )
    value = lambda: _(lambda: double_quoted() | single_quoted() | unquoted())('*')
    comment = lambda: concat(COMMENT(), not_newline('*'))
    assignment = lambda: concat(WS('*'), NAME(), WS('*'), ASSIGN(), WS('*'), value(),
                               _(lambda: concat(WS('+'), comment()))('?'))
    expression = lambda: concat(assignment() | comment() | WS('*'), NEWLINE()).map(merge_dups).filter(limit_quotes)

    return expression()


@hypothesis.given(tokens())
def test_tokenize(expected_tokens: list[tuple[str, str]]) -> None:
    expr = ''.join(text for text, _ in expected_tokens)
    for token, (expected_value, expected_type) in zip(
            environment.tokenize(expr), expected_tokens, strict=True):
        assert token.type == expected_type
        assert token.value == expected_value


@hypothesis.given(st.builds(lambda toks: ''.join(s for s, _ in toks), tokens()))
@hypothesis.example(' ')
@hypothesis.example('A=\\\nB=2')
@hypothesis.example('A=" #" #')
@hypothesis.example('A=$foo=bar')
def test_parse(expr: str) -> None:
    tuple(environment.parse(expr))


@pytest.mark.parametrize('text, error', [
    ('ABC', 'invalid syntax'),
    ('= XYZ', 'unexpected token'),
    ('ABC XYZ', 'unexpected token'),
    ('ABC="123', 'unterminated quote'),
    ('ABC=123"', 'unterminated quote'),
])
def test_parse_invalid(text: str, error: str) -> None:
    with pytest.raises(SyntaxError, match=error):
        tuple(environment.parse(text))


@hypothesis.given(st.text())
def test_parse_not_implemented_error(text: str) -> None:
    """Check that parse() never raises NotImplementedError."""
    try:
        tuple(environment.parse(text))
    except NotImplementedError:
        raise AssertionError('parse() raised NotImplementedError')
    except SyntaxError:
        pass


def test_expand(adjust_env_case: AdjEnv) -> None:
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

escaped = \$do $\{not} ${expand\}
''' """
triple_squote=''' #
# keep this
ignore ', ", and \\ in here
also ignore $PATH expansion
'''
     """
    input_env = adjust_env_case({
        'PATH': '/usr/bin:/bin',
        'empty': 'some'
    })
    expected_env = adjust_env_case({
        'PATH': '/usr/bin:/bin',
        'bad_comment': 'this is some stuff# with a bad comment',
        'blah': 'one\ntwo\nthree\n',
        'empty_string': '',
        'escaped': '$do ${not} ${expand}',
        'escaped_dquote': 'this " is escaped',
        'escaped_squote': 'this \\ does not work',
        'first': '1st',
        'foo': '\nbar=43',
        'good_comment': 'this is some stuff',
        'mixed': 'this is quoted in multiple ways',
        'multiline': 'one\n two =\n    three\n    ',
        'none': 'this  not ',
        'quoted_comment': ' # this is not a comment',
        'second': '2nd',
        'some': 'this is the /usr/bin:/bin',
        'trailer': 'some text here',
        'triple_dquote': '\nthis has "triple" \'quotes\'\n',
        'triple_squote': ' #\n# keep this\nignore \', ", and \\ in here\n'
                         'also ignore $PATH expansion\n',
        'unterminated_quote': 'this isnt complete\n\nuntil=its terminated here',
    })
    output_env = environment.expand(input_text, input_env)
    assert output_env == expected_env
