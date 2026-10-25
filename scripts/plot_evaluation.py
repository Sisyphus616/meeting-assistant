"""Export standalone figures from measured summaries; no inference or scoring."""
import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np


def main(directory):
    summary = json.loads((directory / 'summary.json').read_text())
    if not summary['full_suite_finished']:
        raise SystemExit('Finish the complete experiment before exporting final figures')
    figures = directory / 'figures'
    figures.mkdir(exist_ok=True)
    plt.rcParams.update({'font.size': 10, 'axes.spines.top': False,
                         'axes.spines.right': False, 'svg.fonttype': 'none'})

    def save(fig, name):
        fig.savefig(figures / f'{name}.png', dpi=200, bbox_inches='tight', facecolor='white')
        fig.savefig(figures / f'{name}.svg', bbox_inches='tight', facecolor='white')
        svg = figures / f'{name}.svg'
        svg.write_text('\n'.join(line.rstrip() for line in svg.read_text().splitlines()) + '\n')
        plt.close(fig)

    groups = ['meeting', 'talk', 'meeting_noise']
    labels = ['Meeting\n(n=10)', 'Talk\n(n=10)', 'Meeting + noise\n(n=5 paired)']
    colors = ['.25', '.5', '.75']
    fig, axes = plt.subplots(1, 2, figsize=(9, 4))
    values = [summary['groups'][g]['quality']['rate'] * 100 for g in groups]
    axes[0].bar(labels, values, color=colors, edgecolor='black', linewidth=.5)
    axes[0].set_ylabel('Word error rate (%) — lower is better')
    axes[0].set_ylim(0, max(values) * 1.22)
    for i, value in enumerate(values):
        axes[0].text(i, value + .7, f'{value:.1f}%', ha='center')
    judging = summary['translation_judging']
    if judging['status'] == 'complete':
        scores = [judging['groups'][g]['mean'] for g in groups]
        axes[1].bar(labels, scores, color=colors, edgecolor='black', linewidth=.5)
        axes[1].set_ylabel('Translation rating / 5 — higher is better')
        axes[1].set_ylim(0, 5)
        for i, value in enumerate(scores):
            axes[1].text(i, value + .08, f'{value:.2f}', ha='center')
    else:
        axes[1].text(.5, .5, 'Translation judging incomplete', ha='center', transform=axes[1].transAxes)
    fig.suptitle('Real-model quality on fixed public recordings', y=1.02)
    chinese = summary['groups']['chinese_auxiliary']['quality']['rate']
    fig.text(.5, -.07, f'Chinese auxiliary CER: {chinese:.2%} (5 clips). Translation: equal-weight mean of 3 model judges.\n'
             '5 noise cases reuse 5 meeting recordings; groups are not independent populations.', ha='center', fontsize=8)
    fig.tight_layout()
    save(fig, 'quality')

    all_groups = groups + ['chinese_auxiliary']
    all_labels = labels + ['Chinese auxiliary\n(n=5)']
    medians = [summary['groups'][g]['first_asr_ms']['median'] / 1000 for g in all_groups]
    p95 = [summary['groups'][g]['first_asr_ms']['p95'] / 1000 for g in all_groups]
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.bar(all_labels, medians, color=['.25', '.45', '.65', '.85'], edgecolor='black', linewidth=.5, label='P50')
    ax.scatter(range(4), p95, marker='D', color='black', s=28, label='P95', zorder=3)
    ax.set_ylabel('Upload to first confirmed transcript (s)')
    ax.set_ylim(0, max(p95) * 1.25)
    for i, (median, tail) in enumerate(zip(medians, p95)):
        ax.text(i, median + .15, f'{median:.2f}', ha='center', fontsize=9)
        ax.text(i, tail + .3, f'{tail:.2f}', ha='center', fontsize=9)
    ax.legend(frameon=False)
    ax.set_title('Local CPU, model-prepared trials — 3 repeats per clip')
    cold = next(row for row in summary['cold'] if row['splitter'] == 'pause')
    fig.text(.5, -.03, f'Localhost HTTP/WebSocket; no browser upload network or AUP proxy.\n'
             f'Fresh-process model-cold first transcript: {cold["first_asr_ms"] / 1000:.2f}s (one separate trial; OS caches uncontrolled).',
             ha='center', fontsize=8)
    fig.tight_layout()
    save(fig, 'latency')

    comparisons = summary['comparison']
    if comparisons:
        fig, axes = plt.subplots(1, 2, figsize=(9, 4))
        x = np.arange(len(comparisons))
        for delta, splitter, color, label in [(-.18, 'pause', '.3', 'Pause-first'), (.18, 'fixed10', '.75', 'Fixed 10s')]:
            axes[0].bar(x + delta, [r[splitter]['wer'] * 100 for r in comparisons], .35,
                        color=color, edgecolor='black', linewidth=.5, label=label)
            axes[1].bar(x + delta, [r[splitter]['first_asr_ms']['median'] / 1000 for r in comparisons], .35,
                        color=color, edgecolor='black', linewidth=.5)
        for ax in axes:
            ax.set_xticks(x, [row['sample_id'] for row in comparisons], rotation=25)
        axes[0].set_ylabel('Word error rate (%)')
        axes[1].set_ylabel('First confirmed transcript P50 (s)')
        axes[0].legend(frameon=False)
        fig.suptitle('Segmentation comparison — five predefined talk recordings')
        fig.text(.5, -.03, 'Same model adapters and audio. Sequential method order; small sample, no significance claim.',
                 ha='center', fontsize=8)
        fig.tight_layout()
        save(fig, 'segmentation')
    (figures / 'provenance.json').write_text(json.dumps({'matplotlib': matplotlib.__version__,
        'source': '../summary.json', 'note': 'All values derived from measured summary; no simulated data.'}, indent=2))
    print(figures)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    main(parser.parse_args().directory)
