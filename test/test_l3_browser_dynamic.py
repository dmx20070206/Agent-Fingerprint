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

    assert len(result['features']) == result['feature_count'] == 50
    assert all(not name.startswith('rank_') for name in result['features'])
    assert result['feature_vector'] == list(result['features'].values())

    groups = [
        ('mouse_movement_behavior', 14),
        ('mouse_button_behavior', 10),
        ('keyboard_typing_behavior', 16),
        ('scroll_behavior', 10),
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
