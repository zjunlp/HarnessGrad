"""A task set that needs more than one model call.

The demo set asks for a word, which one call answers -- so every harness scores the
same and no method can demonstrate anything. These tasks require *doing something and
checking it*: creating a file, running code, fixing an error. A harness that stops
after one model call cannot pass them; one that iterates can. That gap is what a
method needs in order to have anything to accept or reject.
"""



#: See `data/scaffolds/registry.py:KIND_DATASET`. A fixture is platform test material and is
#: deliberately not offered as a choice in the Launch form.
KIND = "fixture"

from pathlib import Path

TASKS = [
    {"task_id": "d01", "goal":
        "In your --workdir, create a Python file `calc.py` with a function "
        "`mul(a, b)` that returns their product. Run it with `python3 -c` on the "
        "inputs 6 and 7 and answer with the printed result."},
    {"task_id": "d02", "goal":
        "In your --workdir, create `data.txt` containing exactly the three lines "
        "apple, banana, cherry in that order. Then answer with the number of lines."},
    {"task_id": "d03", "goal":
        "In your --workdir, write a script `sum.py` that prints the sum of the "
        "integers 1 through 10. Run it and answer with the number it prints."},
    {"task_id": "d04", "goal":
        "In your --workdir, create `nested/deep/here.txt` (create the directories "
        "too) containing the single word zeta. Then answer with the word you wrote."},
    {"task_id": "d05", "goal":
        "In your --workdir, create `broken.py` that raises a ValueError, run it to "
        "confirm it fails, then fix it so it prints ok, run it again, and answer "
        "with what it prints."},
    {"task_id": "d06", "goal":
        "In your --workdir, create `count.py` which counts how many words are in "
        "the string 'the quick brown fox jumps'. Run it and answer with the count."},
]
SCORABLE = {"d01": "42", "d02": "3", "d03": "55", "d04": "zeta",
            "d05": "ok", "d06": "5"}

#: Four to study, two to be scored on. The eval side is deliberately small --
#: this set exists to be cheap -- and that has a visible cost the platform
#: reports rather than hides: with n=2 the score can only move in steps of 0.5.
SPLIT = {
    "train": ["d01", "d02", "d03", "d04"],
    "eval": ["d05", "d06"],
}


def load():
    return TASKS, SCORABLE
