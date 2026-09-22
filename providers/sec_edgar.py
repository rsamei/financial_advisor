"""SEC EDGAR - company submissions, Form 4 insider transactions and 13F filing indexes.

Tier I (insider and institutional signal). Operator: sec. No key, but the SEC's access policy
requires a contact address in the User-Agent: this module refuses to run without `FA_CONTACT_EMAIL`
rather than sending an anonymous request.

    fetch({'cik': '0000320193', 'forms': '4', 'last': 20})
    fetch({'cik': '0000320193', 'accession': '0000320193-26-000012'})   # one Form 4, parsed

What this data can and cannot tell you (stated here because the number is seductive):

- A Form 4 is filed within two business days, so it is close to timely - but an insider sells for
  tax, diversification and divorce, and buys for a hundred reasons too. **A cluster of purchases by
  three or more insiders within 30 days is a weak positive signal**, and a single sale is close to
  no signal at all.
- 13F holdings are filed 45 days after quarter end. A 13F tells you what a fund held six weeks ago,
  not what it holds. It is history, and it is long-only US equity history at that.
- Neither may satisfy the primary-source condition for a claim about a company's fundamentals.
  They are evidence about *positioning*, and 05-market-intelligence.md limits them to the
  evidence-strength dimension.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from . import base

CONTRACT = {
    'name': 'sec_edgar',
    'operator': 'sec',
    'tier': 'I',
    'kinds': ('filing', 'insider_tx'),
    'argv': {'--cik': '10-digit CIK, zero padded',
             '--forms': 'comma-separated form types to keep, e.g. 4,13F-HR',
             '--accession': 'one accession number; fetches and parses that Form 4',
             '--last': 'filings to return (default 20)'},
    'endpoints': ['https://data.sec.gov/submissions/CIK{cik}.json',
                  'https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/'],
    'needs_env': ['FA_CONTACT_EMAIL (required)'],
}

SUBMISSIONS = 'https://data.sec.gov/submissions/CIK{cik}.json'
ARCHIVE = 'https://www.sec.gov/Archives/edgar/data/{cik}/{plain}/{accession}.txt'
CIK_RE = re.compile(r'\d{10}')
ACCESSION_RE = re.compile(r'\d{10}-\d{2}-\d{6}')
TRANSACTION_CODES = {'P': 'purchase', 'S': 'sale', 'A': 'award', 'M': 'option_exercise',
                     'G': 'gift', 'F': 'tax_withholding'}


def _headers():
    contact = base.contact_email()
    if not contact:
        raise base.ProviderError(
            'sec_edgar: FA_CONTACT_EMAIL is not set. The SEC requires a contact address in the '
            'User-Agent of every request; this provider will not send an anonymous one.')
    # No Host header here: urllib derives it from the URL. A fixed `data.sec.gov` was also sent to
    # www.sec.gov, which answered every archive request with HTTP 404.
    return {'User-Agent': base.user_agent(contact)}


def _cik(value: str) -> str:
    cik = str(value).strip().lstrip('CIKcik').zfill(10)
    if not CIK_RE.fullmatch(cik):
        raise base.ProviderError(f'sec_edgar --cik: {value!r} is not a CIK')
    return cik


def _submissions(cik: str, forms: set[str], last: int, transport) -> list[dict]:
    url = SUBMISSIONS.format(cik=cik)
    payload = base.get_json(url, operator='sec', headers=_headers(), transport=transport)
    recent = ((payload.get('filings') or {}).get('recent') or {})
    columns = ('accessionNumber', 'filingDate', 'reportDate', 'form', 'primaryDocument')
    if not all(isinstance(recent.get(column), list) for column in columns):
        raise base.ProviderError('sec_edgar: submissions response has no recent filings table')
    name = payload.get('name') or cik

    out = []
    for row in zip(*(recent[column] for column in columns)):
        accession, filed, reported, form, document = row
        if forms and form not in forms:
            continue
        out.append(base.record(
            provider='sec_edgar', tier='I', kind='filing',
            native_id=f'{cik}/{accession}', title=f'{name} {form}',
            asof=reported or filed, published=filed, url=_document_url(cik, accession, document),
            extra={'form': form, 'cik': cik, 'accession': accession, 'entity': name}))
        if len(out) >= last:
            break
    if not out:
        raise base.ProviderError(
            f'sec_edgar: no filings for CIK {cik}' + (f' of form(s) {",".join(sorted(forms))}'
                                                      if forms else ''))
    return out


def _document_url(cik: str, accession: str, document: str) -> str:
    """EDGAR document paths carry one directory level (xslF345X03/form4.xml), so `/` is allowed.

    `..` never is: a traversal in a filing's own metadata would otherwise build a URL pointing
    somewhere else entirely, from data we did not write.
    """
    if '..' in document or document.startswith('/'):
        raise base.ProviderError(f'sec_edgar: refusing document path {document!r}')
    plain = accession.replace('-', '')
    return (f'https://www.sec.gov/Archives/edgar/data/{int(cik)}/{plain}/'
            f'{base.validate_identifier(document, "sec document", allowed="._-/", maxlen=128)}')


def _form4(cik: str, accession: str, transport) -> list[dict]:
    """Parse one Form 4 into per-transaction records.

    Amounts and prices are taken from the filing itself; nothing is inferred. A transaction whose
    code is not in TRANSACTION_CODES keeps the raw code, because inventing a category for an
    unfamiliar code is how a gift becomes a "purchase".
    """
    plain = accession.replace('-', '')
    url = ARCHIVE.format(cik=int(cik), plain=plain, accession=accession)
    text = base.http_get(url, operator='sec', headers=_headers(), transport=transport)
    start, end = text.find('<ownershipDocument'), text.rfind('</ownershipDocument>')
    if start == -1 or end == -1:
        raise base.ProviderError(f'sec_edgar: {accession} contains no ownershipDocument XML')
    try:
        root = ET.fromstring(text[start:end + len('</ownershipDocument>')])
    except ET.ParseError as exc:
        raise base.ProviderError(f'sec_edgar: {accession} XML did not parse: {exc}') from None

    issuer = _text(root, './issuer/issuerName')
    symbol = _text(root, './issuer/issuerTradingSymbol')
    owner = _text(root, './reportingOwner/reportingOwnerId/rptOwnerName')
    out = []
    for index, node in enumerate(root.findall('./nonDerivativeTable/nonDerivativeTransaction')):
        code = _text(node, './transactionCoding/transactionCode')
        date = _text(node, './transactionDate/value')
        shares = _number(node, './transactionAmounts/transactionShares/value')
        price = _number(node, './transactionAmounts/transactionPricePerShare/value')
        out.append(base.record(
            provider='sec_edgar', tier='I', kind='insider_tx',
            native_id=f'{accession}#{index}',
            title=f'{owner or "insider"} {TRANSACTION_CODES.get(code, code)} {symbol or issuer}',
            symbol=symbol, asof=date, value=shares, unit='shares', url=url,
            extra={'insider': owner, 'issuer': issuer, 'transaction_code': code,
                   'transaction_kind': TRANSACTION_CODES.get(code, 'unknown'),
                   'price_per_share': price, 'accession': accession,
                   'acquired_disposed': _text(
                       node, './transactionAmounts/transactionAcquiredDisposedCode/value')}))
    if not out:
        raise base.ProviderError(f'sec_edgar: {accession} has no non-derivative transactions')
    return out


def _text(node, path):
    found = node.find(path)
    return found.text.strip() if found is not None and found.text else None


def _number(node, path):
    raw = _text(node, path)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def fetch(args: dict, transport=None) -> list[dict]:
    cik = _cik(*base.require(args, 'cik'))
    if args.get('accession'):
        accession = str(args['accession']).strip()
        if not ACCESSION_RE.fullmatch(accession):
            raise base.ProviderError(f'sec_edgar --accession: {accession!r} is not an accession '
                                     'number (0000000000-00-000000)')
        return _form4(cik, accession, transport)
    forms = {part.strip().upper() for part in str(args.get('forms') or '').split(',') if part.strip()}
    last = int(args.get('last') or 20)
    if last < 1 or last > 200:
        raise base.ProviderError('sec_edgar --last: expected 1-200')
    return _submissions(cik, forms, last, transport)
