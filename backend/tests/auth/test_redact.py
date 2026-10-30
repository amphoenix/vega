import pytest

from app.shared.redact import redact


@pytest.mark.parametrize('raw,gone', [
    ('GET /api/x?a=1&key=SECRETKEY123&b=2', 'SECRETKEY123'),
    ('X-Vega-Key: abcdef123456789 next', 'abcdef123456789'),
    ('Authorization: Bearer abc.def-ghi123 trailing', 'abc.def-ghi123'),
    ('token eyJhbGciOiJIUzUx.eyJpc3MiOiJkaGFu.ZB6RgJIiMySA done', 'eyJhbGciOiJIUzUx'),
    ('k=AIza' + 'B' * 35 + ' x', 'B' * 35),
    ("{'set-cookie': '__cf_bm=gcW0o; HttpOnly; Secure', 'server': 'cloudflare'}", 'gcW0o'),
    ('Cookie: sid=abc123; theme=dark', 'abc123'),
    ('api_key=sk-1234567890abcdef used', 'sk-1234567890abcdef'),
    ('password: hunter2222', 'hunter2222'),
])
def test_credentials_are_removed(raw, gone):
    assert gone not in redact(raw)


def test_ordinary_log_lines_are_left_alone():
    line = 'SIGNAL SELL ADAUSDT @ 0.1948 conf=80 RSI=70.8 token_count=3 Loaded 84540 instruments'
    assert redact(line) == line


def test_context_around_a_redaction_survives():
    out = redact("{'date': 'x', 'set-cookie': '__cf_bm=abc; HttpOnly', 'server': 'cloudflare', 'cf-ray': 'a5280'}")
    assert "'server': 'cloudflare'" in out and "'cf-ray': 'a5280'" in out


def test_configured_secrets_are_removed_wherever_they_appear():
    out = redact('body had MyConfiguredSecretValue123 inside', ['MyConfiguredSecretValue123', 'short'])
    assert 'MyConfiguredSecretValue123' not in out and 'inside' in out


def test_tiny_extra_secrets_are_ignored_so_normal_words_survive():
    assert redact('the word short stays', ['short']) == 'the word short stays'
