"""Dataset adapters, shared scaffolds, and platform fixtures.

This ordinary package is copied beside trusted QA scorers in the verification
workdir. Its __init__.py prevents a workdir copy from merging with a platform
namespace hidden by the sandbox. Shared implementations live in data.scaffolds;
adapters live at data.datasets.<name>; top-level fixture modules keep their names.
"""
