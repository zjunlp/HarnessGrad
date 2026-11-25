"""Representative authored controls executed only by the PTC-Bench smoke command."""
import argparse
import json
from pathlib import Path
from statistics import mean
import sys


def filtered(ptc):
    for place in ptc.call("yelp_api.search_businesses", category="Chinese", max_results=10000):
        key = place["business_id"]
        distance = ptc.call("yelp_api.get_distance_to_business", business_id=key)["distance_miles"]
        if (place["review_count"] >= 100 and distance <= 10
                and ptc.call("yelp_api.check_business_open", business_id=key)["is_open"]):
            assert ptc.call("yelp_api.bookmark_business", business_id=key)["bookmark_status"]
            return {k: place[k] for k in ("name", "business_id", "rating")} | {"distance_miles": distance}
    raise AssertionError("no qualifying venue in pinned control")


def recent_drift(ptc):
    rows = []
    failed = []
    for index, place in enumerate(ptc.call("yelp_api.search_businesses", category="Italian", max_results=10000), 1):
        key = place["business_id"]
        reviews = ptc.call("yelp_api.get_reviews_for_business", business_id=key, max_results=10000)
        if isinstance(reviews, dict) and "error" in reviews:
            failed.append(index)
            reviews = ptc.call("yelp_api.get_reviews_for_business", business_id=key, max_results=10000)
        old = round(mean(x["rating"] for x in reviews), 2) if reviews else 0.0
        recent = [x["rating"] for x in reviews if x["created_at"] >= "2026-01-27"]
        new = round(mean(recent), 2) if recent else 0.0
        rows.append({"business_id": key, "name": place["name"], "all_time_rating": old,
                     "recent_rating": new, "drift": round(abs(old - new), 2)})
    assert failed == [4]
    return {"top3": sorted(rows, key=lambda x: (-x["drift"], x["name"]))[:3]}


def refined_query(ptc):
    rows = []
    for place in ptc.call("yelp_api.search_businesses", category="Chinese", max_results=10000):
        key = place["business_id"]
        distance = ptc.call("yelp_api.get_distance_to_business", business_id=key)["distance_miles"]
        if place["review_count"] >= 200 and distance <= 5:
            rows.append({k: place[k] for k in ("business_id", "name", "rating", "review_count")} | {
                "distance_miles": distance,
                "wait_minutes": ptc.call("yelp_api.get_wait_time", business_id=key).get("estimated_wait_minutes", 0),
                "is_open": ptc.call("yelp_api.check_business_open", business_id=key)["is_open"],
            })
    initial = sorted(rows, key=lambda x: (-x["rating"], x["distance_miles"]))
    prior = ptc.status()
    assert prior["turn"] == 1 and prior["turns"] == 2
    advance = ptc.answer(json.dumps(initial))
    assert advance == ptc.status()
    assert advance["turn"] == 2 and advance["question"] != prior["question"]
    return sorted((x for x in initial if x["is_open"]), key=lambda x: (x["wait_minutes"], -x["rating"]))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--workdir", type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.workdir.resolve()))
    import ptc

    config = json.loads(args.task.read_bytes())
    mode = (args.workdir / ".ptc-control").read_text()
    if mode == "oracle":
        split = config["task_id"].split("/")[1]
        assert ptc.status()["turn"] == 1
        result = {"main": filtered, "error_recovery": recent_drift, "multi_turn": refined_query}[split](ptc)
        response = json.dumps(result)
        if split == "multi_turn":
            (args.workdir / "answer.txt").write_text(response, encoding="utf-8")
        else:
            assert ptc.answer(response)["complete"]
    elif mode != "noop":
        raise ValueError("invalid control mode")
