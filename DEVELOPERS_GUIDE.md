# PALOS — Developers Guide

This guide covers the internal architecture of `paloalto_scraper.py`, the corrections
pipeline end-to-end, how to extend the exceptions system, and a key methods reference.
It is written for maintainers and AI assistants working on the codebase.

---

## Architecture Overview

PALOS is a single-file scraper with two supporting YAML files:

```
paloalto_scraper.py             # All scraping logic
paloalto_scraper_config.yaml    # Versions to scrape, URLs, run settings
paloalto_scraper_exceptions.yaml  # Known PAN-OS docs corrections (see below)
```

Data flows through five stages per log type:

```
1. Fetch         HTTP GET → BeautifulSoup
2. Extract       format string + field table from HTML
3. Correct       fix field table Variable Name column
4. Map           build long-name → variable-name dict
5. Transform     apply map to format string, apply per-log fixes
```

Stages 3–5 are where the exceptions system lives.

---

## The Corrections Pipeline (per log type)

Each log type goes through `scrape_log_type()`, which calls the pipeline stages in order:

### Stage 1 — `extract_format_string(soup)`

Regex-searches the page text for a `Format:` section and returns the raw comma-separated
field list as a string. Example output:

```
FUTURE_USE, Receive Time, Serial Number, Type, Threat/Content Type, FUTURE_USE, Generated Time, ...
```

### Stage 2 — `extract_field_table(soup)`

Finds the HTML table with a "field name" header, parses it with BeautifulSoup, and returns
a DataFrame. A `Variable Name` column is inserted after `Field Name` using
`_extract_variable_name()`, which pulls the first word from the parenthetical in each field
name. Fields with no parenthetical get an empty string.

`_get_cell_text_with_formatting()` is used for cell content to preserve intentional line
breaks from HTML block elements while collapsing source-formatting whitespace.

### Stage 3 — `_apply_field_table_corrections(field_table)`

**This stage runs on the field table before it is saved to CSV and before name_map is built.**

For each row:

