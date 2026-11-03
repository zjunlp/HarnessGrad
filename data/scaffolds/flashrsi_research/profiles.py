"""Source identities and protocol requirements, separate from runtime readiness."""
COMMIT = 'e9c7667945f959d62da27204649a82f94d1b6705'
PROFILES = {
    'browsecomp': {'class': 'BrowseComp', 'metric': 'accuracy', 'judge': 'text', 'web': True},
    'deepsearchqa': {'class': 'DeepSearchQA', 'metric': 'f1_score', 'judge': 'text', 'web': True},
    'hle': {'class': 'HLE', 'metric': 'accuracy', 'judge': 'structured', 'web': False},
    'livemathbench': {'class': 'LiveMathBench', 'metric': 'accuracy', 'judge': 'text', 'web': False},
    'travelplanner': {'class': 'TravelPlanner', 'metric': 'final_pass_rate', 'judge': 'text', 'web': False},
    'widesearch': {'class': 'WideSearch', 'metric': 'score', 'judge': 'text', 'web': True},
    'workbench': {'class': 'WorkBench', 'metric': 'correct', 'judge': None, 'web': False},
    'harvey_lab': {'class': 'HarveyLAB', 'metric': 'all_pass_rate', 'judge': 'dual-provider', 'web': False},
    'swe_bench': {'class': 'SWEBench', 'metric': 'resolved', 'judge': None, 'web': False},
}

# Immutable source identities transcribed from the pinned native declarations.
SOURCES = {
 'browsecomp': ('http', 'https://openaipublic.blob.core.windows.net/simple-evals/browse_comp_test_set.csv', '7b24471cd5b3eb2a46830a14802b5c029ea62f488ff75a0f88af7923d1454abf'),
 'deepsearchqa': ('hf-file', 'google/deepsearchqa', 'b2623f8653065c2672de6d941fc5434cd652376c'),
 'hle': ('hf-dataset', 'cais/hle', '5a81a4c7271a2a2a312b9a690f0c2fde837e4c29'),
 'livemathbench': ('hf-dataset', 'opencompass/LiveMathBench', 'ea05b95ab431f3ef3900ce86f864451cf5bcd699'),
 'travelplanner': ('hf-dataset', 'osunlp/TravelPlanner', '8736504ecfc31b7f8b7e40122873c337e83fff7c'),
 'widesearch': ('hf-dataset', 'ByteDance-Seed/WideSearch', '6531a7e5b497d44c8912407e0cb3dc95bd98cc09'),
 'workbench': ('git', 'olly-styles/WorkBench', '49c7dfd00c03d384ec59ea57374f50b766aa5613'),
 'harvey_lab': ('git', 'harveyai/harvey-labs', 'f93ae2ac2831e65892fea83094cfb50149bc2e59'),
 'swe_bench': ('hf-dataset', 'SWE-bench/SWE-bench_Verified', '78f471bf655a3137b2e8a75af1501690ec009ec3'),
}
