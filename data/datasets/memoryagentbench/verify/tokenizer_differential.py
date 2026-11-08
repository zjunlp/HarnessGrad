"""Compare real locked tokenizers/chunking on author-owned text with pinned upstream.

No dataset/model calls. The upstream function is checksum-verified, extracted by
AST, and its explicit nltk.download call is removed (the sole declared resource
provisioning delta). Sentence segmentation and both BPE implementations are real.
"""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from data.datasets.memoryagentbench.lib.inputs import initialize, installed_version, read_spec, split_context


def upstream_function(path, nltk, tiktoken):
    raw = Path(path).read_bytes()
    expected = read_spec('chunk-policy.json')['upstream_module_sha256']
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError('upstream chunk module checksum mismatch')
    tree = ast.parse(raw)
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == 'chunk_text_into_sentences')
    downloads = [node for node in function.body if isinstance(node, ast.Expr)
                 and isinstance(node.value, ast.Call)
                 and isinstance(node.value.func, ast.Attribute)
                 and isinstance(node.value.func.value, ast.Name)
                 and node.value.func.value.id == 'nltk' and node.value.func.attr == 'download']
    if len(downloads) != 1 or ast.literal_eval(downloads[0].value.args[0]) != 'punkt':
        raise ValueError('unexpected upstream resource provisioning statement')
    function.body.remove(downloads[0])
    namespace = {'nltk': nltk, 'tiktoken': tiktoken}
    module = ast.Module(body=[function], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(path), 'exec'), namespace)
    return namespace['chunk_text_into_sentences'], expected


def compare(resources, upstream_module):
    resources = Path(resources).resolve()
    sentences, encoding = initialize(resources)
    # Force official public APIs to use only the already verified local files.
    os.environ['TIKTOKEN_CACHE_DIR'] = str(resources / 'tiktoken')
    import nltk
    import tiktoken
    nltk.data.path[:] = [str(resources / 'nltk')]
    nltk.tokenize._get_punkt_tokenizer.cache_clear()
    official, source_hash = upstream_function(upstream_module, nltk, tiktoken)
    official_encoding = tiktoken.encoding_for_model('gpt-4o-mini')
    cases = {
        'empty': '',
        'abbreviations': 'Dr. Smith met Ms. Jones at 3.14 p.m. They left. Next day, Mr. Brown returned.',
        'quotes_multilingual': '"Wait!" she said. 你好世界！ We paid $4.50.\n\nA new paragraph begins here.',
        'special_token': 'A marker <|endoftext|> occurs here. A second sentence follows.',
        'threshold_crossing': ('A short sentence ends here. ' * 900),
        'oversized_first_sentence': 'word ' * 5000 + '. Next sentence.',
    }
    results = []
    for name, text in cases.items():
        actual_sentences = sentences.tokenize(text)
        expected_sentences = nltk.sent_tokenize(text)
        assert actual_sentences == expected_sentences, name + ': sentence segmentation differs'
        tokens = encoding.encode(text, allowed_special={'<|endoftext|>'})
        expected_tokens = official_encoding.encode(text, allowed_special={'<|endoftext|>'})
        assert tokens == expected_tokens, name + ': BPE ids differ'
        actual = split_context(text, sentences, encoding)
        expected = official(text)
        assert actual == expected, name + ': upstream chunk grouping differs'
        if name == 'oversized_first_sentence':
            assert actual[0] == '' and len(encoding.encode(actual[1])) > 4096
        results.append({'case': name, 'text_sha256': hashlib.sha256(text.encode()).hexdigest(),
                        'sentence_count': len(actual_sentences), 'token_count': len(tokens),
                        'chunk_count': len(actual), 'empty_chunks': sum(chunk == '' for chunk in actual),
                        'chunks_sha256': hashlib.sha256(json.dumps(actual, ensure_ascii=False).encode()).hexdigest(),
                        'sentence_equal': True, 'bpe_ids_equal': True, 'upstream_chunks_equal': True})
    return {'schema': 'hg-mab-locked-tokenizer-differential-v1', 'python': sys.executable,
            'prefix': sys.prefix, 'isolated': bool(sys.flags.isolated),
            'package_versions': {name: installed_version(name) for name in read_spec('chunk-policy.json')['dependencies']},
            'package_origins': {'nltk': nltk.__file__, 'tiktoken': tiktoken.__file__},
            'upstream_module_sha256': source_hash, 'resources_lock': read_spec('resources.lock.json'),
            'upstream_delta': 'remove only explicit nltk.download; verified offline tables instead',
            'author_owned_text_only': True, 'dataset_prepared': False, 'model_calls': 0, 'results': results}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resources', type=Path, required=True)
    parser.add_argument('--upstream-module', type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(compare(args.resources, args.upstream_module), indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
