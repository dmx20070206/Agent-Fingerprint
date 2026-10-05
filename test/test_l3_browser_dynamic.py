from analysis.l3_browser_dynamic import format_l3


def _flatten(group):
    for key, value in group.items():
        if isinstance(value, dict):
            for stat, stat_value in value.items():
                yield f"{key}_{stat}", stat_value
        else:
            yield key, value


def test_format_l3_uses_plain_feature_names_and_complete_behavior_groups():
    result = format_l3({'run_id': 'test', 'events': []})

    assert len(result['features']) == result['feature_count'] == 98
    assert all(not name.startswith('rank_') for name in result['features'])
    assert result['feature_vector'] == list(result['features'].values())

    groups = [
        ('mouse_movement_behavior', 28),
        ('mouse_button_behavior', 10),
        ('keyboard_typing_behavior', 17),
        ('scroll_behavior', 12),
        ('episode_behavior', 31),
    ]
    grouped_features = {}
    for group_name, expected_count in groups:
        flattened = dict(_flatten(result[group_name]))
        assert len(flattened) == expected_count
        grouped_features.update(flattened)

    assert grouped_features == result['features']


def _event(event_type, time, **fields):
    return {'type': event_type, 'monotonic_ms': time, **fields}


def test_mouse_pointer_and_mouse_events_are_not_double_counted_or_joined_across_clicks():
    result = format_l3({'events': [
        _event('pointermove', 0, client_x=0, client_y=0),
        _event('pointermove', 10, client_x=10, client_y=0),
        _event('pointerdown', 20, button=0, client_x=10, client_y=0),
        _event('mousedown', 20.2, button=0, client_x=10, client_y=0),
        _event('pointerup', 30, button=0, client_x=10, client_y=0),
        _event('mouseup', 30.2, button=0, client_x=10, client_y=0),
        _event('click', 31),
        _event('pointermove', 40, client_x=100, client_y=0),
        _event('pointermove', 50, client_x=110, client_y=0),
    ]})

    assert result['features']['button0_down_up_ratio'] == 1
    assert result['features']['mouse_event_count'] == 7
    assert result['features']['mouse_curvature_distance_mean'] == 10


def test_backspace_count_uses_only_complete_keypresses():
    result = format_l3({'events': [
        _event('keydown', 0, key='A'),
        _event('keyup', 10, key='A'),
        _event('keydown', 20, key='Backspace'),
    ]})

    assert result['features']['backspace_delete_count'] == 0
    assert result['features']['backspace_delete_ratio'] == 0


def test_missing_is_null_and_duplicate_features_removed():
    import json
    from analysis.feature_schema import FEATURE_NAMES, SCHEMA
    result = format_l3({'events': []})
    assert result['schema'] == SCHEMA
    assert result['feature_names'] == list(FEATURE_NAMES)
    assert result['features']['hold_latency_mean'] is None
    assert result['features']['keypress_count'] == 0
    assert result['features']['mean_key_iei_ms'] is None
    assert result['features']['std_key_iei_ms'] is None
    json.dumps(result, allow_nan=False)


def test_turn_angles_do_not_join_segments():
    import pytest
    for boundary in [
        [_event('click', 15)],
        [_event('monitor_start', 15)],
        [_event('pagehide', 15)],
    ]:
        events = [_event('mousemove', 0, client_x=0, client_y=0),
                  _event('mousemove', 10, client_x=10, client_y=0), *boundary,
                  _event('mousemove', 20, client_x=100, client_y=100),
                  _event('mousemove', 30, client_x=100, client_y=110)]
        f = format_l3({'events': events})['features']
        assert f['mouse_curvature_angle_mean'] is None
        assert f['mouse_curvature_distance_mean'] == pytest.approx(10)
    events = [_event('mousemove', t, client_x=x, client_y=y)
              for t, x, y in [(0, 0, 0), (10, 10, 0), (300, 100, 100), (310, 100, 110)]]
    assert format_l3({'events': events})['features']['mouse_curvature_angle_mean'] is None


def test_sessions_do_not_pair_keys_or_scroll_or_mouse():
    events = [
        _event('keydown', 0, key='a', session_id='a'),
        _event('scroll', 1, scroll_x=0, scroll_y=10, session_id='a'),
        _event('mousemove', 2, client_x=0, client_y=0, session_id='a'),
        _event('keyup', 3, key='a', session_id='b'),
        _event('scroll', 4, scroll_x=0, scroll_y=100, session_id='b'),
        _event('mousemove', 5, client_x=100, client_y=100, session_id='b'),
    ]
    f = format_l3({'events': events})['features']
    assert f['hold_latency_mean'] is None
    assert f['dangling_keyup'] == f['dangling_keydown'] == 1
    assert f['scroll_distance_mean'] is None
    assert f['mouse_curvature_distance_mean'] is None


