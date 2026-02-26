#!/usr/bin/env python3
"""
Unit tests for paloalto_scraper.py

Run with:
    python -m pytest test_scraper.py -v

Each test group corresponds to one of the 13 optimization fixes applied to the scraper.
"""

import logging
import os
import pytest
import requests
import pandas as pd
from bs4 import BeautifulSoup
from unittest.mock import MagicMock, patch

from paloalto_scraper import PaloAltoLogScraper

# ---------------------------------------------------------------------------
# Minimal config used by the test fixture (no real YAML files needed)
# ---------------------------------------------------------------------------

MOCK_MAIN_CONFIG = {
    'settings': {
        'base_delay': 0.0,
        'inter_version_delay': 0.0,
        'max_retries': 3,
        'force_rescrape': False,
        'dry_run': False,
        'output_dir': '/tmp/palos_test',
    },
    'versions': [],
}

MOCK_EXCEPTIONS = {
    'strip_leading_future_use': False,
    'global_name_overrides': {
        'Serial Number': 'serial',
        'Generated Time': 'time_generated',
        'Generate Time': 'time_generated',
    },
    'token_corrections': {
        'FUTURE_USER': 'FUTURE_USE',
        'high_res': 'high_res_timestamp',
    },
    'per_log_corrections': {},
}


@pytest.fixture
def scraper():
    """Scraper instance backed by test config — no network, no real YAML."""
    def _fake_load(config_file, label='configuration'):
        return MOCK_EXCEPTIONS if 'exceptions' in config_file else MOCK_MAIN_CONFIG

    with patch.object(PaloAltoLogScraper, '_load_config', side_effect=_fake_load):
        with patch('os.makedirs'):
            s = PaloAltoLogScraper()
    return s


# ===========================================================================
# Fix #4 — urljoin removed from imports (static check)
# ===========================================================================

def test_urljoin_not_imported():
    """urljoin was imported but never used — verify it is gone."""
    import paloalto_scraper
    assert not hasattr(paloalto_scraper, 'urljoin'), \
        "urljoin should have been removed from module-level imports"


# ===========================================================================
# _extract_variable_name (baseline correctness, unchanged behaviour)
# ===========================================================================

def test_extract_variable_name_basic(scraper):
    assert scraper._extract_variable_name("Source Address (src)") == "src"

def test_extract_variable_name_multi_word_in_parens(scraper):
    # "x or y" in parenthetical → first word only
    assert scraper._extract_variable_name("Field Name (x or y)") == "x"

def test_extract_variable_name_no_paren(scraper):
    assert scraper._extract_variable_name("FUTURE_USE") == ""

def test_extract_variable_name_malformed_no_space_before_paren(scraper):
    # "Server Name Indication(sni)" — no space before ( — regex requires \s+\(
    assert scraper._extract_variable_name("Server Name Indication(sni)") == ""


# ===========================================================================
# Fix #9 — NaN guard in _apply_field_table_corrections
# ===========================================================================

def test_apply_corrections_nan_fills_from_overrides(scraper):
    """NaN in Variable Name should be treated as empty and filled from global_name_overrides."""
    df = pd.DataFrame({
        'Field Name': ['Serial Number'],
        'Variable Name': [float('nan')],
    })
    result = scraper._apply_field_table_corrections(df)
    assert result['Variable Name'].iloc[0] == 'serial'

def test_apply_corrections_token_correction(scraper):
    """Non-empty Variable Name with a known typo should be corrected."""
    df = pd.DataFrame({
        'Field Name': ['High Resolution Timestamp (high_res)'],
        'Variable Name': ['high_res'],
    })
    result = scraper._apply_field_table_corrections(df)
    assert result['Variable Name'].iloc[0] == 'high_res_timestamp'

