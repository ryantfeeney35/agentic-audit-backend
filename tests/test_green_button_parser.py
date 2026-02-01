# backend/tests/test_green_button_parser.py
"""
Tests for Green Button ESPI/Atom XML Parser

Tests parsing, normalization, and data quality detection
for utility usage data from SDG&E and other ESPI providers.
"""

import pytest
from datetime import datetime

from utils.green_button import (
    parse_espi_atom_xml,
    normalize_usage_to_summary,
    detect_data_quality_issues,
    parse_aggregator_response,
)


# Sample ESPI Atom XML (simplified SDG&E format)
SAMPLE_ESPI_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:espi="http://naesb.org/espi">
  <title>Green Button Data</title>
  <updated>2024-12-01T00:00:00Z</updated>
  <entry>
    <title>ElectricMeter Reading</title>
    <content>
      <espi:MeterReading>
        <espi:mRID>meter-12345</espi:mRID>
      </espi:MeterReading>
    </content>
  </entry>
  <entry>
    <title>Service Category</title>
    <content>
      <espi:ServiceCategory>
        <espi:kind>0</espi:kind>
      </espi:ServiceCategory>
    </content>
  </entry>
  <entry>
    <title>Reading Type</title>
    <content>
      <espi:ReadingType>
        <espi:uom>72</espi:uom>
      </espi:ReadingType>
    </content>
  </entry>
  <entry>
    <title>Interval Block</title>
    <content>
      <espi:IntervalBlock>
        <espi:IntervalReading>
          <espi:timePeriod>
            <espi:start>1704067200</espi:start>
            <espi:duration>3600</espi:duration>
          </espi:timePeriod>
          <espi:value>500</espi:value>
          <espi:cost>10</espi:cost>
          <espi:ReadingQuality>
            <espi:quality>0</espi:quality>
          </espi:ReadingQuality>
        </espi:IntervalReading>
        <espi:IntervalReading>
          <espi:timePeriod>
            <espi:start>1704070800</espi:start>
            <espi:duration>3600</espi:duration>
          </espi:timePeriod>
          <espi:value>750</espi:value>
          <espi:cost>15</espi:cost>
        </espi:IntervalReading>
        <espi:IntervalReading>
          <espi:timePeriod>
            <espi:start>1706745600</espi:start>
            <espi:duration>3600</espi:duration>
          </espi:timePeriod>
          <espi:value>600</espi:value>
          <espi:cost>12</espi:cost>
        </espi:IntervalReading>
      </espi:IntervalBlock>
    </content>
  </entry>
</feed>
"""

# Sample gas meter data
SAMPLE_GAS_ESPI_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:espi="http://naesb.org/espi">
  <entry>
    <content>
      <espi:ServiceCategory>
        <espi:kind>1</espi:kind>
      </espi:ServiceCategory>
    </content>
  </entry>
  <entry>
    <content>
      <espi:IntervalBlock>
        <espi:IntervalReading>
          <espi:timePeriod>
            <espi:start>1704067200</espi:start>
            <espi:duration>86400</espi:duration>
          </espi:timePeriod>
          <espi:value>5000</espi:value>
        </espi:IntervalReading>
      </espi:IntervalBlock>
    </content>
  </entry>
</feed>
"""


class TestParseEspiAtomXml:
    """Tests for parse_espi_atom_xml function."""
    
    def test_parses_electric_meter_intervals(self):
        """Should parse interval readings from ESPI XML."""
        result = parse_espi_atom_xml(SAMPLE_ESPI_XML)
        
        assert 'error' not in result
        assert result['fuel_type'] == 'electric'
        assert result['unit'] == 'kWh'
        assert len(result['intervals']) == 3
        
    def test_extracts_meter_id(self):
        """Should extract meter reading ID."""
        result = parse_espi_atom_xml(SAMPLE_ESPI_XML)
        
        assert result['meter_id'] == 'meter-12345'
        
    def test_converts_wh_to_kwh(self):
        """Should convert Wh values to kWh."""
        result = parse_espi_atom_xml(SAMPLE_ESPI_XML)
        
        # First interval: 500 Wh = 0.5 kWh
        assert result['intervals'][0]['value'] == 0.5
        
    def test_extracts_cost_in_cents(self):
        """Should extract cost in cents."""
        result = parse_espi_atom_xml(SAMPLE_ESPI_XML)
        
        assert result['intervals'][0]['cost_cents'] == 10
        
    def test_extracts_reading_quality(self):
        """Should extract reading quality (measured/estimated)."""
        result = parse_espi_atom_xml(SAMPLE_ESPI_XML)
        
        assert result['intervals'][0]['quality'] == 'measured'
        
    def test_aggregates_to_billing_periods(self):
        """Should aggregate intervals into billing periods."""
        result = parse_espi_atom_xml(SAMPLE_ESPI_XML)
        
        # Should have 2 billing periods: January and February 2024
        assert len(result['billing_periods']) == 2
        
        jan_period = next(bp for bp in result['billing_periods'] if bp['month'] == '2024-01')
        assert jan_period['usage'] == 1.25  # 0.5 + 0.75 kWh
        
    def test_detects_gas_service(self):
        """Should detect gas service from ServiceCategory."""
        result = parse_espi_atom_xml(SAMPLE_GAS_ESPI_XML)
        
        assert result['fuel_type'] == 'gas'
        
    def test_handles_invalid_xml(self):
        """Should handle invalid XML gracefully."""
        result = parse_espi_atom_xml("<invalid>xml")
        
        assert 'error' in result
        assert result['intervals'] == []
        assert result['billing_periods'] == []
        
    def test_handles_empty_xml(self):
        """Should handle empty feed."""
        empty_feed = """<?xml version="1.0"?>
        <feed xmlns="http://www.w3.org/2005/Atom" xmlns:espi="http://naesb.org/espi">
        </feed>"""
        
        result = parse_espi_atom_xml(empty_feed)
        
        assert 'error' not in result
        assert result['intervals'] == []


