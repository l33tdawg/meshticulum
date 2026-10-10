"""Validate local documentation links and editable Mermaid source consistency."""
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

ROOT=Path(__file__).resolve().parents[1]
DIAGRAM_DOCS=('README.md','docs/ARCHITECTURE.md','docs/WHY.md','docs/COMPARISON.md')


def headings(path):
    text=re.sub(r'```.*?```','',path.read_text(),flags=re.S)
    result=set();duplicates={}
    for title in re.findall(r'^#{1,6}\s+(.+?)\s*#*$',text,flags=re.M):
        title=re.sub(r'<[^>]+>','',title).lower()
        slug=re.sub(r'[^\w\s-]','',title).replace(' ','-')
        number=duplicates.get(slug,0);duplicates[slug]=number+1
        result.add(slug+('-'+str(number) if number else ''))
    return result


def main():
    errors=[];links=0;diagrams=0
    documents=[ROOT/'README.md',*sorted((ROOT/'docs').glob('*.md'))]
    for path in documents:
        text=re.sub(r'```.*?```','',path.read_text(),flags=re.S)
        for target in re.findall(r'!?\[[^\]]*\]\(([^)]+)\)',text):
            target=target.strip().strip('<>')
            parsed=urlsplit(target)
            if parsed.scheme or parsed.netloc:continue
            destination=(path.parent/unquote(parsed.path)).resolve() if parsed.path else path
            links+=1
            if not destination.is_relative_to(ROOT) or not destination.exists():
                errors.append(str(path.relative_to(ROOT))+': missing '+target)
            elif parsed.fragment and destination.suffix=='.md' and unquote(parsed.fragment) not in headings(destination):
                errors.append(str(path.relative_to(ROOT))+': missing anchor '+target)
    for name in DIAGRAM_DOCS:
        path=ROOT/name
        for index,source in enumerate(re.findall(r'```mermaid\s*\n(.*?)```',path.read_text(),flags=re.S),1):
            diagram=ROOT/'docs/diagrams'/(path.stem.lower()+'-'+str(index)+'.mmd')
            if not diagram.is_file() or diagram.read_text()!=source.rstrip()+'\n':errors.append('Stale Mermaid source: '+str(diagram.relative_to(ROOT)))
            if not diagram.with_suffix('.svg').is_file():errors.append('Missing rendered SVG: '+str(diagram.relative_to(ROOT)))
            diagrams+=1
    if errors:raise SystemExit('\n'.join(errors))
    print('Verified '+str(links)+' local links and '+str(diagrams)+' editable/rendered Mermaid diagrams.')


if __name__=='__main__':main()
