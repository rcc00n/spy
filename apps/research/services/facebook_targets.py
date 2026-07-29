"""Strict source URLs and public-group header verification."""
import re
from urllib.parse import parse_qs, urlencode, urlsplit

RESERVED = {'me','friends','login','logout','checkpoint','search','watch','reel','reels',
            'marketplace','settings','home','notifications','gaming','stories','messages',
            'ads','help','policies','share','privacy','privacycenter','groups','pages','events'}
GROUP_PUBLIC = re.compile(r'^(?:Public group|Общедоступная группа|Открытая группа)(?:\s*[·•]|\s*$)', re.I | re.M)
GROUP_PRIVATE = re.compile(r'^(?:Private group|Закрытая группа|Частная группа)(?:\s*[·•]|\s*$)', re.I | re.M)


def canonical_source_url(value, kind=None):
    try:
        p = urlsplit(value.strip())
        if p.scheme != 'https' or p.hostname not in {'facebook.com','www.facebook.com','m.facebook.com'} or p.username or p.password or p.port not in (None,443):
            return ''
    except ValueError:
        return ''
    parts = p.path.strip('/').split('/')
    base = 'https://www.facebook.com'
    if len(parts) == 2 and parts[0] == 'groups' and re.fullmatch(r'[A-Za-z0-9._-]+', parts[1]):
        return f'{base}/groups/{parts[1]}/' if kind in (None,'group') else ''
    if kind == 'group':
        return ''
    if p.path == '/profile.php':
        ident = parse_qs(p.query).get('id', [''])[0]
        return base+'/profile.php?'+urlencode({'id':ident}) if ident.isdigit() else ''
    if len(parts) == 1 and re.fullmatch(r'[A-Za-z0-9._-]+',parts[0]) and parts[0].lower() not in RESERVED:
        return f'{base}/{parts[0]}/'
    return ''


def group_from_post(url):
    parts = urlsplit(url).path.strip('/').split('/')
    if len(parts) >= 4 and parts[0] == 'groups' and parts[2] in {'posts','permalink'}:
        return canonical_source_url('https://www.facebook.com/groups/'+parts[1]+'/', 'group')
    return ''


def public_group_header(page):
    # Exclude posts/feed and recommendations. Only inspect the header around h1.
    headers = page.locator('[role="main"] h1, [role="main"] [role="heading"][aria-level="1"]')
    text = headers.evaluate_all('''nodes => nodes.map(h => {
        let best = h.innerText || '';
        for(let n=h.parentElement,i=0;n && i<8;n=n.parentElement,i++) {
            if(n.querySelector('[role="feed"], [role="article"]')) break;
            const t=n.innerText||'';
            if(t.length>4000) break;
            best=t;
        }
        return best;
    }).join('\\n')''')
    return bool(GROUP_PUBLIC.search(text)) and not GROUP_PRIVATE.search(text)
