"""RSS and Atom feeds from an allowlisted table - central banks, regulators, official journals.

Tier P (political, regulatory, geopolitical). Operator: whoever publishes the feed, which is why
FEEDS names one per entry - two feeds from the same institution are not two independent sources.

    fetch({'feed': 'ecb_press'})
    fetch({'feed': 'bancaditalia', 'last': 10})

**The feed table is a hard-coded allowlist.** An arbitrary `--url` is not accepted, and a link
found inside an item is never followed. A news item is a `news` record: it is evidence that
something was *reported*, and it never satisfies the primary-source condition for the fact it
reports. The underlying statement from the institution does.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from . import base

# name -> (url, operator, label, verified_on). Adding a feed here is the review step, and the
# date is part of the entry: an allowlist of URLs nobody has fetched is an allowlist of 404s.
# Every entry below was fetched and confirmed to return items on its verified_on date.
FEEDS = {
    'ecb_press': ('https://www.ecb.europa.eu/rss/press.html', 'ecb',
                  'ECB press releases', '2026-09-20'),
    'ecb_publications': ('https://www.ecb.europa.eu/rss/pub.html', 'ecb',
                         'ECB publications', '2026-09-20'),
    'ecb_fx_usd': ('https://www.ecb.europa.eu/rss/fxref-usd.html', 'ecb',
                   'ECB euro reference rate, USD', '2026-09-20'),
    'fed_press': ('https://www.federalreserve.gov/feeds/press_all.xml', 'federalreserve',
                  'Federal Reserve press releases', '2026-09-20'),
    'fed_speeches': ('https://www.federalreserve.gov/feeds/speeches.xml', 'federalreserve',
                     'Federal Reserve speeches', '2026-09-20'),
    'esma': ('https://www.esma.europa.eu/rss.xml', 'esma',
             'European Securities and Markets Authority', '2026-09-20'),
    'eba': ('https://www.eba.europa.eu/rss.xml', 'eba',
            'European Banking Authority', '2026-09-20'),
}

# What the verification on 2026-09-20 also showed, recorded because a caller reading only the
# table above would draw the wrong conclusion from an empty result:
FEED_NOTES = {
    'esma': 'items carry no publication date, so every record has asof: null and fails the '
            'freshness condition c2. Usable for discovery, not for a dated claim.',
    'eba': 'the feed itself is dormant - its newest item was published in June 2024. Records from '
           'it are genuinely, not spuriously, stale.',
}

# Deliberately NOT shipped, as of 2026-09-20: Banca d'Italia, MEF, CONSOB and Agenzia delle
# Entrate. Every plausible RSS path for those four answered 404 when probed, and guessing a URL
# into an allowlist is how a source becomes permanently "failed" without anyone noticing. The
# consequence is real and is stated in 05-market-intelligence.md: tier P currently covers
# euro-area and US institutions, and Italian fiscal announcements must be read by hand until a
# working feed (or an official API) is found and verified here with its date.

CONTRACT = {
    'name': 'rss',
    'operator': 'varies-per-feed',
    'tier': 'P',
    'kinds': ('news', 'event'),
    'argv': {'--feed': 'one of ' + ', '.join(sorted(FEEDS)), '--last': 'items to return'},
    'endpoints': sorted(url for url, _, _, _ in FEEDS.values()),
    'needs_env': [],
}

TAG = re.compile(r'\{[^}]*\}')
STRIP_TAGS = re.compile(r'<[^>]+>')


def operator_for(args: dict) -> str:
    """The registry asks this so two feeds from one institution count as one source."""
    return operator_of(str(args.get('feed') or '').strip().lower())


def operator_of(feed: str) -> str:
    if feed not in FEEDS:
        raise base.ProviderError(f'rss --feed: {feed!r} is not in the allowlist')
    return FEEDS[feed][1]


def _tag(element) -> str:
    return TAG.sub('', element.tag).lower()


def _first(node, *names):
    for child in node:
        if _tag(child) in names:
            return (child.text or '').strip() or (child.attrib.get('href') or '').strip() or None
    return None


def _items(root):
    """RSS <item> and Atom <entry>, without caring which one the publisher chose."""
    for element in root.iter():
        if _tag(element) in ('item', 'entry'):
            yield element


def fetch(args: dict, transport=None) -> list[dict]:
    feed, = base.require(args, 'feed')
    feed = str(feed).strip().lower()
    operator = operator_of(feed)
    url, _, label, _verified = FEEDS[feed]
    last = int(args.get('last') or 15)
    if last < 1 or last > 100:
        raise base.ProviderError('rss --last: expected 1-100')

    text = base.http_get(url, operator=operator, transport=transport)
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise base.ProviderError(f'rss: {feed} did not return parseable XML: {exc}') from None

    out = []
    for item in _items(root):
        title = _first(item, 'title')
        if not title:
            continue
        summary = _first(item, 'description', 'summary', 'content')
        out.append(base.record(
            provider='rss', tier='P', kind='news',
            native_id=f'{feed}:{base.digest_text(title)[:16]}',
            title=STRIP_TAGS.sub('', title).strip(),
            asof=_date(_first(item, 'pubdate', 'published', 'updated', 'date')),
            published=_first(item, 'pubdate', 'published', 'updated', 'date'),
            url=_first(item, 'link'), text=STRIP_TAGS.sub('', summary).strip() if summary else None,
            extra={'feed': feed, 'operator': operator, 'publisher': label}))
        if len(out) >= last:
            break
    if not out:
        raise base.ProviderError(f'rss: {feed} returned no items')
    return out


def _date(raw):
    """Return an ISO date, or None. A feed date this cannot parse is unknown, never today."""
    if not raw:
        return None
    import email.utils
    parsed = email.utils.parsedate_to_datetime(raw) if ',' in raw else None
    if parsed:
        return parsed.date().isoformat()
    match = re.match(r'(\d{4}-\d{2}-\d{2})', raw.strip())
    return match.group(1) if match else None