- If `Variable Name` is **non-empty**: apply `token_corrections` lookup (fixes typos/truncations in PAN-OS parentheticals)
- If `Variable Name` is **empty**: extract the long field name (text before the first `(`), then look up in `global_name_overrides` (fills Audit_Log's fields that have no parenthetical in PAN-OS docs)

The corrected DataFrame is what gets saved to `*_fields.csv` and what feeds Stage 4.

### Stage 4 — `_build_name_map(field_table)`

Builds a `{long_name: variable_name}` dictionary from the (now corrected) field table.
For each row, the text before the `(` becomes the key in three forms: original, normalized
whitespace, and lowercase. Finally, `global_name_overrides` is merged in with precedence
over auto-detected mappings. This handles long names that appear in the format string but
have no matching parenthetical anywhere in the field table.

### Stage 5 — `_transform_format_string(format_string, name_map)` + `_apply_per_log_corrections(items, log_type_name)`

`_transform_format_string` splits the format string on commas and for each token:

1. Handles `Device Group Hierarchy Level N` via a dedicated regex → `dg_hier_level_N`
2. Tries direct match, normalized match, and lowercase match against `name_map`
3. Falls back to the original token (e.g. `FUTURE_USE`) if no match found
4. Applies `token_corrections` to every output token

The `Device Group Hierarchy Level N` regex is the only hardcoded special case remaining.
All other name mismatches (including `"Protocol"` → `proto`) are handled via
`global_name_overrides` in `paloalto_scraper_exceptions.yaml`.

`_apply_per_log_corrections` applies position-based fixes last:

- `strip_leading_future_use`: removes position 0 if it is `FUTURE_USE` (currently disabled)
- `new`: replaces the token at a given position with a specific value
- `split_into`: expands a single token at a given position into a list of tokens

---

## Key Methods Reference

| Method | What it does | Notable edge cases handled |
|--------|-------------|---------------------------|
| `extract_format_string(soup)` | Regex-extracts the `Format:` section from page text | Multi-line format strings via `DOTALL` flag |
| `extract_field_table(soup)` | Finds table with "field name" header, returns DataFrame with Variable Name column | Tables that match by substring ("field" or "field name") |
| `_extract_variable_name(field_name)` | Pulls first word from parenthetical | "x or y" in parenthetical → takes only first word; no parenthetical → returns `""` |
| `_apply_field_table_corrections(field_table)` | Corrects Variable Name column in field table | token_corrections for non-empty; global_name_overrides for empty |
| `_build_name_map(field_table)` | Builds `{long_name: var_name}` dict | Three key forms per entry (original, normalized, lowercase); global_name_overrides wins |
| `_transform_format_string(format_string, name_map)` | Replaces long names with variable names | DG hierarchy regex; Protocol special case; token_corrections on all outputs |
| `_apply_per_log_corrections(items, log_type_name)` | Position-based fixes for specific log types | strip, new, split_into |
| `_get_cell_text_with_formatting(cell)` | HTML cell → text preserving intentional line breaks | Block elements get `\n`; source whitespace collapsed |

---

## Adding a New Exception

All exceptions live in `paloalto_scraper_exceptions.yaml`. Add a comment with the log type
and the reason for each new entry.

### Token correction (typo in Variable Name parenthetical)

The PAN-OS field table has a wrong or truncated variable name in parentheses.

```yaml
token_corrections:
  "wrong_name": "correct_name"  # LogType: explanation
```

This is applied to both `*_fields.csv` Variable Name column and the transformed format string.

### Missing variable name (field has no parenthetical in PAN-OS docs)

The PAN-OS field table row has no `(variable_name)` in the Field Name column.

```yaml
global_name_overrides:
  "Long Field Name": "variable_name"  # LogType: cross-referenced from OtherLog
```

Use the exact long field name string as the key. Cross-reference the correct variable name
from another log type's field table that does have the parenthetical for the same field.

### Long name not auto-mapped from format string

The format string token (long name) does not match any parenthetical in the field table,
due to casing differences or different phrasing.

```yaml
global_name_overrides:
  "Exact Format String Token": "variable_name"  # LogType: reason
```

Use the exact string that appears in the format string as the key.

### Position-based fix (specific log type)

A specific token at a specific position needs to be replaced or split, in a way that cannot
be expressed as a name mapping.

```yaml
per_log_corrections:
  LogType_Name:
    - position: N        # 0-indexed after any strip_leading_future_use removal
      new: "variable_name"           # replace the token at position N
      # OR
      split_into: ["token_a", "token_b"]  # expand position N into multiple tokens
```

Note: if `strip_leading_future_use: true`, the original position 0 (`FUTURE_USE`) is removed
before per-log corrections run, so all positions shift down by 1.

---

## Configuration Reference

`paloalto_scraper_config.yaml`:

| Key | Default | Effect |
|-----|---------|--------|
| `settings.base_delay` | `1.0` | Seconds between HTTP requests |
| `settings.force_rescrape` | `false` | Skip existing version dirs unless true |
| `settings.dry_run` | `false` | Print scrape plan without fetching |
| `settings.output_dir` | `"."` | Root output directory |
| `versions[].name` | — | Version label used as output directory name |
| `versions[].log_types[].name` | — | Log type name used as filename prefix |
| `versions[].log_types[].url` | — | PAN-OS docs URL to scrape |

`paloalto_scraper_exceptions.yaml`:

| Key | Effect |
|-----|--------|
| `strip_leading_future_use` | If true, removes leading `FUTURE_USE` from all format strings |
| `global_name_overrides` | Merged into name_map; takes precedence over auto-detected mappings |
| `token_corrections` | Applied to Variable Name column and to all output format tokens |
| `per_log_corrections` | Position-based corrections keyed by log type name |

---

## Running for Development

```bash
# Dry run — print what would be scraped without making any HTTP requests
# Set dry_run: true in paloalto_scraper_config.yaml
python3 paloalto_scraper.py

# Force re-scrape all versions even if output already exists
# Set force_rescrape: true in paloalto_scraper_config.yaml
python3 paloalto_scraper.py

# Install dependencies
pip install requests beautifulsoup4 pandas lxml pyyaml
```
