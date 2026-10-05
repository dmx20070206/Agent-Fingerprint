"""Render saved out-of-fold evaluation results without retraining."""
import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np


def plot_evaluation(report, output_dir, target):
    if report['status'] != 'completed':
        return []
    scores = report['metrics']
    labels = scores['labels']
    matrix = np.asarray(scores['confusion_matrix'], dtype=int)
    totals = matrix.sum(axis=1, keepdims=True)
    fractions = np.divide(matrix, totals, out=np.zeros_like(matrix, dtype=float), where=totals != 0)
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    with plt.rc_context({'font.size': 11, 'axes.spines.top': False, 'axes.spines.right': False,
                         'svg.fonttype': 'none'}):
        fig, (ax, bars) = plt.subplots(1, 2, figsize=(15, 6.5), layout='constrained')
        try:
            fig.suptitle(
                f'{target.upper()} classification | {report["folds"]}-fold out-of-fold evaluation\n'
                f'N = {int(matrix.sum())}    Accuracy = {scores["accuracy"]:.1%}    '
                f'Macro-F1 = {scores["macro_f1"]:.3f}', fontsize=17, fontweight='bold')
            heatmap = ax.imshow(fractions, vmin=0, vmax=1, cmap='Blues')
            ax.set_xticks(range(len(labels)), labels, rotation=30, ha='right')
            ax.set_yticks(range(len(labels)), labels)
            ax.set(xlabel='Predicted label', ylabel='True label', title='Confusion matrix: count / row percentage')
            for i in range(len(labels)):
                for j in range(len(labels)):
                    ax.text(j, i, f'{matrix[i, j]}\n{fractions[i, j]:.0%}',
                            ha='center', va='center', color='white' if fractions[i, j] > .55 else '#17253d')
            fig.colorbar(heatmap, ax=ax, shrink=.7, label='Fraction of true class')
            x = np.arange(len(labels))
            for offset, (key, name, color) in enumerate([
                ('precision', 'Precision', '#2563eb'), ('recall', 'Recall', '#0d9488'),
                ('f1-score', 'F1', '#d97706')]):
                values = [scores['per_class'][label][key] for label in labels]
                rectangles = bars.bar(x + (offset - 1) * .25, values, .25, label=name, color=color)
                bars.bar_label(rectangles, fmt='%.2f', fontsize=8, padding=3, rotation=90)
            bars.set_xticks(x, [f'{label}\n(n={int(totals[i, 0])})' for i, label in enumerate(labels)],
                            rotation=25, ha='right')
            bars.set(ylim=(0, 1.2), yticks=np.arange(0, 1.01, .2), ylabel='Score', title='Per-class performance')
            bars.set_axisbelow(True)
            bars.grid(axis='y', alpha=.2)
            bars.legend(loc='upper center', ncol=3, fontsize=9)
            paths = [destination / 'evaluation.png', destination / 'evaluation.svg']
            for path in paths:
                fig.savefig(path, dpi=180, facecolor='white')
            return paths
        finally:
            plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-dir', type=Path, required=True)
    args = parser.parse_args()
    report = json.loads((args.model_dir / 'evaluation.json').read_text(encoding='utf-8'))
    config = json.loads((args.model_dir / 'config.json').read_text(encoding='utf-8'))
    paths = plot_evaluation(report, args.model_dir, config['target'])
    if paths:
        print('\n'.join(str(path) for path in paths))
    else:
        print(f'Plot skipped: {report.get("reason", report["status"])}')


if __name__ == '__main__':
    main()
