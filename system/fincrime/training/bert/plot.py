"""Plot observed training/validation histories; rerun while training to refresh."""
import argparse
import json
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--log', default='dist/modernbert-hybrid.log')
    parser.add_argument('--output', default='dist/modernbert-hybrid/curves.png')
    args = parser.parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault('MPLCONFIGDIR', str(output.parent / '.matplotlib'))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    events = []
    for line in Path(args.log).read_text().splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            pass
    train = [e for e in events if e.get('event') == 'train']
    validation = [e for e in events if e.get('event') == 'validation']
    if not train:
        raise SystemExit('No training updates yet')
    rolling = [sum(e['loss'] for e in train[max(0, i - 9):i + 1]) / min(i + 1, 10)
               for i in range(len(train))]
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2), layout='constrained')
    steps = [e['step'] for e in train]
    axes[0].plot(steps, [e['loss'] for e in train], color='#7288a5', alpha=.3, label='Train: individual update')
    axes[0].plot(steps, rolling, color='#2563eb', label='Train: 10-update mean')
    axes[0].plot([e['step'] for e in validation], [e['loss'] for e in validation], 'o-', color='#ea580c', label='Validation: fixed 120 cases')
    axes[0].set(ylabel='Cross-entropy loss', title='Training versus held-out loss')
    for key, label in [('accuracy', 'Accuracy'), ('macro_f1', 'Macro-F1')]:
        axes[1].plot([e['step'] for e in validation], [e[key] for e in validation], 'o-', label=label)
    axes[1].axhline(1/3, color='gray', linestyle=':', label='Balanced accuracy baseline')
    axes[1].set(ylim=(0, 1), ylabel='Score', title='Validation on 120 stratified cases')
    for ax in axes:
        ax.set_xlabel('Optimizer update (effective batch 16)')
        ax.grid(alpha=.15)
        ax.legend(fontsize=8)
    fig.suptitle('ModernBERT NF4 QLoRA + numeric MLP • provisional labels', fontsize=12)
    fig.savefig(output, dpi=160)
    plt.close(fig)
    print(output.resolve())


if __name__ == '__main__':
    main()
