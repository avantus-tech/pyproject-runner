import functools
import string
from typing import Any, Callable, Iterator, no_type_check

import hypothesis
from hypothesis import strategies as st
import pytest

from pyproject_runner import environment


AdjEnv = Callable[[dict[str, str]], dict[str, str]]


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
def tokens() -> st.SearchStrategy[list[tuple[str, str]]]:
    def limit_values(items: list[tuple[str, str]]) -> bool:
        previous_item = '', ''
        for i, item in enumerate(items):
            match previous_item, item:
                case ('"', 'DQUOTE'), ('"', 'DQUOTE') if i > 1 and items[i - 2] == item:
                    return False  # three single double-quotes in a row
                case ("'", 'SQUOTE'), ("'", 'SQUOTE') if i > 1 and items[i - 2] == item:
                    return False  # three single single-quotes in a row
                case ('"', 'DQUOTE'), ('"""', 'DQUOTE'):
                    return False  # A single next to a triple double-quote
                case ("'", 'SQUOTE'), ("'''", 'SQUOTE'):
                    return False  # A single next to ao triple single-quote
            previous_item = item

        match items:
            case [(text, 'TEXT'), *_] if not text or text[0].isspace():
                return False  # whitespace at the beginning of the text
            case [*_, (text, 'TEXT')] if not text or text[-1].isspace():
                return False  # White space at the end of the text

        return True

    def merge_text(items: list[tuple[str, str]]) -> list[tuple[str, str]]:
        result: list[tuple[str, str]] = []
        previous_item = '', ''
        for item in items:
            match previous_item, item:
                case (string1, 'TEXT'), (string2, 'TEXT'):
                    result.pop(-1)
                    item = (string1 + string2, 'TEXT')
            result.append(item)
            previous_item = item
        return result


    tag = lambda s, type='TEXT': s.map(lambda t: [(t, type)] if t else [])

    merge = functools.partial(st.builds, lambda *args: functools.reduce(lambda x, y: x + y, args, []))
    zero_or_one = lambda s: st.just([]) | s
    one_or_more = lambda s: st.recursive(s, lambda x: merge(x, s))

    characters = functools.partial(st.characters, codec='utf-8')
    ws = functools.partial(st.text, ' \t\r\f\v')
    text = lambda exclude=None, min_size=0, max_size=None: st.text(
        characters(exclude_characters=exclude),
        min_size=min_size, max_size=max_size)

    # Reproduces the grammar in the environment module
    single_quote = lambda: st.just([("'", 'SQUOTE')])
    double_quote = lambda: st.just([('"', 'DQUOTE')])
    newline = lambda: st.just([('\n', 'NEWLINE')])
    escaped = lambda: st.builds(lambda t: [(fr'\{t}', 'ESCAPE')], text(min_size=1, max_size=1))
    unquoted = lambda: tag(text('\\"\'\n', min_size=1)) | escaped()
    single_quoted = lambda: st.one_of(
        st.builds(lambda x: [q := ("'", 'SQUOTE'), *x, q],
                  tag(text("\\\n\"'", min_size=1)) | escaped() | double_quote() | newline()),
        st.builds(lambda x: [q := ("'''", 'SQUOTE'), *x, q],
                  tag(text("\\\n\"'", min_size=1)) | escaped() | single_quote() | double_quote() | newline()),
    )
    double_quoted = lambda: st.one_of(
        st.builds(lambda x: [q := ('"', 'DQUOTE'), *x, q],
                  tag(text('\\\n\'"', min_size=1)) | escaped() | single_quote() | newline()),
        st.builds(lambda x: [q := ('"""', 'DQUOTE'), *x, q],
                  tag(text('\\\n\'"', min_size=1)) | escaped() | single_quote() | double_quote() | newline()),
    )
    value = lambda: one_or_more(double_quoted() | single_quoted() | unquoted()).map(merge_text).filter(limit_values)
    comment = lambda ws: tag(st.builds('{}#{}'.format, ws, text('\n')), 'COMMENT')
    name = lambda: st.builds(
        str.__add__,
        st.text(string.ascii_letters + '_', min_size=1, max_size=1),
        st.text(string.ascii_letters + string.digits + '_'),
    )
    _assign = lambda min_ws: tag(st.builds(
        lambda *args: ''.join(args), ws(), name(), ws(), st.just('='), ws(min_size=min_ws)), 'ASSIGN')
    assignment = lambda: st.one_of(
        merge(_assign(0), value(), comment(ws(min_size=1))),
        merge(_assign(0), zero_or_one(value())),
        merge(_assign(1), comment(st.just(''))),
    )
    expression = lambda: st.one_of(
        merge(assignment() | comment(ws()), newline()),
        st.builds(lambda t: [(f'{t}\n', 'NEWLINE')], ws()),
    )

    return expression()


@hypothesis.given(tokens())
@hypothesis.example([(' ', 'TEXT')])
@hypothesis.example([(' ABC ', 'TEXT')])
def test_tokenize(expected_tokens: list[tuple[str, str]]) -> None:
    expr = ''.join(text for text, _ in expected_tokens)
    for token, (expected_value, expected_type) in zip(
            environment.tokenize(expr), expected_tokens, strict=True):
        assert token.type == expected_type
        assert token.value == expected_value


@hypothesis.given(st.builds(lambda toks: ''.join(s for s, _ in toks), tokens()))
@hypothesis.example(' ')
@hypothesis.example('A=\\\nB=2')
def test_parse(expr: str) -> None:
    tuple(environment.parse(expr))


@pytest.mark.parametrize('text, error', [
    ('ABC', 'Unexpected token'),
    ('ABC="123', 'Unterminated quote'),
    ('ABC=123"', 'Unterminated quote'),
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
