# SPDX-License-Identifier: Apache-2.0
"""Check the static Pages payload without opening a browser or making requests."""
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib.parse import urlsplit, unquote
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / 'website'


class Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ids, self.links, self.assets = [], [], []
        self.h1 = 0
        self.language = self.viewport = False

    def handle_starttag(self, tag, values):
        a = dict(values)
        if 'id' in a:
            self.ids.append(a['id'])
        if tag == 'html':
            self.language = a.get('lang') == 'en'
        if tag == 'meta' and a.get('name') == 'viewport':
            self.viewport = 'width=device-width' in a.get('content', '')
        if tag == 'h1':
            self.h1 += 1
        if tag == 'a' and 'href' in a:
            self.links.append(a['href'])
        if tag == 'img':
            assert 'alt' in a, 'image requires alt text or an explicit decorative empty alt'
        if tag in ('img', 'script') and 'src' in a:
            self.assets.append(a['src'])
        if tag == 'source' and 'srcset' in a:
            self.assets.extend(item.strip().split()[0] for item in a['srcset'].split(','))
        if tag == 'link' and a.get('rel') in ('stylesheet', 'icon'):
            self.assets.append(a['href'])


def main():
    files = sorted(p for p in SITE.rglob('*') if p.is_file())
    assert files and all(not p.is_symlink() for p in SITE.rglob('*')), 'unsafe Pages payload'
    pages = {}
    for path in sorted(SITE.rglob('*.html')):
        page = Page()
        page.feed(path.read_text())
        assert page.h1 == 1 and page.language and page.viewport, f'{path.name}: page structure incomplete'
        assert len(page.ids) == len(set(page.ids)), f'{path.name}: duplicate page anchor'
        pages[path.resolve()] = page
    assert (SITE / 'index.html').resolve() in pages, 'missing project landing page'
    for source, page in pages.items():
        for link in page.links:
            u = urlsplit(link)
            assert not u.scheme or u.scheme == 'https', 'unexpected link scheme'
            if not u.scheme:
                assert not u.netloc and not u.path.startswith('/'), 'link must work under /meshticulum/'
                destination = (source.parent / unquote(u.path)).resolve() if u.path else source
                assert destination.is_relative_to(SITE) and destination.is_file(), 'missing or escaping local destination'
                if u.fragment:
                    assert destination in pages and u.fragment in pages[destination].ids, 'missing page anchor'
            prefix = 'https://github.com/l33tdawg/meshticulum/blob/main/'
            if link.startswith(prefix):
                relative = unquote(u.path.split('/blob/main/', 1)[1])
                assert (ROOT / relative).is_file(), 'GitHub source link refers to a missing project file'
        for asset in page.assets:
            u = urlsplit(asset)
            assert not u.scheme and not u.netloc and not u.path.startswith('/'), 'asset must be local and relative'
            path = (source.parent / unquote(u.path)).resolve()
            assert path.is_relative_to(SITE) and path.is_file(), 'missing or escaping asset'
    svg = 0
    for path in files:
        if path.suffix == '.svg':
            tree = ET.fromstring(path.read_bytes())
            assert tree.tag == '{http://www.w3.org/2000/svg}svg' and tree.get('viewBox'), 'invalid scalable diagram'
            svg += 1
        if path.suffix in ('.html', '.css', '.js', '.svg', '.md'):
            raw = path.read_text()
            assert not any(x in raw for x in ('/Users/', '192.168.50.', '/root/esp-venv', 'BEGIN PRIVATE KEY')), 'private deployment content'
            assert not re.search(r'https?://[^\s"\']+\.(?:js|css)(?:[?"\']|$)', raw), 'remote runtime dependency'
    assert svg >= 3, 'logo and architecture diagrams required'
    print(json.dumps(dict(website_files=len(files), pages=len(pages), links=sum(len(p.links) for p in pages.values()),
                          local_assets=sum(len(p.assets) for p in pages.values()), scalable_figures=svg, network_requests=False)))


if __name__ == '__main__':
    main()
