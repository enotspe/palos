# CLAUDE.md

**Project:** PALOS — PAN-OS Logs Scraper

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Run the scraper
```bash
python3 paloalto_scraper.py
```

### Install dependencies
```bash
pip install requests beautifulsoup4 pandas lxml pyyaml
```

### Dry run (preview without scraping)
Set `dry_run: true` in `paloalto_scraper_config.yaml`, then run the scraper.

## Architecture

This is a single-file web scraper (`paloalto_scraper.py`) with a YAML config (`paloalto_scraper_config.yaml`). It scrapes Palo Alto Networks PAN-OS syslog field documentation and outputs CSV datasets.

### Data flow
1. Config loads PAN-OS versions and per-log-type URLs from `paloalto_scraper_config.yaml`
2. `PaloAltoLogScraper.run()` iterates versions → log types
3. For each log type page, it extracts:
   - **Format string**: comma-separated ordered field list (e.g. `FUTURE_USE, Receive Time, Serial Number, ...`)
   - **Field table**: HTML table with `Field Name` and `Description` columns
4. Outputs per log type into `{version_name}/` directories:
   - `{LogType}_format.csv`: line 1 = original format string, line 2 = transformed (long names replaced with variable names like `receive_time`)
   - `{LogType}_fields.csv`: field table with an added `Variable Name` column extracted from the parenthetical in field names
5. After all per-type files exist, `panos_syslog_fields.csv` consolidates all log types into a matrix (position × log type)

### Key methods
- `extract_format_string()`: regex-based extraction of the `Format:` section from page text
- `extract_field_table()`: finds HTML tables with "field name" header, parses with BS4, adds `Variable Name` column
- `_apply_field_table_corrections()`: post-processes the field table's Variable Name column — applies `token_corrections` to non-empty names, fills empty names from `field_table_overrides` using the long field name; `token_corrections` keys can be variable names (fix field table typos) or unmapped long format string names (fallback when auto-detection fails — the raw long name remains as the output token and token_corrections catches it)
- `_build_name_map()` + `_transform_format_string()`: replaces human-readable long names in the format string with snake_case variable names; the only hardcoded special case is `Device Group Hierarchy Level N` → `dg_hier_level_N` (regex); all other name mismatches are handled via `global_name_overrides`; fields with no parenthetical (filled by `field_table_overrides`) are also mapped via the full field name as key
- `_apply_per_log_corrections()`: applies post-processing corrections per log type; `match:` (value-based, first occurrence) is preferred over `position:` (fragile index); supports `new:` (replace token) and `split_into:` (expand token into multiple)
- `_get_cell_text_with_formatting()`: HTML→text that preserves intentional line breaks from block elements while collapsing source-formatting whitespace

### Config settings
| Key | Default | Effect |
|-----|---------|--------|
| `base_delay` | `1.0` | Seconds between HTTP requests |
| `force_rescrape` | `false` | Skip existing version dirs unless true |
| `dry_run` | `false` | Print plan without fetching |
| `output_dir` | `"."` | Root output directory |
| `strip_leading_future_use` | `false` | Remove leading `FUTURE_USE` token from all transformed format strings |

### Output structure
```
{version_name}/          # e.g. 11.1+/
  {LogType}_format.csv   # 2 lines: original format, variable-name format
  {LogType}_fields.csv   # Field Name, Variable Name, Description (+ other cols)
  panos_syslog_fields.csv  # consolidated matrix across all log types
```
