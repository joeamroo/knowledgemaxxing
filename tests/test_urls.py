from km.urls import canonicalize, domain_of, unwrap_google_redirect


def test_strip_utm_params():
    assert (
        canonicalize("https://example.com/post?utm_source=tw&utm_medium=x&id=5")
        == "https://example.com/post?id=5"
    )


def test_strip_tracking_params():
    assert (
        canonicalize("https://example.com/a?fbclid=x&gclid=y&ref=z&s=1&t=2&q=keep")
        == "https://example.com/a?q=keep"
    )


def test_lowercase_scheme_host_only():
    assert (
        canonicalize("HTTPS://Example.COM/Path/Case")
        == "https://example.com/Path/Case"
    )


def test_strip_fragment_and_trailing_slash():
    assert canonicalize("https://example.com/post/#section") == "https://example.com/post"
    assert canonicalize("https://example.com/") == "https://example.com"


def test_unify_twitter_hosts():
    for host in ("mobile.twitter.com", "x.com", "twitter.com", "www.twitter.com"):
        assert (
            canonicalize(f"https://{host}/user/status/123")
            == "https://twitter.com/user/status/123"
        )


def test_unwrap_google_redirect():
    wrapped = "https://www.google.com/url?q=https%3A%2F%2Fguzey.com%2Fpost&sa=D&usg=x"
    assert unwrap_google_redirect(wrapped) == "https://guzey.com/post"


def test_unwrap_passthrough_non_redirect():
    assert unwrap_google_redirect("https://guzey.com/post") == "https://guzey.com/post"


def test_canonicalize_unwraps_google_redirect():
    wrapped = "https://www.google.com/url?q=https%3A%2F%2Fguzey.com%2Fpost%2F&sa=D"
    assert canonicalize(wrapped) == "https://guzey.com/post"


def test_domain_of():
    assert domain_of("https://Sub.Example.com/x") == "sub.example.com"
    assert domain_of("https://www.example.com/x") == "example.com"
    assert domain_of("not a url") == ""


def test_canonicalize_garbage_survives():
    assert canonicalize("") == ""
    assert canonicalize("notaurl") == "notaurl"


def test_canonicalize_template_port_survives():
    # code snippets in chats contain fake URLs with template ports
    assert canonicalize("http://localhost:${PORT}/api") is not None
    assert canonicalize("http://localhost:3000`**") is not None
