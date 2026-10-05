"""Shared selection of website collections, agents and model labels."""
import re
from pathlib import Path


def parse_selection(value):
    if value is None:
        return None
    values = value.split(',') if isinstance(value, str) else value
    values = sorted({v.strip() for v in values if v.strip()})
    if not values:
        raise ValueError('Selection must contain a name or all')
    if 'all' in values:
        if len(values) != 1:
            raise ValueError('all cannot be combined with specific names')
        return None
    return values


def selections(sites=None, agents=None, llms=None):
    return {k: parse_selection(v) for k, v in
            [('sites', sites), ('agents', agents), ('llms', llms)]}


def add_selection_arguments(parser):
    for name in ('sites', 'agents', 'llms'):
        parser.add_argument('--' + name, help='Comma-separated names (default: all)')


def site_label(run_dir, agent, model):
    """Read the collection from the organized layout or a legacy run suffix."""
    run = Path(run_dir)
    if run.parent.name == model and run.parent.parent.name == agent:
        return run.parent.parent.parent.name
    match = re.search(r'_(flights|shop|forums?|keyboard|mouse_move|mouse_click|wheel_scroll)(?:_\d+)?$', run.name)
    return {'forums': 'forum'}.get(match[1], match[1]) if match else None


def matches(labels, selection):
    return all(values is None or labels.get(key) in values for key, values in selection.items())


def validate_selection(selection, available):
    for key, values in selection.items():
        unknown = set(values or []) - available[key]
        if unknown:
            raise ValueError(f'Unknown {key}: {", ".join(sorted(unknown))}; '
                             f'available: {", ".join(sorted(available[key])) or "none"}')