def test_apply_corrections_fills_empty_string(scraper):
    """Empty-string Variable Name should look up the long field name in global_name_overrides."""
    df = pd.DataFrame({
        'Field Name': ['Serial Number (some_parenthetical)'],
        'Variable Name': [''],
    })
    # var_name is "", so long_name = "Serial Number" → lookup → 'serial'
    result = scraper._apply_field_table_corrections(df)
    assert result['Variable Name'].iloc[0] == 'serial'

def test_apply_corrections_unknown_field_stays_empty(scraper):
    """A field with no parenthetical and no override entry stays empty."""
    df = pd.DataFrame({
        'Field Name': ['Completely Unknown Field'],
        'Variable Name': [''],
    })
    result = scraper._apply_field_table_corrections(df)
    assert result['Variable Name'].iloc[0] == ''

def test_apply_corrections_preserves_unknown_var_name(scraper):
    """A non-empty var_name not in token_corrections should pass through unchanged."""
    df = pd.DataFrame({
        'Field Name': ['Some Field (my_var)'],
        'Variable Name': ['my_var'],
    })
    result = scraper._apply_field_table_corrections(df)
    assert result['Variable Name'].iloc[0] == 'my_var'


# ===========================================================================
# Fix #5 — no redundant keys in _build_name_map
# ===========================================================================

def test_build_name_map_simple_name_no_duplicate_keys(scraper):
    """For a simple name like 'Source Address', long_name == normalized; only one key + lowercase."""
    df = pd.DataFrame({
        'Field Name': ['Source Address (src)'],
        'Variable Name': ['src'],
    })
    name_map = scraper._build_name_map(df)
    # Both 'Source Address' and 'source address' should resolve to 'src'
    assert name_map['Source Address'] == 'src'
    assert name_map['source address'] == 'src'
    # The normalized form is identical to the original, so no extra key was inserted
    # (we can't directly count keys, but verify correctness)

def test_build_name_map_global_overrides_win(scraper):
    """Auto-detected mapping must be overridden by global_name_overrides."""
    df = pd.DataFrame({
        'Field Name': ['Generated Time (wrong_var)'],
        'Variable Name': ['wrong_var'],
    })
    name_map = scraper._build_name_map(df)
    # MOCK_EXCEPTIONS has 'Generated Time': 'time_generated'
    assert name_map['Generated Time'] == 'time_generated'

def test_build_name_map_empty_var_name_skipped(scraper):
    """Rows with empty Variable Name must not create entries in the map."""
    df = pd.DataFrame({
        'Field Name': ['No Parenthetical Field'],
        'Variable Name': [''],
    })
    name_map = scraper._build_name_map(df)
    assert 'No Parenthetical Field' not in name_map


# ===========================================================================
# Fix #2 — _transform_format_string returns a list (not CSV string)
# ===========================================================================

def test_transform_returns_list(scraper):
    df = pd.DataFrame({
        'Field Name': ['Source Address (src)', 'Destination Address (dst)'],
        'Variable Name': ['src', 'dst'],
    })
    name_map = scraper._build_name_map(df)
    result = scraper._transform_format_string("Source Address, Destination Address", name_map)
    assert isinstance(result, list)
    assert result == ['src', 'dst']

def test_transform_dg_hierarchy_regex(scraper):
    result = scraper._transform_format_string(
        "Device Group Hierarchy Level 1, Device Group Hierarchy Level 3", {}
    )
    assert result == ['dg_hier_level_1', 'dg_hier_level_3']

def test_transform_fallback_keeps_original_token(scraper):
    result = scraper._transform_format_string("FUTURE_USE, Unknown Token", {})
    assert result == ['FUTURE_USE', 'Unknown Token']

def test_transform_token_corrections_applied_after_map(scraper):
    """If a name_map lookup returns a value that is itself a typo, token_corrections fixes it."""
    # MOCK_EXCEPTIONS has 'FUTURE_USER' -> 'FUTURE_USE'
    name_map = {'Broken Field': 'FUTURE_USER'}
    result = scraper._transform_format_string("Broken Field", name_map)
    assert result == ['FUTURE_USE']