class TestNormalizeUsageToSummary:
    """Tests for normalize_usage_to_summary function."""
    
    def test_calculates_annual_usage(self):
        """Should calculate total annual usage."""
        raw_data = {
            'fuel_type': 'electric',
            'unit': 'kWh',
            'intervals': [],
            'billing_periods': [
                {'month': '2024-01', 'start_date': '2024-01-01', 'end_date': '2024-01-31', 
                 'usage': 500, 'cost_usd': 75, 'unit': 'kWh', 'num_intervals': 744, 'quality_flags': []},
                {'month': '2024-02', 'start_date': '2024-02-01', 'end_date': '2024-02-29', 
                 'usage': 450, 'cost_usd': 67.5, 'unit': 'kWh', 'num_intervals': 696, 'quality_flags': []},
            ],
        }
        
        result = normalize_usage_to_summary(raw_data)
        
        # With 2 months of data, should annualize: (950 / 2) * 12 = 5700
        assert result['annual_usage'] == 5700.0
        
    def test_calculates_seasonal_pattern(self):
        """Should calculate seasonal averages."""
        raw_data = {
            'fuel_type': 'electric',
            'unit': 'kWh',
            'intervals': [],
            'billing_periods': [
                {'month': '2024-01', 'start_date': '2024-01-01', 'end_date': '2024-01-31', 
                 'usage': 800, 'unit': 'kWh', 'num_intervals': 744, 'quality_flags': []},  # Winter
                {'month': '2024-02', 'start_date': '2024-02-01', 'end_date': '2024-02-29', 
                 'usage': 750, 'unit': 'kWh', 'num_intervals': 696, 'quality_flags': []},  # Winter
                {'month': '2024-07', 'start_date': '2024-07-01', 'end_date': '2024-07-31', 
                 'usage': 1200, 'unit': 'kWh', 'num_intervals': 744, 'quality_flags': []},  # Summer
            ],
        }
        
        result = normalize_usage_to_summary(raw_data)
        
        assert result['seasonal_pattern'] is not None
        assert result['seasonal_pattern']['winter'] == 775.0  # (800 + 750) / 2
        assert result['seasonal_pattern']['summer'] == 1200.0
        
    def test_creates_monthly_breakdown(self):
        """Should create monthly breakdown array."""
        raw_data = {
            'fuel_type': 'electric',
            'unit': 'kWh',
            'billing_periods': [
                {'month': '2024-01', 'start_date': '2024-01-01', 'end_date': '2024-01-31', 
                 'usage': 500, 'cost_usd': 75, 'unit': 'kWh', 'num_intervals': 744, 'quality_flags': []},
            ],
        }
        
        result = normalize_usage_to_summary(raw_data)
        
        assert len(result['monthly_breakdown']) == 1
        assert result['monthly_breakdown'][0]['month'] == '2024-01'
        assert result['monthly_breakdown'][0]['usage'] == 500
        
    def test_handles_missing_cost(self):
        """Should handle missing cost data."""
        raw_data = {
            'fuel_type': 'electric',
            'unit': 'kWh',
            'billing_periods': [
                {'month': '2024-01', 'start_date': '2024-01-01', 'end_date': '2024-01-31', 
                 'usage': 500, 'unit': 'kWh', 'num_intervals': 744, 'quality_flags': []},
            ],
        }
        
        result = normalize_usage_to_summary(raw_data)
        
        assert result['annual_cost_usd'] is None
        
    def test_handles_empty_data(self):
        """Should handle empty data gracefully."""
        result = normalize_usage_to_summary({})
        
        assert 'no_data' in result['data_quality_flags'] or 'no_billing_periods' in result['data_quality_flags']


