#!/usr/bin/env python3

"""
Palo Alto PAN-OS Syslog Field Scraper

This script scrapes syslog field descriptions from Palo Alto Networks documentation
for different PAN-OS versions and saves them as separate files for format and field descriptions.

Requirements:
    pip install requests beautifulsoup4 pandas lxml pyyaml
"""

import csv
import requests
from bs4 import BeautifulSoup, NavigableString, Tag
import pandas as pd
import os
import time
import re
import logging
import yaml
from typing import List, Dict, Optional

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class PaloAltoLogScraper:
    def __init__(self, config_file='paloalto_scraper_config.yaml', base_delay=None):
        """
        Initialize the scraper with rate limiting and configuration

        Args:
            config_file: Path to the YAML configuration file
            base_delay: Base delay between requests in seconds (overrides config file if provided)
        """
        # Load configuration from file
        config = self._load_config(config_file, label='main config')

        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        })

        # Use provided base_delay or fall back to config file value
        self.base_delay = base_delay if base_delay is not None else config.get('settings', {}).get('base_delay', 1.0)

        # Load PAN-OS versions from configuration file
        self.versions = config.get('versions', [])
        if not self.versions:
            logger.warning("No versions found in configuration file. Scraper will not process any versions.")

        # Set output directory (defaults to current working directory)
        self.output_dir = config.get('settings', {}).get('output_dir', os.getcwd())
        os.makedirs(self.output_dir, exist_ok=True)

        # Load force_rescrape and dry_run flags
        self.force_rescrape = config.get('settings', {}).get('force_rescrape', False)
        self.dry_run = config.get('settings', {}).get('dry_run', False)

        # Configurable pause between versions (previously hardcoded to 2s)
        self.inter_version_delay = config.get('settings', {}).get('inter_version_delay', 2.0)

        # Max HTTP retry attempts per URL
        self.max_retries = config.get('settings', {}).get('max_retries', 3)

        # Load exceptions/corrections file
        exceptions = self._load_config('paloalto_scraper_exceptions.yaml', label='exceptions')
        self.global_name_overrides = exceptions.get('global_name_overrides', {})
        self.token_corrections = exceptions.get('token_corrections', {})
        self.per_log_corrections = exceptions.get('per_log_corrections', {})
        self.strip_leading_future_use = config.get('settings', {}).get('strip_leading_future_use', False)

        logger.info(f"Loaded {len(self.versions)} versions from main config")
        logger.info(f"Force rescrape: {self.force_rescrape}")
        logger.info(f"Dry run mode: {self.dry_run}")
        logger.info(f"Strip leading FUTURE_USE: {self.strip_leading_future_use}")
        logger.info(f"Loaded {len(self.global_name_overrides)} global name overrides, "
                    f"{len(self.token_corrections)} token corrections, "
                    f"{len(self.per_log_corrections)} per-log correction entries")

    def _load_config(self, config_file: str, label: str = 'configuration') -> dict:
        """
        Load configuration from YAML file

        Args:
            config_file: Path to the YAML configuration file
            label: Human-readable label for log messages (e.g. 'main config', 'exceptions')

        Returns:
            Dictionary containing configuration
        """
        # Get the directory where this script is located
        script_dir = os.path.dirname(os.path.abspath(__file__))
        config_path = os.path.join(script_dir, config_file)

        try:
            with open(config_path, 'r') as f:
                config = yaml.safe_load(f)
                logger.info(f"Loaded {label} from {config_path}")
                return config
        except FileNotFoundError:
            logger.error(f"{label.capitalize()} file not found: {config_path}")
            raise
        except yaml.YAMLError as e:
            logger.error(f"Error parsing {label} YAML: {e}")
            raise

    def _version_exists(self, version: dict) -> bool:
        """
        Check if a version has already been scraped (fully).

        A version is considered complete when the number of CSV files in its directory
        is at least equal to the number of configured log types. Fewer files than that
        indicates a partial/interrupted scrape that should be redone.

        Args:
            version: Version dictionary with 'name' and 'log_types' keys

        Returns:
            True if the version directory appears complete, False otherwise
        """
        version_dir = self.get_version_directory(version['name'])

        if os.path.exists(version_dir):
            files = [f for f in os.listdir(version_dir) if f.endswith('.csv')]
            expected_min = len(version.get('log_types', []))
            if len(files) >= expected_min:
                logger.info(f"Version {version['name']} already complete ({len(files)} CSV files)")
                return True
            elif files:
                logger.warning(
                    f"Version {version['name']} appears incomplete: "
                    f"found {len(files)} CSV files, expected at least {expected_min}. "
                    f"Will re-scrape."
                )

        return False

    def _get_versions_to_scrape(self) -> List[dict]:
        """
        Get the list of versions to scrape based on force_rescrape flag

        Returns:
            List of version dictionaries to scrape
        """
        if self.force_rescrape:
            logger.info("Force rescrape enabled - will scrape all versions")
            return self.versions

        # Filter out existing versions
        versions_to_scrape = [v for v in self.versions if not self._version_exists(v)]

        existing_count = len(self.versions) - len(versions_to_scrape)
        logger.info(f"Found {existing_count} existing versions, {len(versions_to_scrape)} new versions to scrape")

        return versions_to_scrape

    def get_version_directory(self, version_name: str) -> str:
        """
        Get the version directory path

        Args:
            version_name: Version name like '11.1+'

        Returns:
            Path to version directory
        """
        return os.path.join(self.output_dir, version_name)

    def get_page_content(self, url: str) -> Optional[BeautifulSoup]:
        """
        Fetch and parse a web page, retrying on transient failures.

        Args:
            url: URL to fetch

        Returns:
            BeautifulSoup object or None if all attempts failed
        """
        for attempt in range(1, self.max_retries + 1):
            try:
                attempt_label = f" (attempt {attempt}/{self.max_retries})" if attempt > 1 else ""
                logger.info(f"Fetching: {url}{attempt_label}")
                response = self.session.get(url, timeout=30)
                response.raise_for_status()

                # Rate limiting
                time.sleep(self.base_delay)

                return BeautifulSoup(response.content, 'html.parser')

            except requests.exceptions.RequestException as e:
                logger.error(f"Error fetching {url} (attempt {attempt}/{self.max_retries}): {e}")
                if attempt < self.max_retries:
                    wait = self.base_delay * attempt
                    logger.info(f"Retrying in {wait:.1f}s...")
                    time.sleep(wait)

        return None

    def extract_format_string(self, soup: BeautifulSoup) -> Optional[str]:
        """
        Extract the syslog format string from the page

        Args:
            soup: BeautifulSoup object of the page

        Returns:
            Format string or None if not found
        """
        # Look for text that starts with "Format:"
        text_content = soup.get_text()

        # Pattern to match "Format:" followed by the comma-separated list
        format_match = re.search(r'Format\s*:\s*(.+?)(?:\n\s*\n|\n{2,})', text_content, re.IGNORECASE | re.DOTALL)

        if format_match:
            format_string = format_match.group(1).strip()
            # Clean up any extra whitespace
            format_string = re.sub(r'\s+', ' ', format_string)
            logger.info(f"Found format string: {format_string[:100]}...")
            return format_string

        logger.warning("No format string found on page")
        return None

    def _extract_variable_name(self, field_name: str) -> str:
        """
        Extract variable name from Field Name.
        Format: "Field Long Name (variable_name ...)" -> "variable_name"
        """
        match = re.match(r"^.+?\s+\(([^)]+)\)", str(field_name))
        if match:
            # Take first word in parentheses (handles "x or y" cases)
            return match.group(1).split()[0].strip()
        return ""

    def _apply_field_table_corrections(self, field_table: pd.DataFrame) -> pd.DataFrame:
        """Apply corrections to the Variable Name column of the field table.

        - Non-empty Variable Names: apply token_corrections (fixes PA docs typos).
        - Empty Variable Names: fill from global_name_overrides using the long field name
          (handles fields like Audit_Log's Serial Number that have no parenthetical in PA docs).
        """
        if 'Variable Name' not in field_table.columns or 'Field Name' not in field_table.columns:
            return field_table

        corrected_names = []
        for _, row in field_table.iterrows():
            raw = row['Variable Name']
            var_name = "" if pd.isna(raw) else str(raw)
            field_name = str(row['Field Name'])

            if var_name:
                corrected_names.append(self.token_corrections.get(var_name, var_name))
            else:
                match = re.match(r"^(.+?)\s+\(", field_name)
                long_name = match.group(1).strip() if match else field_name.strip()
                corrected_names.append(self.global_name_overrides.get(long_name, ""))

        field_table = field_table.copy()
        field_table['Variable Name'] = corrected_names
        return field_table

    def _build_name_map(self, field_table: pd.DataFrame) -> Dict[str, str]:
        """Build mapping from long field names to variable names."""
        name_map = {}
        if 'Field Name' not in field_table.columns or 'Variable Name' not in field_table.columns:
            return name_map

        for _, row in field_table.iterrows():
            field_name = str(row['Field Name'])
            var_name = str(row['Variable Name'])
            if not var_name:
                continue
            # Extract long name (text before parentheses)
            match = re.match(r"^(.+?)\s+\(", field_name)
            if match:
                long_name = match.group(1).strip()
                name_map[long_name] = var_name
                # Normalized whitespace version — only add if distinct to avoid redundant keys
                normalized = re.sub(r'\s+', ' ', long_name)
                if normalized != long_name:
                    name_map[normalized] = var_name
                name_map[normalized.lower()] = var_name

        # Global overrides take precedence over auto-detected mappings
        name_map.update(self.global_name_overrides)
        return name_map

    def _transform_format_string(self, format_string: str, name_map: Dict[str, str]) -> List[str]:
        """Transform format string: replace long names with variable names.

        Returns a list of variable-name tokens (not a CSV string).
        """
        format_items = [item.strip() for item in format_string.split(',')]
        new_items = []

        for item in format_items:
            # Special case: Device Group Hierarchy Level X (only remaining hardcoded rule)
            match_dg = re.match(r"Device Group Hierarchy Level (\d+)", item, re.IGNORECASE)
            if match_dg:
                new_items.append(f"dg_hier_level_{match_dg.group(1)}")
                continue

            # Try direct match, then normalized, then lowercase
            normalized = re.sub(r'\s+', ' ', item)
            if item in name_map:
                new_items.append(name_map[item])
            elif normalized in name_map:
                new_items.append(name_map[normalized])
            elif normalized.lower() in name_map:
                new_items.append(name_map[normalized.lower()])
            else:
                new_items.append(item)  # Keep original (e.g., FUTURE_USE)

        # Apply token-level corrections (fixes PA docs typos in Variable Name column)
        new_items = [self.token_corrections.get(item, item) for item in new_items]

        return new_items

    def _apply_per_log_corrections(self, items: list, log_type_name: str) -> list:
        """Apply strip and position- or value-based corrections for a specific log type."""
        if self.strip_leading_future_use and items and items[0] == "FUTURE_USE":
            items = items[1:]

        for correction in self.per_log_corrections.get(log_type_name, []):
            if 'match' in correction:
                target = correction['match']
                try:
                    pos = items.index(target)
                except ValueError:
                    logger.warning(
                        f"Per-log correction for {log_type_name}: "
                        f"match '{target}' not found in items; skipping."
                    )
                    continue
            elif 'position' in correction:
                pos = correction['position']
                if pos < 0 or pos >= len(items):
                    logger.warning(
                        f"Per-log correction for {log_type_name} has out-of-bounds "
                        f"position {pos} (list length {len(items)}); skipping."
                    )
                    continue
            else:
                logger.warning(
                    f"Per-log correction for {log_type_name} has neither "
                    f"'position' nor 'match' key; skipping."
                )
                continue

            if 'new' in correction:
                items[pos] = correction['new']
            elif 'split_into' in correction:
                items = items[:pos] + correction['split_into'] + items[pos + 1:]

        return items

    def _get_cell_text_with_formatting(self, cell) -> str:
        """
        Extract text from a BeautifulSoup cell while preserving
        line breaks from HTML block elements.

        Uses BS4 tree traversal to avoid regex manipulation of raw HTML.
        """
        BLOCK_TAGS = frozenset({'p', 'div', 'li', 'dt', 'dd', 'tr',
                                 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'})
        LIST_TAGS = frozenset({'ul', 'ol', 'dl'})
        parts = []

        def _walk(node):
            if isinstance(node, NavigableString):
                # Collapse source-formatting whitespace in text nodes
                parts.append(re.sub(r'\s+', ' ', str(node)))
            elif isinstance(node, Tag):
                name = node.name.lower() if node.name else ''
                if name == 'br':
                    parts.append('\n')
                elif name in BLOCK_TAGS or name in LIST_TAGS:
                    parts.append('\n')
                    for child in node.children:
                        _walk(child)
                    parts.append('\n')
                else:
                    for child in node.children:
                        _walk(child)

        for child in cell.children:
            _walk(child)

        text = ''.join(parts)
        # Collapse horizontal whitespace (preserve newlines)
        text = re.sub(r'[^\S\n]+', ' ', text)
        # Limit consecutive newlines
        text = re.sub(r'\n{3,}', '\n\n', text)
        # Strip trailing whitespace from each line
        lines = [line.strip() for line in text.split('\n')]
        return '\n'.join(lines).strip()

    def extract_field_table(self, soup: BeautifulSoup) -> Optional[pd.DataFrame]:
        """
        Extract the field description table from the page

        Args:
            soup: BeautifulSoup object of the page

        Returns:
            DataFrame with field descriptions or None if not found
        """
        # Look for tables that contain field descriptions
        tables = soup.find_all('table')

        for table in tables:
            # Check if this table contains field information
            headers = [th.get_text(strip=True).lower() for th in table.find_all('th')]

            # Look for tables with "field name" and "description" headers
            if 'field name' in ' '.join(headers) or 'field' in ' '.join(headers):
                try:
                    # Extract table data
                    data = []
                    rows = table.find_all('tr')

                    if not rows:
                        continue

                    # Get headers
                    header_row = rows[0]
                    headers = [th.get_text(strip=True) for th in header_row.find_all(['th', 'td'])]

                    # Get data rows
                    for row in rows[1:]:
                        cells = row.find_all(['td', 'th'])
                        if len(cells) >= len(headers):
                            row_data = [self._get_cell_text_with_formatting(cell) for cell in cells[:len(headers)]]
                            data.append(row_data)

                    if data:
                        df = pd.DataFrame(data, columns=headers)

                        # Add Variable Name column extracted from Field Name
                        if 'Field Name' in df.columns:
                            variable_names = [self._extract_variable_name(fn) for fn in df['Field Name']]
                            # Insert after Field Name column
                            field_name_idx = df.columns.get_loc('Field Name')
                            df.insert(field_name_idx + 1, 'Variable Name', variable_names)

                        logger.info(f"Extracted field table: {len(df)} rows")
                        return df

                except Exception as e:
                    logger.error(f"Error parsing field table: {e}")
                    continue

        logger.warning("No field description table found")
        return None

    def scrape_log_type(self, log_type: dict, version_dir: str) -> bool:
        """
        Scrape a specific log type and save format and table files.

        Args:
            log_type: Dictionary with 'name' and 'url' keys
            version_dir: Directory to save files

        Returns:
            True if both format string and field table were saved successfully.
            A warning is logged when only one of the two was available (partial result).
        """
        logger.info(f"Processing log type: {log_type['name']}")

        soup = self.get_page_content(log_type['url'])
        if not soup:
            logger.error(f"Failed to fetch page for {log_type['name']}")
            return False

        # Extract format string and field table
        format_string = self.extract_format_string(soup)
        field_table = self.extract_field_table(soup)

        if field_table is not None:
            field_table = self._apply_field_table_corrections(field_table)
            table_filepath = os.path.join(version_dir, f"{log_type['name']}_fields.csv")
            try:
                field_table.to_csv(table_filepath, index=False)
                logger.info(f"Saved field table to {table_filepath}")
            except Exception as e:
                logger.error(f"Error saving field table: {e}")

        if format_string:
            format_filepath = os.path.join(version_dir, f"{log_type['name']}_format.csv")

            if field_table is not None:
                name_map = self._build_name_map(field_table)
                items = self._transform_format_string(format_string, name_map)
                items = self._apply_per_log_corrections(items, log_type['name'])
                transformed = ",".join(f'"{item}"' for item in items)
            else:
                transformed = None

            try:
                with open(format_filepath, 'w', encoding='utf-8') as f:
                    f.write(f"{format_string}\n")
                    if transformed:
                        f.write(f"{transformed}\n")
                logger.info(f"Saved format to {format_filepath}"
                            + ("" if transformed else " (no transformation - field table missing)"))
            except Exception as e:
                logger.error(f"Error saving format file: {e}")

        # Warn on partial results
        if format_string is None and field_table is not None:
            logger.warning(f"{log_type['name']}: field table saved but no format string found")
        elif format_string is not None and field_table is None:
            logger.warning(f"{log_type['name']}: format string saved without field table (no transformation)")

        return format_string is not None and field_table is not None

    def _build_consolidated_matrix(self, version_dir: str, log_types: list) -> None:
        """Build the consolidated position × log type matrix and save to panos_syslog_fields.csv.

        Reads line 2 of each *_format.csv (the variable-name transformed line), aligns all
        log types by position index, and writes the matrix with log type names as columns.
        Log types whose format file is missing or has no transformed line are skipped with
        a warning. Positions beyond a log type's last field are left empty.
        """
        columns = {}   # display_name → list of tokens
        ordered_names = []

        for log_type in log_types:
            name = log_type['name']
            format_path = os.path.join(version_dir, f"{name}_format.csv")

            if not os.path.exists(format_path):
                logger.warning(f"Matrix: no format file for {name}, skipping column")
                continue

            try:
                with open(format_path, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
            except Exception as e:
                logger.error(f"Matrix: cannot read {format_path}: {e}")
                continue

            if len(lines) < 2 or not lines[1].strip():
                logger.warning(f"Matrix: {name}_format.csv has no transformed line 2, skipping column")
                continue

            try:
                tokens = next(csv.reader([lines[1].strip()]))
            except Exception as e:
                logger.error(f"Matrix: cannot parse {name}_format.csv line 2: {e}")
                continue

            # Display name: strip _Log suffix, replace remaining underscores with spaces
            display_name = re.sub(r'_Log$', '', name).replace('_', ' ')
            ordered_names.append(display_name)
            columns[display_name] = tokens

        if not columns:
            logger.warning("Matrix: no valid format files found, skipping panos_syslog_fields.csv")
            return

        max_len = max(len(v) for v in columns.values())
        data = {n: columns[n] + [''] * (max_len - len(columns[n])) for n in ordered_names}

        df = pd.DataFrame(data, columns=ordered_names)
        matrix_path = os.path.join(version_dir, 'panos_syslog_fields.csv')
        try:
            df.to_csv(matrix_path, index=False)
            logger.info(
                f"Saved consolidated matrix to {matrix_path} "
                f"({max_len} rows × {len(ordered_names)} columns)"
            )
        except Exception as e:
            logger.error(f"Matrix: cannot save {matrix_path}: {e}")

    def scrape_version(self, version: dict) -> int:
        """
        Scrape all log types for a specific PAN-OS version

        Args:
            version: Version dictionary with 'name' and 'log_types' keys

        Returns:
            Number of successfully processed log types
        """
        logger.info(f"Starting scrape for PAN-OS version {version['name']}")

        # Create version directory
        version_dir = self.get_version_directory(version['name'])
        os.makedirs(version_dir, exist_ok=True)

        # Process each log type
        successful_count = 0

        for log_type in version['log_types']:
            if self.scrape_log_type(log_type, version_dir):
                successful_count += 1

        self._build_consolidated_matrix(version_dir, version['log_types'])

        return successful_count

    def run(self, specific_versions: Optional[List[dict]] = None):
        """
        Run the complete scraping process

        Args:
            specific_versions: Optional list of specific versions to scrape
        """
        # Determine which versions to scrape
        if specific_versions:
            # If specific versions provided, use them directly
            versions_to_scrape = specific_versions
            logger.info(f"Using {len(specific_versions)} specific versions provided by caller")
        else:
            # Otherwise, use the smart filtering based on force_rescrape flag
            versions_to_scrape = self._get_versions_to_scrape()

        logger.info(f"Starting scrape for {len(versions_to_scrape)} versions")

        # Dry run mode - just print what would be scraped
        if self.dry_run:
            logger.info("=" * 60)
            logger.info("DRY RUN MODE - No actual scraping will be performed")
            logger.info("=" * 60)
            logger.info(f"\nVersions that would be scraped ({len(versions_to_scrape)} total):")
            for i, version in enumerate(versions_to_scrape, 1):
                version_dir = self.get_version_directory(version['name'])
                logger.info(f"  {i}. Version {version['name']} -> {version_dir}")
                for log_type in version['log_types']:
                    logger.info(f"      - {log_type['name']}: {log_type['url']}")
            logger.info("\n" + "=" * 60)
            logger.info(f"Total versions to scrape: {len(versions_to_scrape)}")
            logger.info("=" * 60)
            return

        total_processed = 0
        for version in versions_to_scrape:
            try:
                logger.info(f"Processing version {version['name']}")
                successful_count = self.scrape_version(version)
                total_processed += successful_count
                logger.info(f"Completed version {version['name']} - {successful_count} log types processed")

                # Configurable pause between versions (fix #11: was hardcoded to 2s)
                time.sleep(self.inter_version_delay)

            except Exception as e:
                logger.error(f"Error processing version {version['name']}: {e}")
                continue

        logger.info(f"Scraping completed! Total log types processed: {total_processed}")


def main():
    """Main execution function"""
    # Fix #1: no longer hardcoding base_delay=1.0; let the config file value take effect
    scraper = PaloAltoLogScraper()

    # Scrape all versions from config
    scraper.run()

if __name__ == "__main__":
    main()