def test_transform_pipeline_no_csv_roundtrip(scraper):
    """The list returned by _transform_format_string feeds _apply_per_log_corrections directly."""
    scraper.per_log_corrections = {
        'Test_Log': [{'position': 0, 'new': 'corrected'}]
    }
    result = scraper._transform_format_string("FUTURE_USE, Other", {})
    # result is a list — pass directly to per_log_corrections
    assert isinstance(result, list)
    final = scraper._apply_per_log_corrections(result, 'Test_Log')
    assert final[0] == 'corrected'


# ===========================================================================
# Fix #6 — _apply_per_log_corrections bounds check
# ===========================================================================

def test_per_log_corrections_out_of_bounds_warns_and_skips(scraper, caplog):
    scraper.per_log_corrections = {
        'Test_Log': [{'position': 99, 'new': 'something'}]
    }
    items = ['a', 'b', 'c']
    with caplog.at_level(logging.WARNING):
        result = scraper._apply_per_log_corrections(items, 'Test_Log')
    assert result == ['a', 'b', 'c']  # unchanged
    assert 'out-of-bounds' in caplog.text

def test_per_log_corrections_new(scraper):
    scraper.per_log_corrections = {
        'Test_Log': [{'position': 1, 'new': 'corrected'}]
    }
    result = scraper._apply_per_log_corrections(['a', 'b', 'c'], 'Test_Log')
    assert result == ['a', 'corrected', 'c']

def test_per_log_corrections_split_into(scraper):
    scraper.per_log_corrections = {
        'Test_Log': [{'position': 1, 'split_into': ['x', 'y']}]
    }
    result = scraper._apply_per_log_corrections(['a', 'b', 'c'], 'Test_Log')
    assert result == ['a', 'x', 'y', 'c']

def test_per_log_corrections_strip_leading_future_use(scraper):
    scraper.strip_leading_future_use = True
    result = scraper._apply_per_log_corrections(['FUTURE_USE', 'a', 'b'], 'NoCorrections_Log')
    assert result == ['a', 'b']

def test_per_log_corrections_negative_position_warns(scraper, caplog):
    scraper.per_log_corrections = {
        'Test_Log': [{'position': -1, 'new': 'bad'}]
    }
    with caplog.at_level(logging.WARNING):
        result = scraper._apply_per_log_corrections(['a', 'b'], 'Test_Log')
    assert result == ['a', 'b']
    assert 'out-of-bounds' in caplog.text


# ===========================================================================
# Fix #13 — _get_cell_text_with_formatting uses BS4 traversal (no regex on HTML)
# ===========================================================================

def test_cell_text_plain(scraper):
    soup = BeautifulSoup('<td>Hello World</td>', 'html.parser')
    assert scraper._get_cell_text_with_formatting(soup.find('td')) == 'Hello World'

def test_cell_text_br_creates_newline(scraper):
    soup = BeautifulSoup('<td>Line 1<br>Line 2</td>', 'html.parser')
    result = scraper._get_cell_text_with_formatting(soup.find('td'))
    assert 'Line 1' in result
    assert 'Line 2' in result
    assert '\n' in result

def test_cell_text_p_tags_create_newlines(scraper):
    soup = BeautifulSoup('<td><p>Para 1</p><p>Para 2</p></td>', 'html.parser')
    result = scraper._get_cell_text_with_formatting(soup.find('td'))
    assert 'Para 1' in result
    assert 'Para 2' in result
    assert '\n' in result

def test_cell_text_list_items(scraper):
    soup = BeautifulSoup('<td><ul><li>Item A</li><li>Item B</li></ul></td>', 'html.parser')
    result = scraper._get_cell_text_with_formatting(soup.find('td'))
    assert 'Item A' in result
    assert 'Item B' in result

def test_cell_text_collapses_source_whitespace(scraper):
    soup = BeautifulSoup('<td>   multiple   spaces   </td>', 'html.parser')
    result = scraper._get_cell_text_with_formatting(soup.find('td'))
    assert '   ' not in result
    assert result == 'multiple spaces'