class TestDetectDataQualityIssues:
    """Tests for detect_data_quality_issues function."""
    
    def test_detects_partial_data(self):
        """Should flag partial data (< 12 months)."""
        summary = {
            'months_covered': 6,
            'billing_periods': [{'month': f'2024-0{i}', 'usage': 500} for i in range(1, 7)],
        }
        
        flags = detect_data_quality_issues(summary)
        
        assert 'partial_data_6_months' in flags
        
    def test_detects_data_gaps(self):
        """Should flag gaps in data coverage."""
        summary = {
            'billing_periods': [
                {'month': '2024-01', 'usage': 500},
                {'month': '2024-03', 'usage': 500},  # February missing
            ],
        }
        
        flags = detect_data_quality_issues(summary)
        
        assert 'data_gaps_detected' in flags
        
    def test_detects_high_variance(self):
        """Should flag high variance in usage."""
        summary = {
            'billing_periods': [
                {'month': '2024-01', 'usage': 100},
                {'month': '2024-02', 'usage': 500},
                {'month': '2024-03', 'usage': 2000},  # High variance
            ],
        }
        
        flags = detect_data_quality_issues(summary)
        
        assert 'high_variance' in flags
        
    def test_detects_anomalous_spikes(self):
        """Should flag values > 2 std dev from mean."""
        summary = {
            'billing_periods': [
                {'month': '2024-01', 'usage': 500},
                {'month': '2024-02', 'usage': 510},
                {'month': '2024-03', 'usage': 490},
                {'month': '2024-04', 'usage': 505},
                {'month': '2024-05', 'usage': 495},
                {'month': '2024-06', 'usage': 500},
                {'month': '2024-07', 'usage': 5000},  # Anomalous spike (10x normal)
            ],
        }
        
        flags = detect_data_quality_issues(summary)
        
        assert 'anomalous_spikes' in flags
        
    def test_no_flags_for_good_data(self):
        """Should return no flags for consistent 12-month data."""
        summary = {
            'months_covered': 12,
            'billing_periods': [
                {'month': f'2024-{str(i).zfill(2)}', 'usage': 500 + (i * 10)} 
                for i in range(1, 13)
            ],
        }
        
        flags = detect_data_quality_issues(summary)
        
        assert 'partial_data' not in str(flags)
        assert 'data_gaps_detected' not in flags
        assert 'high_variance' not in flags


class TestParseAggregatorResponse:
    """Tests for parse_aggregator_response function (UtilityAPI format)."""
    
    def test_parses_utilityapi_bills(self):
        """Should parse UtilityAPI bill format."""
        response = {
            'bills': [
                {
                    'bill_start_date': '2024-01-01',
                    'bill_end_date': '2024-01-31',
                    'bill_total_kwh': 500,
                    'bill_total_cost': 75.00,
                },
                {
                    'bill_start_date': '2024-02-01',
                    'bill_end_date': '2024-02-29',
                    'bill_total_kwh': 450,
                    'bill_total_cost': 67.50,
                },
            ],
        }
        
        result = parse_aggregator_response(response)
        
        assert len(result['billing_periods']) == 2
        assert result['billing_periods'][0]['usage'] == 500
        assert result['billing_periods'][0]['cost_usd'] == 75.00
        
    def test_handles_empty_bills(self):
        """Should handle empty bills array."""
        response = {'bills': []}
        
        result = parse_aggregator_response(response)
        
        assert result['billing_periods'] == []
        
    def test_handles_missing_dates(self):
        """Should skip bills with missing dates."""
        response = {
            'bills': [
                {'bill_total_kwh': 500},  # Missing dates
            ],
        }
        
        result = parse_aggregator_response(response)
        
        assert result['billing_periods'] == []


# Integration test combining parse + normalize
class TestEndToEndParsing:
    """End-to-end tests combining parsing and normalization."""
    
    def test_full_pipeline(self):
        """Should parse ESPI and produce normalized summary."""
        raw_data = parse_espi_atom_xml(SAMPLE_ESPI_XML)
        summary = normalize_usage_to_summary(raw_data)
        
        assert 'error' not in summary
        assert summary['fuel_type'] == 'electric'
        assert summary['unit'] == 'kWh'
        assert summary['annual_usage'] > 0
        assert len(summary['monthly_breakdown']) > 0
        assert isinstance(summary['data_quality_flags'], list)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