def test_clock_reset_and_missing_viewport():
    f = format_l3({'events': [_event('keydown', 100, key='a'),
                               _event('keyup', 10, key='a'),
                               _event('click', 20, client_x=10, client_y=20)]})['features']
    assert f['hold_latency_mean'] is None
    assert f['click_bbox_area_frac'] is None


def test_genuine_adjacent_mouse_moves_retained_and_negative_angle_valid():
    import math
    import pytest
    f = format_l3({'events': [_event('mousemove', 0, client_x=0, client_y=0),
                             _event('mousemove', 1, client_x=math.cos(-1), client_y=math.sin(-1))]})['features']
    assert f['mousemove_count'] == 2
    assert f['mouse_direction_mean'] == pytest.approx(-1)


def test_discrete_moves_cross_clicks_waits_and_scroll_but_not_continuous_paths():
    import math
    import pytest
    f = format_l3({'events': [
        _event('pointermove', 0, client_x=0, client_y=0),
        _event('mousemove', 1, client_x=0, client_y=0),
        _event('click', 2),
        _event('mousemove', 1000, client_x=3, client_y=0),
        _event('scroll', 1500, scroll_y=100),
        _event('mousemove', 2000, client_x=3, client_y=4),
    ]})['features']
    assert f['mousemove_count'] == 3
    assert f['mouse_transition_distance_mean'] == 3.5
    assert f['mouse_transition_distance_std'] == 0.5
    assert f['mouse_transition_direction_mean'] == pytest.approx(math.pi / 4)
    assert f['mouse_transition_direction_std'] == pytest.approx(math.pi / 4)
    assert f['mouse_transition_turn_angle_mean'] == pytest.approx(math.pi / 2)
    assert f['mouse_transition_turn_angle_std'] is None
    assert f['mouse_transition_interval_mean'] == 1000
    assert f['mouse_transition_interval_std'] == 0
    assert f['mouse_isolated_move_ratio'] == 1
    assert f['mouse_curvature_distance_mean'] is None
    assert f['mouse_curvature_angle_mean'] is None


def test_discrete_boundaries_preserve_original_order():
    for boundary in ('monitor_start', 'navigate', 'navigation', 'beforeunload', 'pagehide',
                     'session_change', 'clock_reset'):
        events = [_event('mousemove', 100, client_x=0, client_y=0, session_id='a')]
        if boundary not in ('session_change', 'clock_reset'):
            events.append(_event(boundary, 110, session_id='a'))
        events.append(_event('mousemove', 0 if boundary == 'clock_reset' else 120,
                             client_x=10, client_y=0,
                             session_id='b' if boundary == 'session_change' else 'a'))
        f = format_l3({'events': events})['features']
        assert f['mouse_transition_distance_mean'] is None
        assert f['mouse_transition_interval_mean'] is None
        assert f['mouse_isolated_move_ratio'] == 1


def test_stationary_invalid_and_untimed_moves():
    f = format_l3({'events': [
        _event('mousemove', 0, client_x=0, client_y=0),
        _event('mousemove', 10, client_x=1, client_y=0),
        _event('mousemove', 20, client_x=1, client_y=0),
        _event('mousemove', 30, client_x=float('nan'), client_y=0),
        _event('mousemove', None, client_x=1, client_y=1),
    ]})['features']
    assert f['mouse_transition_distance_mean'] == 2 / 3
    assert f['mouse_transition_interval_mean'] == 10
    assert f['mouse_transition_turn_angle_mean'] is None
    assert f['mouse_isolated_move_ratio'] == 0.5


def test_no_moves_do_not_use_click_coordinates():
    f = format_l3({'events': [_event('click', 0, client_x=0, client_y=0),
                             _event('click', 100, client_x=100, client_y=100)]})['features']
    assert all(value is None for name, value in f.items()
               if name.startswith('mouse_transition_') or name == 'mouse_isolated_move_ratio')


def test_discrete_direction_unwrap_and_continuous_membership():
    import math
    import pytest
    events = [_event('mousemove', t, client_x=x, client_y=y)
              for t, x, y in [(0, 0, 0), (250, -1, 0.01), (500, -2, 0), (1000, -3, 0)]]
    f = format_l3({'events': events})['features']
    assert f['mouse_transition_direction_mean'] == pytest.approx(math.pi)
    assert f['mouse_transition_direction_std'] < 0.02
    assert f['mouse_isolated_move_ratio'] == 0.25