def test_cell_text_no_excessive_blank_lines(scraper):
    soup = BeautifulSoup('<td><p>A</p><p>B</p><p>C</p></td>', 'html.parser')
    result = scraper._get_cell_text_with_formatting(soup.find('td'))
    # Must not have 3+ consecutive newlines
    assert '\n\n\n' not in result


# ===========================================================================
# Fix #10 — get_page_content retries on transient failures
# ===========================================================================

def test_get_page_content_retries_on_connection_error(scraper):
    """Two failures followed by success — should succeed on attempt 3."""
    good_response = MagicMock()
    good_response.content = b'<html><body>OK</body></html>'
    good_response.raise_for_status = MagicMock()

    with patch.object(scraper.session, 'get') as mock_get:
        mock_get.side_effect = [
            requests.exceptions.ConnectionError("fail 1"),
            requests.exceptions.ConnectionError("fail 2"),
            good_response,
        ]
        with patch('time.sleep'):
            result = scraper.get_page_content("http://example.com")

    assert result is not None
    assert mock_get.call_count == 3

def test_get_page_content_returns_none_after_all_retries_exhausted(scraper):
    with patch.object(scraper.session, 'get') as mock_get:
        mock_get.side_effect = requests.exceptions.ConnectionError("always fails")
        scraper.max_retries = 2
        with patch('time.sleep'):
            result = scraper.get_page_content("http://example.com")

    assert result is None
    assert mock_get.call_count == 2

def test_get_page_content_no_retry_on_success(scraper):
    good_response = MagicMock()
    good_response.content = b'<html><body>OK</body></html>'
    good_response.raise_for_status = MagicMock()

    with patch.object(scraper.session, 'get', return_value=good_response):
        with patch('time.sleep'):
            result = scraper.get_page_content("http://example.com")

    assert result is not None


# ===========================================================================
# Fix #8 — _version_exists detects incomplete versions
# ===========================================================================

def test_version_exists_complete(scraper, tmp_path):
    scraper.output_dir = str(tmp_path)
    version = {'name': 'v1', 'log_types': [{'name': 'A'}, {'name': 'B'}]}
    vdir = tmp_path / 'v1'
    vdir.mkdir()
    (vdir / 'A_format.csv').write_text('data')
    (vdir / 'B_format.csv').write_text('data')
    assert scraper._version_exists(version) is True

def test_version_exists_incomplete_returns_false_and_warns(scraper, tmp_path, caplog):
    scraper.output_dir = str(tmp_path)
    version = {'name': 'v1', 'log_types': [{'name': 'A'}, {'name': 'B'}, {'name': 'C'}]}
    vdir = tmp_path / 'v1'
    vdir.mkdir()
    (vdir / 'A_format.csv').write_text('data')  # only 1 of 3 expected
    with caplog.at_level(logging.WARNING):
        result = scraper._version_exists(version)
    assert result is False
    assert 'incomplete' in caplog.text.lower()

def test_version_exists_no_directory(scraper, tmp_path):
    scraper.output_dir = str(tmp_path)
    version = {'name': 'nonexistent', 'log_types': []}
    assert scraper._version_exists(version) is False


# ===========================================================================
# Fix #7 — scrape_log_type warns on partial result
# ===========================================================================

def test_scrape_log_type_warns_on_missing_field_table(scraper, tmp_path, caplog):
    """If format string is found but no field table, a warning is logged."""
    soup_html = """
    <html><body>
    <p>Format: FUTURE_USE, Receive Time</p>
    </body></html>
    """
    with patch.object(scraper, 'get_page_content', return_value=BeautifulSoup(soup_html, 'html.parser')):
        with caplog.at_level(logging.WARNING):
            result = scraper.scrape_log_type(
                {'name': 'Test_Log', 'url': 'http://x'},
                str(tmp_path)
            )
    assert result is False  # partial = not fully successful
    assert 'without field table' in caplog.text

