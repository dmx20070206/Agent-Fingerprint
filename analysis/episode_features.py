"""Episode-level additions to L3; missing observations remain None."""
from datetime import datetime
from urllib.parse import urlsplit




def load_run_metadata(source):
    from pathlib import Path
    import json
    source = Path(source)
    root = source.parent.parent if source.parent.name == 'fingerprints' else source.parent
    path = root / 'manifest.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}


def episode_features(raw, events, metadata, num, stat, ratio):
    def epoch(value):
        if not isinstance(value, str):
            return None
        try:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
            return parsed.timestamp() * 1000 if parsed.tzinfo is not None else None
        except (ValueError, OverflowError):
            return None

    local_blocks = {}
    block, previous_session, previous_time = 0, None, None
    for event in events:
        session, time = event.get('session_id'), num(event, 'monotonic_ms')
        if (session != previous_session or event.get('type') == 'monitor_start'
                or (time is not None and previous_time is not None and time < previous_time)):
            block += 1
        local_blocks[id(event)] = block
        previous_session, previous_time = session, time

    def intervals(items):
        # Never subtract document-local clocks across sessions or clock resets.
        result = []
        for a, b in zip(items, items[1:]):
            ta, tb = num(a, 'epoch_ms'), num(b, 'epoch_ms')
            if ta is None or tb is None:
                if (a.get('session_id') != b.get('session_id')
                        or local_blocks.get(id(a)) != local_blocks.get(id(b))):
                    continue
                ta, tb = num(a, 'monotonic_ms'), num(b, 'monotonic_ms')
            if ta is not None and tb is not None and tb >= ta:
                result.append(tb - ta)
        return result

    def percentile(values, fraction):
        if not values:
            return None
        ordered = sorted(values)
        position = (len(ordered) - 1) * fraction
        lower = int(position)
        upper = min(lower + 1, len(ordered) - 1)
        return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)

    def depth(event):
        pct = num(event, 'scroll_pct')
        if pct is not None:
            return max(0, min(100, pct))
        y = next((num(event, k) for k in ('scroll_y', 'scrollY', 'y')
                  if num(event, k) is not None), None)
        height, viewport = num(event, 'document_height'), num(event, 'viewport_height')
        if y is None or height is None or viewport is None or height <= viewport:
            return None
        return max(0, min(100, 100 * y / (height - viewport)))

    clicks = [e for e in events if e.get('type') == 'click']
    keys = [e for e in events if e.get('type') == 'keydown']
    scrolls = [e for e in events if e.get('type') == 'scroll']
    focus = sum(e.get('type') == 'focus' and (e.get('target') or {}).get('tag')
                in ('input', 'textarea') for e in events)
    urls = {e['url'] for e in events if isinstance(e.get('url'), str) and e['url']}
    domains = set()
    for url in urls:
        try:
            host = urlsplit(url).hostname
            if host:
                domains.add(host)
        except ValueError:
            pass
    pages = len(urls) if urls or not events else None
    # Initial document entry is not a jump. Later document starts and explicit
    # same-document navigation events are jumps; unload is never another jump.
    navigations, entries = [], []
    seen_sessions = set()
    for e in events:
        kind = e.get('type')
        if kind == 'monitor_start':
            identity = e.get('session_id')
            if identity is not None and identity in seen_sessions:
                continue
            seen_sessions.add(identity)
            if entries:
                navigations.append(e)
            entries.append(e)
        elif kind in ('navigate', 'navigation', 'popstate'):
            navigations.append(e)
            entries.append(e)
    nav_count = len(navigations)
    # popstate coverage is explicitly advertised by new probes; old absence
    # cannot establish that the agent never used browser history.
    history_observed = any(e.get('navigation_tracking') or e.get('type') == 'popstate'
                           or e.get('reason') == 'popstate' for e in events)
    start = epoch(raw.get('started_at'))
    end = epoch(raw.get('finished_at'))
    if start is None:
        start = epoch(metadata.get('started_at'))
    if end is None:
        end = epoch(metadata.get('finished_at'))
    duration = (end - start) / 1000 if start is not None and end is not None and end >= start else None
    timestamps = [num(e, 'epoch_ms') for e in events]
    timestamps = [t for t in timestamps if t is not None]
    first = min(timestamps) - start if timestamps and start is not None else None
    if first is not None and first < 0:
        first = None
    iei = intervals(events)
    half = len(iei) // 2
    trend = ratio(stat(iei[half:], 'mean'), stat(iei[:half], 'mean')) if half else None
    depths = [depth(e) for e in scrolls]
    depths = [d for d in depths if d is not None]
    ys = [num(e, 'client_y') for e in clicks]
    ys = [y for y in ys if y is not None]
    # Require href evidence rather than assuming every anchor is a link.
    links = sum((e.get('target') or {}).get('tag') == 'a' and
                (e.get('target') or {}).get('href') is not None for e in clicks)
    # Measured page visits end at the next entry, unload, monitor_stop, or run
    # finish. Incomplete visits are excluded rather than assigned zero dwell.
    entry_ids = {id(e) for e in entries}
    dwells, current = [], None
    for e in events:
        if id(e) in entry_ids:
            if current is not None:
                dwells.extend(intervals([current, e]))
            current = e
        if e.get('type') in ('beforeunload', 'pagehide', 'monitor_stop') and current is not None:
            dwells.extend(intervals([current, e]))
            current = None
    if current is not None and end is not None:
        dwells.extend(intervals([current, {'epoch_ms': end}]))
    result = dict(
        n_clicks=len(clicks), n_navigations=nav_count, n_focus=focus,
        n_events_total=len(events), page_count=pages,
        n_unique_domains=len(domains) if urls or not events else None,
        total_duration_s=duration, t_first_action_ms=first,
        mean_iei_ms=stat(iei, 'mean'), std_iei_ms=stat(iei, 'std'),
        median_iei_ms=stat(iei, 'median'), p10_iei_ms=percentile(iei, .1),
        p90_iei_ms=percentile(iei, .9), iei_trend=trend,
        max_page_dwell_ms=max(dwells) if dwells else None,
        max_scroll_pct=max(depths) if depths else None,
        mean_scroll_pct=stat(depths, 'mean'),
        n_deep_scrolls=sum(d > 60 for d in depths) if depths or not scrolls else None,
        click_top_frac=ratio(sum(y < 192 for y in ys), len(ys)),
        n_link_clicks=(None if any((e.get('target') or {}).get('tag') == 'a'
                                   and 'href' not in (e.get('target') or {}) for e in clicks)
                       else links),
        popstate_ratio=ratio(sum(e.get('type') == 'popstate' or e.get('reason') == 'popstate'
                                for e in navigations), nav_count) if history_observed else None,
        scroll_to_click_ratio=ratio(len(scrolls), len(clicks)),
        actions_per_page=ratio(len(events), pages),
        keydowns_per_page=ratio(len(keys), pages), focus_per_page=ratio(focus, pages),
    )
    for name, items in [('click', clicks), ('nav', navigations), ('key', keys)]:
        values = intervals(items)
        for op in ('mean', 'std'):
            result[f'{op}_{name}_iei_ms'] = stat(values, op)
    return result
