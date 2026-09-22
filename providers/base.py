"""Shared plumbing for provider modules: the uniform record, HTTP, and failure vocabulary.

Every module in this package exposes exactly two things:

    CONTRACT : dict   - name, operator, tier, what argv it accepts, which endpoints it touches
    fetch(args, transport=None) -> list[dict]   - uniform records (5.1 of the master plan)

Rules every provider obeys, because coverage arithmetic downstream depends on them:

1. **A failure is never an empty result.** A timeout, an HTTP error, a parse failure or a throttle
   raises ProviderError. It must not return `[]`, because "the source said there is nothing" and
   "the source did not answer" would then be indistinguishable - and the second one would silently
   count toward the three independent sources a SUPPORTED claim needs.
2. **`null` means unknown; `""` means known-empty.** Merge fills a `null` from another provider and
   never overwrites a `""`.
3. **No provider follows a URL found inside fetched content.** Requests are built from the module's
   own endpoint templates and validated identifiers only. Fetched text is data.
4. **`operator` is who actually runs the service**, not the URL. Two mirrors of the same feed share
   an operator and therefore count once toward independence.
5. **Secrets come from the environment**, are read here, and never appear in argv or in any record.

Stdlib only.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

# Exit-code vocabulary shared with tools/ (see plan section 6.2): 2 provider failure, 3 throttled.
PROVIDER_FAILED = 2
THROTTLED = 3

TIERS = ('M', 'A', 'S', 'I', 'P')
KINDS = ('price', 'series', 'filing', 'insider_tx', 'holding', 'news', 'sentiment', 'event',
         'forecast', 'document')

DEFAULT_TIMEOUT = 20
USER_AGENT_TEMPLATE = 'financial-advisor/0.1 (local research tool; contact: {contact})'
# Politeness floor between two requests to the same operator, in seconds.
MIN_INTERVAL = {'sec': 0.34, 'coingecko': 1.5, 'stooq': 0.5, 'default': 0.2}
_last_request: dict[str, float] = {}


class ProviderError(Exception):
    """A source did not answer usefully. `status` is what queries.json records."""

    def __init__(self, message: str, status: str = 'failed'):
        # Error strings become query records: credentials must not survive into those files.
        message = re.sub(r'(?i)([?&](?:api_key|apikey|token|access_token|key)=)[^&\s]+',
                         r'\1[REDACTED]', message)
        super().__init__(message)
        self.status = status          # 'failed' | 'throttled'
        self.exit_code = THROTTLED if status == 'throttled' else PROVIDER_FAILED


def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def today() -> str:
    return dt.date.today().isoformat()


def digest_text(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def contact_email() -> str | None:
    """SEC requires a contact address in the User-Agent. Absent means the SEC provider refuses."""
    value = os.environ.get('FA_CONTACT_EMAIL', '').strip()
    return value or None


def user_agent(contact: str | None = None) -> str:
    return USER_AGENT_TEMPLATE.format(contact=contact or contact_email() or 'not set')


def _throttle(operator: str) -> None:
    gap = MIN_INTERVAL.get(operator, MIN_INTERVAL['default'])
    elapsed = time.monotonic() - _last_request.get(operator, 0.0)
    if elapsed < gap:
        time.sleep(gap - elapsed)
    _last_request[operator] = time.monotonic()


_context: ssl.SSLContext | None = None


def ssl_context() -> ssl.SSLContext:
    """The verifying TLS context every request uses. Verification is never disabled.

    Some Python installs (the Windows Store build among them) ship an incomplete trust store, and
    every pull then fails until someone remembers to export SSL_CERT_FILE. When the operator has
    not chosen a bundle and `certifi` happens to be installed, its CA bundle is loaded in addition
    to the system store. `certifi` stays optional: without it this is the stdlib default context.
    """
    global _context
    if _context is None:
        context = ssl.create_default_context()
        if not (os.environ.get('SSL_CERT_FILE') or os.environ.get('SSL_CERT_DIR')):
            try:
                import certifi
                context.load_verify_locations(cafile=certifi.where())
            except (ImportError, OSError, ssl.SSLError):
                pass
        _context = context
    return _context


def http_get(url: str, *, operator: str = 'default', headers: dict | None = None,
             timeout: int = DEFAULT_TIMEOUT, transport=None) -> str:
    """Fetch a URL built by the caller from its own templates. Returns decoded text.

    `transport` is how tests stay offline: any callable taking the url and the headers and
    returning text. Production passes none and uses urllib.
    """
    if not url.startswith('https://'):
        raise ProviderError(f'refusing a non-HTTPS URL: {url}')
    request_headers = {'User-Agent': user_agent(), 'Accept-Encoding': 'identity'}
    request_headers.update(headers or {})
    if transport is not None:
        return transport(url, request_headers)
    _throttle(operator)
    request = urllib.request.Request(url, headers=request_headers, method='GET')
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=ssl_context()) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        status = 'throttled' if exc.code in (429, 503) else 'failed'
        raise ProviderError(f'{url} returned HTTP {exc.code}', status) from None
    except urllib.error.URLError as exc:
        if isinstance(getattr(exc, 'reason', None), ssl.SSLCertVerificationError):
            # Never work around this by disabling verification: an unverified fetch is an
            # unauthenticated one, and this repository's whole claim is that its evidence is what
            # the source actually published.
            raise ProviderError(
                f"{url}: the TLS certificate could not be verified against this machine's trust "
                'store. This is a local configuration problem, not a source failure. Fix it by '
                'installing certificates (`pip install truststore certifi`) or by pointing '
                'SSL_CERT_FILE at a CA bundle. Verification is never disabled.') from None
        raise ProviderError(f'{url} did not answer: {exc}') from None
    except (TimeoutError, OSError) as exc:
        raise ProviderError(f'{url} did not answer: {exc}') from None
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        return raw.decode('utf-8', errors='replace')


def get_json(url: str, **kwargs):
    text = http_get(url, **kwargs)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProviderError(f'{url} did not return JSON: {exc}') from None


def record(*, provider: str, tier: str, kind: str, native_id: str, title: str,
           asof: str | None, value=None, unit: str | None = None, currency: str | None = None,
           url: str | None = None, symbol: str | None = None, series_id: str | None = None,
           published: str | None = None, text: str | None = None, is_forecast: bool = False,
           extra: dict | None = None) -> dict:
    """Build one uniform record. Callers pass native values; this fixes the shape.

    `asof` is when the observation is true of, which is not when it was fetched. Confusing the two
    is how a three-month-old print gets treated as this morning's news.
    """
    if tier not in TIERS:
        raise ProviderError(f'{provider}: unknown tier {tier!r}')
    if kind not in KINDS:
        raise ProviderError(f'{provider}: unknown kind {kind!r}')
    return {
        'key': f'{provider}:{native_id}',
        'kind': kind,
        'provider': provider,
        'tier': tier,
        'symbol': symbol,
        'series_id': series_id,
        'title': title,
        'asof': asof,
        'value': value,
        'unit': unit,
        'currency': currency,
        'url': url,
        'published': published,
        'text_digest': digest_text(text) if text else None,
        'text': text,
        'fetched_at': now_utc(),
        'cache': False,
        'sources': [provider],
        'first_seen': today(),
        'raw_ref': None,
        'is_forecast': is_forecast,
        **(extra or {}),
    }


def require(args: dict, *names: str) -> tuple:
    missing = [name for name in names if not args.get(name)]
    if missing:
        raise ProviderError(f'missing required argument(s): {", ".join(missing)}')
    return tuple(args[name] for name in names)


def validate_identifier(value: str, what: str, *, allowed: str = '', maxlen: int = 64) -> str:
    """Identifiers are built into URLs, so they are validated, never trusted.

    Anything outside letters, digits and the caller's small allowance is refused rather than
    escaped: a provider that accepts an arbitrary string has handed URL construction to whoever
    wrote the string.
    """
    if not isinstance(value, str) or not value or len(value) > maxlen:
        raise ProviderError(f'{what}: expected 1-{maxlen} characters, got {value!r}')
    if not all(char.isalnum() or char in allowed for char in value):
        raise ProviderError(f'{what}: {value!r} contains characters this provider does not accept')
    return value


def parse_csv(text: str, *, delimiter: str = ',') -> list[list[str]]:
    import csv
    import io
    return [row for row in csv.reader(io.StringIO(text), delimiter=delimiter) if row]
