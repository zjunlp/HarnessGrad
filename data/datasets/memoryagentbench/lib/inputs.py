"""Pinned task text and offline sentence grouping for MemoryAgentBench.

No external download or implicit tokenizer cache is used. The grouping matches
upstream, including empty chunks before an oversized first sentence.
"""
import base64
import importlib.metadata
import json
from pathlib import Path

from .storage import file_sha256

SPECS = Path(__file__).resolve().parents[1] / "specs"
SPEC_SHA256 = {
    "chunk-policy.json": "c3e54b75d541d9195713292255c767de7b8a9f7ce48a324868b9a26d7600f43b",
    "frozen-episodes.json": "eef8007232d95d44c82cdf936a245da920b2b6e8653c5e82e57464691f03d30c",
    "resources.lock.json": "9f89826ef63c636ac81874f5ec96ac92d4feb0a215d8cc53f2b460cfce4f0a53",
    "source.json": "9878ce7ac3330e87013424c3d65b0f47094995b25087e03bf46c4592475dd827",
    "templates.json": "f02769d347960aee3ce2dd5bd641e63b855314096cc94c856cd699172cf2bdcd",
}


def read_spec(name):
    if name not in SPEC_SHA256 or file_sha256(SPECS / name) != SPEC_SHA256[name]:
        raise ValueError("input specification checksum mismatch: " + name)
    return json.loads((SPECS / name).read_text(encoding="utf-8"))


def render_question(source, question):
    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must be a nonempty string")
    templates = read_spec("templates.json")
    try:
        family = templates["source_to_family"][source]
    except KeyError as exc:
        raise ValueError("unsupported source: " + str(source)) from exc
    return templates["query_templates"][family].format(question=question)


def validate_resources(resources):
    resources = Path(resources).resolve()
    policy = read_spec("chunk-policy.json")
    for package, expected in policy["dependencies"].items():
        if importlib.metadata.version(package) != expected:
            raise ValueError("dependency_version_mismatch:" + package)
    lock = read_spec("resources.lock.json")
    for entry in lock["files"]:
        relative = Path(entry["path"]).relative_to("resources")
        path = resources / relative
        if not path.is_file():
            raise ValueError("resource_missing:" + str(relative))
        if path.stat().st_size != entry["bytes"] or file_sha256(path) != entry["sha256"]:
            raise ValueError("resource_hash_mismatch:" + str(relative))
    return lock


def initialize(resources):
    """Construct both tokenizers directly from verified files; no global cache."""
    resources = Path(resources).resolve()
    lock = validate_resources(resources)
    from nltk.tokenize.punkt import PunktSentenceTokenizer, PunktParameters
    from nltk.tabdata import PunktDecoder
    import tiktoken
    params, decoder = PunktParameters(), PunktDecoder()
    for filename, attribute, decode in (
        ("collocations.tab", "collocations", lambda f: set(decoder.tab2tups(f))),
        ("sent_starters.txt", "sent_starters", decoder.txt2set),
        ("abbrev_types.txt", "abbrev_types", decoder.txt2set),
        ("ortho_context.tab", "ortho_context", decoder.tab2intdict),
    ):
        with (resources / "nltk/tokenizers/punkt_tab/english" / filename).open(encoding="utf-8") as stream:
            setattr(params, attribute, decode(stream))
    sentence_tokenizer = PunktSentenceTokenizer(params)
    encoding_path = next(e["path"] for e in lock["files"] if e["path"].startswith("resources/tiktoken/"))
    ranks = {}
    with (resources / Path(encoding_path).relative_to("resources")).open("rb") as stream:
        for line in stream:
            token, rank = line.split()
            ranks[base64.b64decode(token)] = int(rank)
    # o200k_base regex and special IDs from pinned tiktoken 0.14.0 (MIT).
    pattern = "|".join([
        r"[^\r\n\p{L}\p{N}]?[\p{Lu}\p{Lt}\p{Lm}\p{Lo}\p{M}]*[\p{Ll}\p{Lm}\p{Lo}\p{M}]+(?i:'s|'t|'re|'ve|'m|'ll|'d)?",
        r"[^\r\n\p{L}\p{N}]?[\p{Lu}\p{Lt}\p{Lm}\p{Lo}\p{M}]+[\p{Ll}\p{Lm}\p{Lo}\p{M}]*(?i:'s|'t|'re|'ve|'m|'ll|'d)?",
        r"\p{N}{1,3}", r" ?[^\s\p{L}\p{N}]+[\r\n/]*", r"\s*[\r\n]+", r"\s+(?!\S)", r"\s+",
    ])
    encoding = tiktoken.Encoding(name="o200k_base", pat_str=pattern, mergeable_ranks=ranks,
                                 special_tokens={"<|endoftext|>": 199999, "<|endofprompt|>": 200018})
    return sentence_tokenizer, encoding


def split_context(context, sentence_tokenizer, encoding):
    if not isinstance(context, str):
        raise TypeError("context must be a string")
    chunks, pending, running = [], [], 0
    for sentence in sentence_tokenizer.tokenize(context):
        count = len(encoding.encode(sentence, allowed_special={"<|endoftext|>"}))
        if running + count > 4096:
            chunks.append(" ".join(pending))
            pending, running = [sentence], count
        else:
            pending.append(sentence)
            running += count
    if pending:
        chunks.append(" ".join(pending))
    return chunks
