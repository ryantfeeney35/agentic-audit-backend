"""Tests for ESPI parser enhancements (Group 4)."""

import pytest
import xml.etree.ElementTree as ET
from datetime import datetime

from utils.green_button import (
    map_reading_quality,
    extract_customer_obfuscated_key,
    parse_espi_multi_usage_points,
    parse_local_time_parameters,
    convert_interval_to_utc,
    parse_espi_filename,
    parse_espi_atom_xml,
    NAMESPACES,
    LocalTimeParameters,
)


# ---------------------------------------------------------------------------
# ReadingQuality mapping (4.1)
# ---------------------------------------------------------------------------

class TestReadingQuality:

    def test_measured_codes(self):
        assert map_reading_quality('0') == 'measured'
        assert map_reading_quality('1') == 'measured'
        assert map_reading_quality('14') == 'measured'
        assert map_reading_quality('19') == 'measured'

    def test_estimated_codes(self):
        assert map_reading_quality('7') == 'estimated'
        assert map_reading_quality('8') == 'estimated'
        assert map_reading_quality('9') == 'estimated'
        assert map_reading_quality('10') == 'estimated'
        assert map_reading_quality('17') == 'estimated'

    def test_projected_code(self):
        assert map_reading_quality('12') == 'projected'

    def test_unknown_code(self):
        assert map_reading_quality('999') == 'unknown'

    def test_quality_in_parsed_interval(self):
        xml = """
        <feed xmlns="http://www.w3.org/2005/Atom" xmlns:espi="http://naesb.org/espi">
          <entry>
            <content>
              <espi:IntervalBlock>
                <espi:IntervalReading>
                  <espi:timePeriod><espi:start>1704067200</espi:start><espi:duration>3600</espi:duration></espi:timePeriod>
                  <espi:value>500</espi:value>
                  <espi:ReadingQuality><espi:quality>7</espi:quality></espi:ReadingQuality>
                </espi:IntervalReading>
              </espi:IntervalBlock>
            </content>
          </entry>
        </feed>
        """
        result = parse_espi_atom_xml(xml)
        assert len(result['intervals']) == 1
        assert result['intervals'][0]['quality'] == 'estimated'


# ---------------------------------------------------------------------------
# COK extraction (4.2)
# ---------------------------------------------------------------------------

class TestCOKExtraction:

    def test_extracts_from_retail_customer_link(self):
        xml = """
        <feed xmlns="http://www.w3.org/2005/Atom" xmlns:espi="http://naesb.org/espi">
          <link rel="related" href="https://soagwx.sempra.com:3443/DataCustodian/espi/1_1/resource/RetailCustomer/12345"/>
        </feed>
        """
        root = ET.fromstring(xml)
        assert extract_customer_obfuscated_key(root) == '12345'

    def test_returns_none_when_missing(self):
        xml = """
        <feed xmlns="http://www.w3.org/2005/Atom" xmlns:espi="http://naesb.org/espi">
          <link rel="self" href="https://example.com/UsagePoint/1"/>
        </feed>
        """
        root = ET.fromstring(xml)
        assert extract_customer_obfuscated_key(root) is None


# ---------------------------------------------------------------------------
# Multi-UsagePoint (4.3)
# ---------------------------------------------------------------------------

class TestMultiUsagePoint:

    def test_single_usage_point_fallback(self):
        xml = """
        <feed xmlns="http://www.w3.org/2005/Atom" xmlns:espi="http://naesb.org/espi">
          <entry>
            <content>
              <espi:IntervalBlock>
                <espi:IntervalReading>
                  <espi:timePeriod><espi:start>1704067200</espi:start><espi:duration>3600</espi:duration></espi:timePeriod>
                  <espi:value>1000</espi:value>
                </espi:IntervalReading>
              </espi:IntervalBlock>
            </content>
          </entry>
        </feed>
        """
        results = parse_espi_multi_usage_points(xml)
        assert len(results) == 1
        assert results[0]['customer_obfuscated_key'] is None

    def test_invalid_xml(self):
        results = parse_espi_multi_usage_points("<not valid xml")
        assert len(results) == 1
        assert 'error' in results[0]


# ---------------------------------------------------------------------------
# LocalTimeParameters (4.4)
# ---------------------------------------------------------------------------

class TestLocalTimeParameters:

    def test_parses_local_time_params(self):
        xml = """
        <feed xmlns="http://www.w3.org/2005/Atom" xmlns:espi="http://naesb.org/espi">
          <entry>
            <content>
              <espi:LocalTimeParameters>
                <espi:dstOffset>3600</espi:dstOffset>
                <espi:tzOffset>-28800</espi:tzOffset>
                <espi:dstStartRule>360E2000</espi:dstStartRule>
                <espi:dstEndRule>B40E2000</espi:dstEndRule>
              </espi:LocalTimeParameters>
            </content>
          </entry>
        </feed>
        """
        root = ET.fromstring(xml)
        params = parse_local_time_parameters(root)
        assert params is not None
        assert params.dst_offset == 3600
        assert params.tz_offset == -28800
        assert params.dst_start_rule == '360E2000'
        assert params.dst_end_rule == 'B40E2000'

    def test_returns_none_when_missing(self):
        xml = '<feed xmlns="http://www.w3.org/2005/Atom" xmlns:espi="http://naesb.org/espi"></feed>'
        root = ET.fromstring(xml)
        assert parse_local_time_parameters(root) is None

    def test_convert_interval_to_utc(self):
        interval = {'start': '2024-01-01T08:00:00', 'value': 1.0}
        params = LocalTimeParameters(tz_offset=-28800)  # PST = UTC-8
        result = convert_interval_to_utc(interval, params)
        assert result['start'] == '2024-01-01T16:00:00'

    def test_no_params_returns_unchanged(self):
        interval = {'start': '2024-01-01T08:00:00', 'value': 1.0}
        result = convert_interval_to_utc(interval, None)
        assert result['start'] == '2024-01-01T08:00:00'


# ---------------------------------------------------------------------------
# Filename parser (4.5)
# ---------------------------------------------------------------------------

class TestFilenameParser:

    def test_daily_file(self):
        name = "CEN_D_SUSTAINRGY_12345_CONSUMPTION_20260101_01012026120000_ESPI2-1_01-01.xml"
        result = parse_espi_filename(name)
        assert result is not None
        assert result['job_type'] == 'D'
        assert result['third_party_name'] == 'SUSTAINRGY'
        assert result['third_party_id'] == '12345'
        assert result['execution_date'] == '20260101'
        assert result['transaction_id'] == '01012026120000'
        assert result['espi_version'] == '2-1'
        assert result['split_position'] == 1
        assert result['split_total'] == 1

    def test_historical_split_file(self):
        name = "CEN_H_SUSTAINRGY_12345_CONSUMPTION_20260101_01012026120000_ESPI2-1_02-05.xml"
        result = parse_espi_filename(name)
        assert result is not None
        assert result['job_type'] == 'H'
        assert result['split_position'] == 2
        assert result['split_total'] == 5

    def test_correction_file(self):
        name = "CEN_C_SUSTAINRGY_12345_CONSUMPTION_20260301_03012026080000_ESPI2-1_01-01.xml"
        result = parse_espi_filename(name)
        assert result is not None
        assert result['job_type'] == 'C'

    def test_non_matching_filename(self):
        assert parse_espi_filename("random_file.xml") is None
        assert parse_espi_filename("SUBSCRIPTIONS_20260101.CSV") is None