def test_episode_volume_timing_and_scroll_depth():
    import pytest
    events = [
        _event('monitor_start', 0, epoch_ms=1000, url='https://a.test/', session_id='a', navigation_tracking=True),
        _event('click', 100, epoch_ms=1100, url='https://a.test/', client_y=100, target={'tag': 'a', 'href': '/next'}),
        _event('keydown', 200, epoch_ms=1200, key='a'),
        _event('keydown', 300, epoch_ms=1300, key='b'),
        _event('keydown', 500, epoch_ms=1500, key='c'),
        _event('focus', 600, epoch_ms=1600, target={'tag': 'input'}),
        _event('focus', 700, epoch_ms=1700, target={'tag': 'button'}),
        _event('scroll', 800, epoch_ms=1800, y=700, document_height=1500, viewport_height=500),
        _event('scroll', 900, epoch_ms=1900, scroll_pct=60),
        _event('beforeunload', 1000, epoch_ms=2000),
        _event('monitor_start', 0, epoch_ms=2100, url='https://b.test/', session_id='b'),
        _event('navigation', 200, epoch_ms=2300, url='https://b.test/next', reason='popstate', session_id='b'),
        _event('monitor_stop', 300, epoch_ms=2400, session_id='b'),
    ]
    f = format_l3({'events': events}, {'started_at': '1970-01-01T00:00:00+00:00',
                                     'finished_at': '1970-01-01T00:00:03+00:00'})['features']
    assert f['n_events_total'] == 13
    assert f['n_clicks'] == f['n_focus'] == f['n_link_clicks'] == 1
    assert f['page_count'] == 3 and f['n_unique_domains'] == 2
    assert f['n_navigations'] == 2 and f['popstate_ratio'] == .5
    assert f['total_duration_s'] == 3 and f['t_first_action_ms'] == 1000
    assert f['mean_iei_ms'] == pytest.approx(1400 / 12)
    assert f['p10_iei_ms'] == f['median_iei_ms'] == 100
    assert f['p90_iei_ms'] == pytest.approx(190)
    assert f['mean_key_iei_ms'] == 150 and f['std_key_iei_ms'] == 50
    assert f['inter_key_latency_mean'] is None  # no keyups, different metric
    assert f['mean_nav_iei_ms'] == 200 and f['std_nav_iei_ms'] is None
    assert f['max_page_dwell_ms'] == 1000
    assert f['max_scroll_pct'] == 70 and f['mean_scroll_pct'] == 65
    assert f['n_deep_scrolls'] == 1 and f['click_top_frac'] == 1
    assert f['scroll_to_click_ratio'] == 2
    assert f['actions_per_page'] == 13 / 3
    assert f['keydowns_per_page'] == 1 and f['focus_per_page'] == 1 / 3


def test_episode_missing_metadata_and_cross_document_clocks():
    f = format_l3({'events': [
        _event('click', 100, session_id='a', target={'tag': 'a'}),
        _event('click', 500, session_id='b'),
        _event('scroll', 600, session_id='b'),
    ]})['features']
    assert f['mean_click_iei_ms'] is None
    assert f['mean_iei_ms'] == 100
    for name in ('page_count', 'actions_per_page', 'total_duration_s',
                 't_first_action_ms', 'popstate_ratio', 'n_link_clicks',
                 'max_scroll_pct', 'n_deep_scrolls'):
        assert f[name] is None


def test_episode_iei_trend_and_click_statistics():
    f = format_l3({'events': [_event('click', t, client_y=y) for t, y in
                             [(0, 0), (100, 191), (200, 192), (500, 300), (800, 10)]]})['features']
    assert f['iei_trend'] == 3
    assert f['mean_click_iei_ms'] == 200
    assert f['std_click_iei_ms'] == 100
    assert f['click_top_frac'] == .6


def test_key_interval_does_not_bridge_hidden_local_clock_reset():
    f = format_l3({'events': [
        _event('keydown', 100, key='a'),
        _event('scroll', 0),
        _event('keydown', 200, key='b'),
    ]})['features']
    assert f['mean_key_iei_ms'] is None


def test_run_metadata_loaded_beside_raw_fingerprints(tmp_path):
    import json
    from analysis.episode_features import load_run_metadata
    source = tmp_path / 'fingerprints' / 'l3_browser_dynamic.json'
    source.parent.mkdir()
    metadata = {'started_at': '2026-10-05T00:00:00+00:00',
                'finished_at': '2026-10-05T00:00:10+00:00'}
    (tmp_path / 'manifest.json').write_text(json.dumps(metadata))
    assert format_l3({'events': []}, load_run_metadata(source))['features']['total_duration_s'] == 10
