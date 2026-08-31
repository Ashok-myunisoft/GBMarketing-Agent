"""Excel template services.

This package turns a business-provided Excel template into the single
source of truth for export column order/headers/mapping - nothing about
the output shape lives in agents/export_agent.py, which is the only
consumer of this package:

- template_reader.py reads the template's header row and column order,
  and resolves each column to a Company field, a fixed/static value, or
  "intentionally blank".
- template_mapping.py is the primary resolution mechanism: an OPTIONAL
  hidden sheet embedded in the template itself (MAPPING_SHEET_NAME) that
  lets the template author explicitly declare, per column, a Company
  field / a static value / "leave blank" - so replacing the template
  file is enough to change the export, with no code change.
- field_aliases.py is the fallback used only for templates that have no
  mapping sheet at all (older templates, or ones that only need simple
  header-name matching) - fully backward compatible.
"""

from services.excel_template.field_aliases import match_field
from services.excel_template.template_mapping import (
    MAPPING_SHEET_NAME,
    ColumnMappingOverride,
    read_mapping_overrides,
)
from services.excel_template.template_reader import (
    TemplateColumn,
    TemplateLayout,
    read_template_layout,
)

__all__ = [
    "match_field",
    "MAPPING_SHEET_NAME",
    "ColumnMappingOverride",
    "read_mapping_overrides",
    "TemplateColumn",
    "TemplateLayout",
    "read_template_layout",
]
