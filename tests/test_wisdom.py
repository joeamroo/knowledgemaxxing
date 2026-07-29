from km.extract.wisdom import is_aphorism, is_contrarian, is_natural_law


def test_natural_law_names():
    assert is_natural_law("Goodhart's law strikes again in education")
    assert is_natural_law("classic example of the Lindy effect")
    assert is_natural_law("this is just Chesterton's fence")
    assert is_natural_law("Berkson's paradox explains hot people being mean")
    assert not is_natural_law("I love laws about zoning")


def test_law_suffix_shape():
    assert is_natural_law("Cunningham's Law: the fastest way to get an answer")
    assert is_natural_law("Amara's law applies to AI right now")


def test_contrarian_framings():
    assert is_contrarian("Everyone says diversify, but concentration built every fortune")
    assert is_contrarian("Conventional wisdom about sleep is completely wrong")
    assert is_contrarian("Most people think college matters. In reality it signals.")
    assert not is_contrarian("I diversified my portfolio today")


def test_aphorism_shape():
    assert is_aphorism("The obstacle is the way, not the thing in the way")
    assert is_aphorism("You do not rise to your goals, you fall to your systems")
    assert not is_aphorism("check out this link https://t.co/abc")
    assert not is_aphorism("what should I eat today?")
    assert not is_aphorism("I think my life is great")  # personal, not aphorism
    assert not is_aphorism("too short")
