# Python runtime filesystem boundary

Native sandbox plan 14 hides conventional global `site-packages` and
`dist-packages` of the platform base Python and declared base Python runtimes.
A read-only interpreter prefix grants standard-library and native-extension
files; it does not grant every third-party package installed beside them.
The masks apply after parent runtime mounts. Direct runtime/visible grants of
a global site or its children are rejected. Public package projections remain
explicit mounts after these masks.

Prepare actor dependencies in an independent virtualenv (`pyvenv.cfg` present),
and declare that virtualenv as a runtime. Its own site packages remain visible,
including SDK namespace packages. A virtualenv configured to inherit system
site packages cannot use the hidden base packages: install its dependencies in
the independent environment. Existing global-only dependency setups must migrate.

This is a boundary for conventional Python third-party directories, not a claim
that `/usr` or all OS files are sealed. It does not discover arbitrary custom
package folders or interpret PYTHONPATH as an allowlist. Runtime declarations
are trusted configuration; declare selected interpreter roots rather than broad
stores with arbitrarily nested interpreter installations.
