"""Host-only native constraints, with explicit database paths and no API keys."""

import contextlib
import hashlib
import json
import os
import sys
from pathlib import Path

DATABASE_FILES = {
    "flights/clean_Flights_2022.csv": {
        "Flight Number",
        "Price",
        "DepTime",
        "ArrTime",
        "ActualElapsedTime",
        "FlightDate",
        "OriginCityName",
        "DestCityName",
        "Distance",
    },
    "accommodations/clean_accommodations_2022.csv": {
        "NAME",
        "price",
        "room type",
        "house_rules",
        "minimum nights",
        "maximum occupancy",
        "review rate number",
        "city",
    },
    "restaurants/clean_restaurant_2022.csv": {
        "Name",
        "Average Cost",
        "Cuisines",
        "Aggregate Rating",
        "City",
    },
    "attractions/attractions.csv": {
        "Name",
        "Latitude",
        "Longitude",
        "Address",
        "Phone",
        "Website",
        "City",
    },
    "googleDistanceMatrix/distance.csv": {
        "origin",
        "destination",
        "duration",
        "distance",
    },
    "background/citySet_with_states.txt": None,
}


def check_database(database):
    import pandas as pd

    digest = hashlib.sha256()
    for relative, columns in DATABASE_FILES.items():
        file = database / relative
        if not file.is_file():
            raise ValueError(
                f"Prepare TravelPlanner database/{relative}; see README.md"
            )
        if columns is not None:
            if columns - set(pd.read_csv(file, nrows=0).columns):
                raise ValueError(
                    f"TravelPlanner database/{relative} lacks native columns"
                )
        elif any(
            len(line.split("\t")) != 2
            for line in file.read_text(encoding="utf-8").split("\n")
        ):
            raise ValueError(
                "TravelPlanner requires native citySet_with_states.txt rows without a trailing blank line"
            )
        digest.update(relative.encode() + b"\0")
        with file.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    return digest.hexdigest()


def score(row, plan, database):
    from flashrsi.benchmarks.travelplanner._official import (
        commonsense_constraint as common,
    )
    from flashrsi.benchmarks.travelplanner._official import hard_constraint as hard
    from flashrsi.benchmarks.travelplanner._official.accommodations import (
        Accommodations,
    )
    from flashrsi.benchmarks.travelplanner._official.attractions import Attractions
    from flashrsi.benchmarks.travelplanner._official.flights import Flights
    from flashrsi.benchmarks.travelplanner._official.googleDistanceMatrix import (
        GoogleDistanceMatrix,
    )
    from flashrsi.benchmarks.travelplanner._official.restaurants import Restaurants

    state = {
        "flight": Flights(str(database / "flights/clean_Flights_2022.csv")),
        "accommodation": Accommodations(
            str(database / "accommodations/clean_accommodations_2022.csv")
        ),
        "restaurants": Restaurants(
            str(database / "restaurants/clean_restaurant_2022.csv")
        ),
        "googleDistanceMatrix": GoogleDistanceMatrix(
            path=str(database / "googleDistanceMatrix/distance.csv")
        ),
        "attractions": Attractions(str(database / "attractions/attractions.csv")),
        "city_state_map": dict(
            line.split("\t")
            for line in (database / "background/citySet_with_states.txt")
            .read_text(encoding="utf-8")
            .split("\n")
        ),
    }
    for module in (common, hard):
        for key, value in state.items():
            setattr(module, key, value)
    # Keep eval.py's gate: hard checks run only after complete sandbox-backed
    # information. Its fixed 45/180 denominators do not describe user subsets.
    c = common.evaluation(row, plan) if plan else None
    h = (
        hard.evaluation(row, plan)
        if c and c["is_not_absent"][0] and c["is_valid_information_in_sandbox"][0]
        else None
    )
    cp = bool(c and h and all(v[0] is None or v[0] for v in c.values()))
    hp = bool(h and all(v[0] is None or v[0] != False for v in h.values()))  # noqa: E712
    return {
        "delivery_rate": float(bool(plan)),
        "commonsense_pass_rate": float(cp),
        "hard_pass_rate": float(hp),
        "final_pass_rate": float(cp and hp),
    }


def main():
    request = json.load(sys.stdin)
    try:
        database = Path(request["database"])
        with (
            open(os.devnull, "w") as sink,
            contextlib.redirect_stdout(sink),
            contextlib.redirect_stderr(sink),
        ):
            if check_database(database) != request["database_checksum"]:
                raise ValueError("TravelPlanner database changed after startup")
            metrics = score(request["row"], request["plan"], database)
    except Exception as exc:
        print(f"TravelPlanner scoring failed ({type(exc).__name__})", file=sys.stderr)
        return 1
    print(json.dumps(metrics))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