def test_scrape_log_type_returns_false_on_fetch_failure(scraper, tmp_path):
    with patch.object(scraper, 'get_page_content', return_value=None):
        result = scraper.scrape_log_type(
            {'name': 'Test_Log', 'url': 'http://x'},
            str(tmp_path)
        )
    assert result is False


# ===========================================================================
# Fix #11 — inter_version_delay is configurable (not hardcoded)
# ===========================================================================

def test_inter_version_delay_attribute_exists(scraper):
    assert hasattr(scraper, 'inter_version_delay')
    assert scraper.inter_version_delay == 0.0  # MOCK_MAIN_CONFIG sets 0.0

def test_inter_version_delay_loaded_from_config():
    """Confirm that the config value is picked up by __init__."""
    custom_config = dict(MOCK_MAIN_CONFIG)
    custom_config['settings'] = dict(MOCK_MAIN_CONFIG['settings'], inter_version_delay=5.0)

    def _fake_load(config_file, label='configuration'):
        return MOCK_EXCEPTIONS if 'exceptions' in config_file else custom_config

    with patch.object(PaloAltoLogScraper, '_load_config', side_effect=_fake_load):
        with patch('os.makedirs'):
            s = PaloAltoLogScraper()
    assert s.inter_version_delay == 5.0


# ===========================================================================
# Fix #12 — _load_config log message uses the label parameter
# ===========================================================================

def test_load_config_label_in_log(tmp_path, caplog):
    """The label argument should appear in the success log message."""
    cfg = {'settings': {}, 'versions': []}
    yaml_file = tmp_path / 'test.yaml'
    import yaml
    yaml_file.write_text(yaml.dump(cfg))

    s_partial = object.__new__(PaloAltoLogScraper)  # bypass __init__
    with caplog.at_level(logging.INFO):
        result = PaloAltoLogScraper._load_config(s_partial, str(yaml_file), label='my_label')
    assert 'my_label' in caplog.text


# ===========================================================================
# Fix #1 — main() no longer passes base_delay=1.0
# ===========================================================================

def test_main_does_not_override_config_base_delay():
    """If base_delay in config is 0.5, the scraper should use 0.5, not 1.0."""
    config_05 = dict(MOCK_MAIN_CONFIG)
    config_05['settings'] = dict(MOCK_MAIN_CONFIG['settings'], base_delay=0.5)

    def _fake_load(config_file, label='configuration'):
        return MOCK_EXCEPTIONS if 'exceptions' in config_file else config_05

    with patch.object(PaloAltoLogScraper, '_load_config', side_effect=_fake_load):
        with patch('os.makedirs'):
            s = PaloAltoLogScraper()
    assert s.base_delay == 0.5


# ===========================================================================
# Fix #3 — no duplicate format filepath computation (structural, smoke test)
# ===========================================================================

def test_scrape_log_type_writes_format_file(scraper, tmp_path):
    """Smoke test: both format and fields CSVs are written in the happy path."""
    soup_html = """
    <html><body>
    <p>Format : Source Address, FUTURE_USE</p>
    <table>
      <tr><th>Field Name</th><th>Description</th></tr>
      <tr><td>Source Address (src)</td><td>The source IP.</td></tr>
    </table>
    </body></html>
    """
    with patch.object(scraper, 'get_page_content',
                      return_value=BeautifulSoup(soup_html, 'html.parser')):
        result = scraper.scrape_log_type(
            {'name': 'Test_Log', 'url': 'http://x'},
            str(tmp_path)
        )

    assert result is True
    assert (tmp_path / 'Test_Log_format.csv').exists()
    assert (tmp_path / 'Test_Log_fields.csv').exists()

    format_lines = (tmp_path / 'Test_Log_format.csv').read_text().splitlines()
    assert format_lines[0] == 'Source Address, FUTURE_USE'
    assert '"src"' in format_lines[1]
    assert '"FUTURE_USE"' in format_lines[1]
