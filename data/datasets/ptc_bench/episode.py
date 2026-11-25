"""A trusted simulator episode with a closed, documented tool surface."""
from importlib import import_module
import json


# The official experiments/run_eval.py _dispatch_api_call and _serialize_api_state
# map the stable API names and final-state fields this way, including for *Alt APIs.
APIS = {
    "EnterpriseCalendarAPI": ("enterprise_calendar_api", ("metadata", "employees", "events")),
    "OrgChartAPI": ("org_chart_api", ("employees", "reporting_history")),
    "YelpAPI": ("yelp_api", ("businesses", "reviews", "saved_places", "bookmarks")),
    "GoogleMapsAPI": ("maps_api", ("geocode_cache",)),
    "UberAPI": ("uber_api", ("pricing", "rides", "current_ride", "ride_counter")),
    "GroceryDeliveryAPI": ("grocery_api", ("shopping_list", "stores", "gift_cards")),
    "PersonalCalendarAPI": ("personal_calendar_api", ("events", "current_date", "event_counter")),
    "DoorDashAPI": ("doordash_api", ("restaurants", "menus", "orders", "order_counter")),
    "SpotifyAPI": ("spotify_api", ("playlists", "tracks", "recently_played", "saved_playlists")),
    "VenmoAPI": ("venmo_api", ("balance", "friends", "transactions", "pending_requests")),
}


def _load_apis(task):
    from ptc_bench.constants.eval_config import MULTI_TURN_FUNC_DOC_PATH
    from ptc_bench.constants.executable_backend_config import (
        ALL_API_CLASSES, CLASS_FILE_PATH_MAPPING, MULTI_TURN_FUNC_DOC_FILE_MAPPING,
    )
    from ptc_bench.utils import load_file

    # Preserve the official runner's all-APIs default and replace just the task's Alt APIs.
    alternative = {name.removesuffix("Alt"): name for name in task["involved_classes"]}
    objects, tools, methods = {}, [], {}
    for base in ALL_API_CLASSES:
        chosen = alternative.get(base, base)
        entry = CLASS_FILE_PATH_MAPPING[chosen]
        module, class_name = entry if isinstance(entry, tuple) else (entry, chosen)
        api = getattr(import_module(module), class_name)()
        thresholds = task.get("flakiness_config", {})
        flakiness = thresholds.get(class_name, thresholds.get(chosen))
        if flakiness is not None:
            api._flaky_thresholds = flakiness
        api._load_scenario(task["initial_config"].get(chosen, {}))
        objects[base] = api
        prefix, _ = APIS[base]
        for doc in load_file(MULTI_TURN_FUNC_DOC_PATH / MULTI_TURN_FUNC_DOC_FILE_MAPPING[chosen]):
            method = doc["name"]
            if not method.isidentifier() or method.startswith("_"):
                raise ValueError("invalid public method in pinned tool documentation")
            name = f"{prefix}.{method}"
            bound = getattr(api, method)
            if name in methods or not callable(bound):
                raise ValueError("invalid pinned PTC-Bench tool catalogue")
            methods[name] = bound
            tools.append({**doc, "name": name})
    return objects, tools, methods


class Episode:
    def __init__(self, task):
        self.questions = [turn[0]["content"] for turn in task["question"]]
        self.answers = []
        self.released_turns = 0
        self.api_calls = 0
        self.api_errors = 0
        self._apis, self.tools, self._methods = _load_apis(task)

    def status(self):
        index = len(self.answers)
        complete = index == len(self.questions)
        if not complete:
            self.released_turns = index + 1
        return {"complete": complete, "turn": min(index + 1, len(self.questions)),
                "turns": len(self.questions),
                "question": None if complete else self.questions[index]}

    def handle(self, op, args):
        if op == "status" and args == {}:
            return self.status()
        if op == "tools" and args == {}:
            return self.tools
        if len(self.answers) == len(self.questions):
            return {"error": "episode already submitted"}
        if op == "answer" and set(args) == {"text"} and isinstance(args["text"], str):
            if self.released_turns != len(self.answers) + 1:
                return {"error": "read the current user request before answering"}
            self.answers.append(args["text"])
            return self.status()
        if (op != "call" or set(args) != {"name", "arguments"}
                or not isinstance(args["name"], str) or not isinstance(args["arguments"], dict)):
            return {"error": "invalid PTC-Bench operation or arguments"}
        method = self._methods.get(args["name"])
        if method is None:
            return {"error": "unknown documented tool"}
        self.api_calls += 1
        try:
            json.dumps(args["arguments"], allow_nan=False)
            result = method(**args["arguments"])
            result = json.loads(json.dumps(result, default=str, allow_nan=False))
        except Exception as exc:
            # The official direct API runner also reports tool errors to the actor as
            # data. These include the benchmark's injected, recoverable API failures.
            result = {"error": f"{type(exc).__name__}: {exc}"}
        if isinstance(result, dict) and "error" in result:
            self.api_errors += 1
        return result

    def snapshot(self):
        complete = len(self.answers) == len(self.questions)
        return {
            "final_state": {base: {key: getattr(api, key) for key in APIS[base][1]}
                            for base, api in self._apis.items()},
            "turn_answers": self.answers[:-1] if complete else self.answers,
            "final_answer": self.answers[-1] if complete else None,
            "api_calls": self.api_calls,
            "api_errors": self.api_errors,
            "released_turns": self.released_turns,
            "complete": complete,
        }
