"""Plot every retained sample; no pooling, hardware access or invented metrics."""
import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parents[1]
    parser.add_argument('--data', type=Path, default=root / 'benchmarks/2026-10-09-radio/radio-results.json')
    parser.add_argument('--output', type=Path, default=root / 'docs/diagrams/radio-goodput.svg')
    args = parser.parse_args()
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    record = json.loads(args.data.read_text())
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':11,'svg.hashsalt':'meshticulum-radio-20261009'})
    fig, ax = plt.subplots(figsize=(10,4.8), constrained_layout=True)
    labels=[]
    for index,key in enumerate(('ca','cb','ab')):
        group=record['directions'][key]
        labels.append(group['direction'])
        values=[row['sender_acknowledged_goodput_Bps']/1000 for row in group['samples']]
        offsets=[(i-(len(values)-1)/2)*.029 for i in range(len(values))]
        ax.scatter(values,[index+o for o in offsets],s=40,color='#398487',alpha=.65,zorder=3)
        median=group['goodput_Bps']['median']/1000
        ax.plot([median,median],[index-.22,index+.22],color='#073c40',linewidth=3,zorder=4)
        ax.annotate(f'{median:.3f} kB/s median',(.03,index),ha='left',va='center',color='#073c40')
    ax.axvline(record['target_goodput_Bps']/1000,color='#bb6746',linestyle=(0,(5,3)),linewidth=1.5)
    ax.text(1.51,-.53,'1.5 kB/s target',ha='right',va='bottom',color='#9c4d2f')
    ax.set(xlim=(0,1.65),ylim=(2.55,-.55),yticks=range(3),yticklabels=labels,
           xlabel='Useful encrypted application goodput (decimal kB/s)')
    ax.set_title('Retained single-radio LoRa bench · 2026-10-09\n10 finite 8 KiB transfers per direction; 30/30 RF samples completed',loc='left',fontsize=14,pad=24)
    ax.xaxis.grid(True,color='#e2e9e8',linewidth=.7);ax.set_axisbelow(True)
    for edge in ('top','right','left'): ax.spines[edge].set_visible(False)
    ax.spines['bottom'].set_color('#bdcbca');ax.tick_params(axis='y',length=0,pad=12)
    ax.legend(handles=[Line2D([0],[0],marker='o',linestyle='none',color='#398487',label='Individual sample'),
                       Line2D([0],[0],color='#073c40',linewidth=3,label='Directional median')],
              loc='upper left',bbox_to_anchor=(0,-.22),frameon=False,ncol=2)
    fig.text(.065,-.07,'SF7 / BW500 / CR4/5 · sender start → flow ACK · receiver hash independently verified',fontsize=9,color='#536866')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    metadata={'Date':'2026-10-09','Title':'Meshticulum retained single-radio goodput',
              'Description':'All ten samples for each direction; measured medians about 1.2 kB/s, target 1.5 kB/s.'}
    fig.savefig(args.output,metadata=metadata,bbox_inches='tight')
    plt.close(fig)
    if args.output.suffix.lower()=='.svg':
        args.output.write_text('\n'.join(line.rstrip() for line in args.output.read_text().splitlines())+'\n')
    print(str(args.output))


if __name__=='__main__': main()
