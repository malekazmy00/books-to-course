"""Every column the store writes or filters on must be created by its own DDL."""
import re

from b2c_orchestration import translation_store as ts


def _ddl_columns() -> set[str]:
    cols = set()
    for stmt in ts._DDL:
        create = re.search(r"CREATE TABLE IF NOT EXISTS translation_unit_results \((.*)\)", stmt, re.S)
        if create:
            cols |= {line.split()[0] for line in create.group(1).splitlines()
                     if line.strip() and not line.strip().startswith("PRIMARY KEY")}
        added = re.search(r"ALTER TABLE translation_unit_results ADD COLUMN IF NOT EXISTS (\w+)", stmt)
        if added:
            cols.add(added.group(1))
    return cols


def test_upsert_columns_exist_in_ddl():
    written = re.search(r"INSERT INTO translation_unit_results\s*\(([^)]*)\)", ts._UPSERT).group(1)
    missing = {c.strip() for c in written.split(",")} - _ddl_columns()
    assert not missing, f"columns written but never created: {missing}"
