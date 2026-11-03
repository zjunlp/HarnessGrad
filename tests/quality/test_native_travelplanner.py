"""Native six-table TravelPlanner controls; synthetic instrument, no model call."""
import copy
import json
from pathlib import Path
import sys

import pandas as pd
import pytest

from data.scaffolds.native_research.native import activate
from data.scaffolds.native_research.adapter import Adapter
from tools.prepare_native_research import prepare

activate()
from flashrsi.integrations._travelplanner_grade import check_database, score as official


@pytest.fixture
def planning(tmp_path):
    database = tmp_path / 'database'
    tables = {
        'flights/clean_Flights_2022.csv': [
            {'Flight Number': 'F1', 'Price': 10, 'DepTime': '09:00', 'ArrTime': '10:00',
             'ActualElapsedTime': 60, 'FlightDate': '2022-01-01', 'OriginCityName': 'Alpha', 'DestCityName': 'Beta', 'Distance': 100},
            {'Flight Number': 'F2', 'Price': 10, 'DepTime': '09:00', 'ArrTime': '10:00',
             'ActualElapsedTime': 60, 'FlightDate': '2022-01-03', 'OriginCityName': 'Beta', 'DestCityName': 'Alpha', 'Distance': 100}],
        'accommodations/clean_accommodations_2022.csv': [
            {'NAME': 'Hotel', 'price': 10, 'room type': 'Private room', 'house_rules': 'No parties',
             'minimum nights': 1, 'maximum occupancy': 2, 'review rate number': 5, 'city': 'Beta'}],
        'restaurants/clean_restaurant_2022.csv': [
            {'Name': 'Cafe' + str(i), 'Average Cost': 1, 'Cuisines': 'Local', 'Aggregate Rating': 5, 'City': 'Beta'} for i in range(1,4)],
        'attractions/attractions.csv': [
            {'Name': 'Museum', 'Latitude': 0, 'Longitude': 0, 'Address': 'test', 'Phone': 'test', 'Website': 'test', 'City': 'Beta'}],
        'googleDistanceMatrix/distance.csv': [
            {'origin': 'Alpha', 'destination': 'Beta', 'duration': '1 hour', 'distance': '100 km'}],
    }
    for name, rows in tables.items():
        path = database / name
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows).to_csv(path, index=False)
    path = database / 'background/citySet_with_states.txt'
    path.parent.mkdir(parents=True)
    path.write_text('Alpha\tStateA\nBeta\tStateB')
    row = {'id': 'native-control', 'query': 'Plan a three-day round trip from Alpha to Beta.',
        'reference_information': 'F1 Alpha to Beta; F2 Beta to Alpha; Hotel, Cafe1/2/3 and Museum in Beta.',
        'org': 'Alpha', 'dest': 'Beta', 'days': 3, 'visiting_city_number': 1,
        'date': ['2022-01-01', '2022-01-02', '2022-01-03'], 'people_number': 1,
        'local_constraint': dict.fromkeys(['house rule', 'cuisine', 'room type', 'transportation']),
        'budget': 100, 'level': 'easy'}
    blank = dict.fromkeys(['transportation', 'breakfast', 'attraction', 'lunch', 'dinner', 'accommodation'], '-')
    plan = [
        {**blank, 'current_city': 'from Alpha to Beta', 'transportation': 'Flight Number: F1, from Alpha to Beta', 'accommodation': 'Hotel, Beta'},
        {**blank, 'current_city': 'Beta', 'breakfast': 'Cafe1, Beta', 'lunch': 'Cafe2, Beta',
         'dinner': 'Cafe3, Beta', 'attraction': 'Museum, Beta;', 'accommodation': 'Hotel, Beta'},
        {**blank, 'current_city': 'from Beta to Alpha', 'transportation': 'Flight Number: F2, from Beta to Alpha'},
    ]
    return database, row, plan


def test_exact_native_constraint_controls(planning):
    database, row, plan = planning
    assert len(check_database(database)) == 64
    good = official(row, plan, database)
    assert good == dict.fromkeys(['delivery_rate','commonsense_pass_rate','hard_pass_rate','final_pass_rate'], 1.)
    invalid = copy.deepcopy(plan)
    invalid[1]['breakfast'] = 'Unknown restaurant, Beta'
    assert official(row, invalid, database)['final_pass_rate'] == 0.
    assert official(row, None, database)['delivery_rate'] == 0.


def test_prepared_adapter_conversion_and_hash_custody(planning, tmp_path, monkeypatch):
    database, row, plan = planning
    source = tmp_path / 'validation.jsonl'
    source.write_text(json.dumps(row) + '\n')
    target = tmp_path / 'prepared'
    manifest = prepare('travelplanner', target, {'data_path': str(source), 'database_path': str(database)}, judge_model='scripted-converter')
    monkeypatch.setenv('HG_DATA_TRAVELPLANNER', str(target))
    adapter = Adapter('travelplanner')
    task = adapter.load()[0][0]
    assert row['reference_information'] in task['goal']
    case = adapter.case(manifest['records'][0], manifest)
    monkeypatch.chdir(target)
    (target / 'travelplanner_database').symlink_to(target / 'database', target_is_directory=True)
    assets = Path(__file__).resolve().parents[2] / 'data/scaffolds/native_research'
    # `scoring.py` imports the package's own `native` helper by bare name, so the directory
    # still goes on `sys.path`; only the module *this test* loads is resolved by path.
    monkeypatch.syspath_prepend(str(assets))
    # Loaded by path, not by the bare name `scoring`: another module of that name can already
    # be in `sys.modules` from an earlier test in the same session, and then this import
    # silently binds a different `score` (observed as "score() takes 2 positional arguments").
    import importlib.util
    spec = importlib.util.spec_from_file_location('native_research_scoring', assets / 'scoring.py')
    scoring_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scoring_module)
    score = scoring_module.score
    received = []
    def converter(messages):
        received.append(messages)
        return json.dumps(plan)
    result = score('travelplanner', case, 'Complete itinerary', converter)
    assert result['reward'] == 1. and len(received) == 1
    assert received[0][0]['role'] == 'system'
    (target / 'database/flights/clean_Flights_2022.csv').write_text('changed')
    with pytest.raises(ValueError, match='digest mismatch'):
        adapter.load()
