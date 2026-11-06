# SpreadsheetBench formula cache freshness

The [scorer](verify/check.py) compares cached cell values. LibreOffice conversion
can preserve a workbook's stale caches, so a successful conversion alone does not
establish that formulas were recalculated.

The recalculation path:

1. Creates a private LibreOffice profile with `OOXMLRecalcMode=0`.
2. Copies the OOXML package into scratch storage and removes scalar formula caches
   while preserving the other package entries.
3. Requires every recorded formula to retain its formula and have a populated cache
   in the converted workbook before accepting that workbook.

A copied input or partially evaluated conversion is refused. Recalculation failures
produce unmeasured evidence, exit 3 and no new reward file; completed value
mismatches remain measured zero. Shared, array and data-table formula groups are
refused because their dependent caches cannot all be invalidated by this path.

The [authored control](../../../tests/fixtures/hg_spreadrt/synthetic_control.py)
creates two arithmetic formulas with stale caches `[999, 888]` and requires fresh
values `[5, 10]`, reward 1 and verified scorer-input binding. It reads no released
dataset records. Run it with a new external work directory as its argument,
`openpyxl`, LibreOffice at `/usr/bin/soffice`, an isolated Linux network namespace
and `SPREADRT_PARENT_NETNS` set to the launcher's network namespace identifier.

Cache freshness does not establish general formula correctness or Excel/LibreOffice
semantic equivalence. The control covers two scalar arithmetic formulas; richer
workbooks, external links, UDFs, volatile formulas, other engine versions and
production read-only candidate lifecycles require separate validation. It supplies
no dataset scores or coverage.
