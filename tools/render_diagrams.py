"""Render editable Mermaid sources and SVGs from the architecture documents."""
import argparse
from pathlib import Path
import re
import subprocess

DOCUMENTS=('README.md','docs/ARCHITECTURE.md','docs/WHY.md','docs/COMPARISON.md')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output-dir',type=Path)
    parser.add_argument('--puppeteer-config',type=Path)
    parser.add_argument('--mermaid-config',type=Path)
    args=parser.parse_args();root=args.root.resolve()
    output=args.output_dir or root/'docs/diagrams';output.mkdir(parents=True,exist_ok=True)
    count=0
    for name in DOCUMENTS:
        path=root/name
        blocks=re.findall(r'```mermaid\s*\n(.*?)```',path.read_text(),flags=re.S)
        for index,source in enumerate(blocks,1):
            prefix=output/(path.stem.lower()+'-'+str(index))
            prefix.with_suffix('.mmd').write_text(source.rstrip()+'\n')
            cmd=['mmdc','--input',str(prefix.with_suffix('.mmd')),'--output',str(prefix.with_suffix('.svg')),'--quiet']
            if args.puppeteer_config:cmd+=['--puppeteerConfigFile',str(args.puppeteer_config)]
            config=args.mermaid_config or root/'docs/diagrams/config.json'
            if config.is_file():cmd+=['--configFile',str(config)]
            subprocess.run(cmd,check=True,timeout=30)
            count+=1
    print('Rendered '+str(count)+' Mermaid diagrams.')


if __name__=='__main__':main()
