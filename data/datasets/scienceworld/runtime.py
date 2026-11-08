"""Read-only managed payload lookup; never import the third-party package."""
import hashlib
from pathlib import Path
JAR_SHA256 = "f4e1fdce0cdabe1bab7128a7e78ea820210d9db3c28d7272bcbbc3f3a1c5f0d7"
def payload(binding):
    jar = Path(binding['path']) / 'lib/python3.12/site-packages/scienceworld/scienceworld.jar'
    if jar.is_symlink() or not jar.is_file() or hashlib.sha256(jar.read_bytes()).hexdigest() != JAR_SHA256:
        raise RuntimeError('managed ScienceWorld JAR missing or differs from sealed scorer; explicitly prepare the scienceworld execution runtime')
    return jar
