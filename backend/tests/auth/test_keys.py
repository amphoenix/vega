import pytest

from app.auth import keys


def test_named_and_bare_entries():
    e = keys.parse_entries('alice:abc,bob:def, plain ,')
    assert [(x.name, x.secret) for x in e] == [('alice', 'abc'), ('bob', 'def'), ('key3', 'plain')]


def test_uppercase_or_odd_prefix_is_part_of_the_secret():
    assert keys.parse_entries('UPPER:zz')[0].secret == 'UPPER:zz'
    assert keys.parse_entries('has space:zz')[0].secret == 'has space:zz'


@pytest.mark.parametrize('secret,ok', [
    ('weakkey123', False),                     # the old 10-char key
    ('a' * 40, False),                         # long but a single repeated character
    ('abcabcabcabcabcabcabcabcabcabcab', False),   # only 3 distinct characters
    ('x' * 31, False),
    ('Xk3pQ9wLm2Zr7TvB5nYc8HdF1aJ4uSeG', True),
])
def test_strength_rule(secret, ok):
    assert keys.is_strong(secret) is ok


def test_generated_secrets_are_strong_and_unique():
    a, b = keys.generate_secret(), keys.generate_secret()
    assert keys.is_strong(a) and keys.is_strong(b) and a != b and len(a) >= 43


def test_weak_entries_are_never_usable():
    raw = 'old:weakkey123,alice:Xk3pQ9wLm2Zr7TvB5nYc8HdF1aJ4uSeG'
    assert [e.name for e in keys.usable_entries(raw)] == ['alice']
    assert [e.name for e in keys.weak_entries(raw)] == ['old']


def test_client_secret_is_the_first_usable_one_without_the_name():
    raw = 'old:short,alice:Xk3pQ9wLm2Zr7TvB5nYc8HdF1aJ4uSeG,bob:Zz9y8X7w6V5u4T3s2R1q0P9o8N7m6L5k'
    assert keys.first_secret(raw) == 'Xk3pQ9wLm2Zr7TvB5nYc8HdF1aJ4uSeG'
    assert keys.first_secret('weak') == '' and keys.first_secret('') == ''


def test_fingerprint_is_short_and_stable_and_hides_the_key():
    secret = 'Xk3pQ9wLm2Zr7TvB5nYc8HdF1aJ4uSeG'
    fp = keys.fingerprint(secret)
    assert fp == keys.fingerprint(secret) and len(fp) == 8 and secret not in fp
