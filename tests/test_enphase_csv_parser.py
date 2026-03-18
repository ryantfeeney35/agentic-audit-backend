"""
Tests for Enphase CSV parser.

Uses the sample Enlighten custom report at backend/tests/data/5887542_custom_report.csv
"""

import pytest
from datetime import datetime
from pathlib import Path

# Module under test
from utils.enphase_csv_parser import (
    validate_headers,
    parse_datetime,
    wh_to_kwh,
    detect_granularity,
    calculate_interval_end,
    parse_row,
    parse_csv,
    calculate_summary,
    EnphaseCSVParseError,
    EXPECTED_HEADERS,
    COLUMN_MAPPING,
)


class TestValidateHeaders:
    """Tests for header validation."""
    
    def test_exact_headers_valid(self):
        """Exact match of expected headers passes."""
        assert validate_headers(EXPECTED_HEADERS) is True
    
    def test_headers_with_extra_whitespace(self):
        """Headers with leading/trailing whitespace are normalized."""
        headers = [h + " " for h in EXPECTED_HEADERS]
        assert validate_headers(headers) is True
    
    def test_missing_datetime_column(self):
        """Missing Date/Time column raises error."""
        headers = list(EXPECTED_HEADERS)
        headers.remove("Date/Time")
        with pytest.raises(EnphaseCSVParseError, match="Missing required 'Date/Time' column"):
            validate_headers(headers)
    
    def test_no_energy_columns(self):
        """No recognized energy columns raises error."""
        headers = ["Date/Time", "Unknown Column 1", "Unknown Column 2"]
        with pytest.raises(EnphaseCSVParseError, match="No recognized energy columns found"):
            validate_headers(headers)
    
    def test_partial_columns_valid(self):
        """Subset of columns (Date/Time + at least one energy column) is valid."""
        headers = ["Date/Time", "Energy Produced (Wh)"]
        assert validate_headers(headers) is True


class TestParseDatetime:
    """Tests for date/time parsing."""
    
    def test_primary_format(self):
        """MM/DD/YYYY HH:MM format parses correctly."""
        result = parse_datetime("06/25/2025 00:00")
        assert result == datetime(2025, 6, 25, 0, 0)
    
    def test_with_whitespace(self):
        """Leading/trailing whitespace is trimmed."""
        result = parse_datetime("  06/25/2025 14:30  ")
        assert result == datetime(2025, 6, 25, 14, 30)
    
    def test_short_year_format(self):
        """MM/DD/YY HH:MM format parses correctly."""
        result = parse_datetime("12/31/24 23:59")
        assert result == datetime(2024, 12, 31, 23, 59)
    
    def test_iso_format_fallback(self):
        """ISO format as fallback."""
        result = parse_datetime("2025-06-25 14:30")
        assert result == datetime(2025, 6, 25, 14, 30)
    
    def test_invalid_format_raises(self):
        """Invalid format raises EnphaseCSVParseError."""
        with pytest.raises(EnphaseCSVParseError, match="Unable to parse date"):
            parse_datetime("25-06-2025 00:00")


class TestWhToKwh:
    """Tests for Wh to kWh conversion."""
    
    def test_simple_conversion(self):
        """1500 Wh = 1.5 kWh."""
        assert wh_to_kwh("1500") == 1.5
    
    def test_with_commas(self):
        """Comma-separated numbers work."""
        assert wh_to_kwh("1,500") == 1.5
    
    def test_with_decimals(self):
        """Decimal values work."""
        assert wh_to_kwh("1500.5") == 1.5005
    
    def test_empty_string_returns_none(self):
        """Empty string returns None."""
        assert wh_to_kwh("") is None
    
    def test_whitespace_only_returns_none(self):
        """Whitespace-only returns None."""
        assert wh_to_kwh("   ") is None
    
    def test_invalid_returns_none(self):
        """Non-numeric returns None."""
        assert wh_to_kwh("abc") is None
    
    def test_zero_value(self):
        """Zero converts correctly."""
        assert wh_to_kwh("0") == 0.0


class TestDetectGranularity:
    """Tests for granularity detection."""
    
    def test_single_interval_defaults_to_15m(self):
        """Single interval defaults to 15m."""
        intervals = [datetime(2025, 6, 25, 0, 0)]
        assert detect_granularity(intervals) == "15m"
    
    def test_5_minute_intervals(self):
        """5-minute spacing detected."""
        intervals = [
            datetime(2025, 6, 25, 0, 0),
            datetime(2025, 6, 25, 0, 5),
            datetime(2025, 6, 25, 0, 10),
        ]
        assert detect_granularity(intervals) == "5m"
    
    def test_15_minute_intervals(self):
        """15-minute spacing detected."""
        intervals = [
            datetime(2025, 6, 25, 0, 0),
            datetime(2025, 6, 25, 0, 15),
            datetime(2025, 6, 25, 0, 30),
        ]
        assert detect_granularity(intervals) == "15m"
    
    def test_hourly_intervals(self):
        """Hourly spacing detected."""
        intervals = [
            datetime(2025, 6, 25, 0, 0),
            datetime(2025, 6, 25, 1, 0),
            datetime(2025, 6, 25, 2, 0),
        ]
        assert detect_granularity(intervals) == "hourly"
    
    def test_daily_intervals(self):
        """Daily spacing detected."""
        intervals = [
            datetime(2025, 6, 25, 0, 0),
            datetime(2025, 6, 26, 0, 0),
            datetime(2025, 6, 27, 0, 0),
        ]
        assert detect_granularity(intervals) == "daily"


class TestCalculateIntervalEnd:
    """Tests for interval end calculation."""
    
    def test_5m_granularity(self):
        """5-minute interval end."""
        start = datetime(2025, 6, 25, 0, 0)
        end = calculate_interval_end(start, "5m")
        assert end == datetime(2025, 6, 25, 0, 5)
    
    def test_15m_granularity(self):
        """15-minute interval end."""
        start = datetime(2025, 6, 25, 0, 0)
        end = calculate_interval_end(start, "15m")
        assert end == datetime(2025, 6, 25, 0, 15)
    
    def test_hourly_granularity(self):
        """Hourly interval end."""
        start = datetime(2025, 6, 25, 0, 0)
        end = calculate_interval_end(start, "hourly")
        assert end == datetime(2025, 6, 25, 1, 0)
    
    def test_daily_granularity(self):
        """Daily interval end."""
        start = datetime(2025, 6, 25, 0, 0)
        end = calculate_interval_end(start, "daily")
        assert end == datetime(2025, 6, 26, 0, 0)


class TestParseRow:
    """Tests for single row parsing."""
    
    def test_complete_row(self):
        """Row with all columns parses correctly."""
        row = {
            "Date/Time": "06/25/2025 00:00",
            "Energy Produced (Wh)": "1000",
            "Energy Consumed (Wh)": "500",
            "Exported to Grid (Wh)": "600",
            "Imported from Grid (Wh)": "100",
            "Stored in batteries (Wh)": "200",
            "Discharged from batteries (Wh)": "50",
        }
        result = parse_row(row, list(row.keys()))
        
        assert result["interval_start"] == datetime(2025, 6, 25, 0, 0)
        assert result["production_kwh"] == 1.0
        assert result["consumption_kwh"] == 0.5
        assert result["grid_export_kwh"] == 0.6
        assert result["grid_import_kwh"] == 0.1
        assert result["battery_charge_kwh"] == 0.2
        assert result["battery_discharge_kwh"] == 0.05
    
    def test_row_with_zeros(self):
        """Row with zero values parses correctly."""
        row = {
            "Date/Time": "06/25/2025 00:00",
            "Energy Produced (Wh)": "0",
            "Energy Consumed (Wh)": "0",
        }
        result = parse_row(row, list(row.keys()))
        
        assert result["production_kwh"] == 0.0
        assert result["consumption_kwh"] == 0.0
    
    def test_row_missing_datetime_raises(self):
        """Missing Date/Time raises error."""
        row = {"Energy Produced (Wh)": "1000"}
        with pytest.raises(EnphaseCSVParseError, match="missing Date/Time"):
            parse_row(row, list(row.keys()))


class TestParseCSVWithSampleFile:
    """Integration tests using the actual sample CSV file."""
    
    @pytest.fixture
    def sample_csv_path(self):
        """Path to sample CSV file."""
        return Path(__file__).parent / "data" / "5887542_custom_report.csv"
    
    @pytest.fixture
    def sample_csv_content(self, sample_csv_path):
        """Content of sample CSV file."""
        if not sample_csv_path.exists():
            pytest.skip(f"Sample file not found: {sample_csv_path}")
        return sample_csv_path.read_bytes()
    
    def test_parse_sample_file(self, sample_csv_content):
        """Parse complete sample file successfully."""
        records, granularity, summary = parse_csv(sample_csv_content)
        
        # Should have records
        assert len(records) > 0
        
        # Should detect 15-minute granularity
        assert granularity == "15m"
        
        # Each record should have required fields
        first_record = records[0]
        assert "interval_start" in first_record
        assert "interval_end" in first_record
        assert "granularity" in first_record
    
    def test_sample_file_summary(self, sample_csv_content):
        """Summary statistics calculated correctly."""
        records, granularity, summary = parse_csv(sample_csv_content)
        
        assert summary["record_count"] == len(records)
        assert summary["date_range_start"] is not None
        assert summary["date_range_end"] is not None
        # Totals should be non-negative
        assert summary["total_production_kwh"] >= 0
        assert summary["total_consumption_kwh"] >= 0
    
    def test_sample_file_records_sorted(self, sample_csv_content):
        """Records are sorted by interval_start."""
        records, _, _ = parse_csv(sample_csv_content)
        
        for i in range(1, len(records)):
            assert records[i]["interval_start"] >= records[i-1]["interval_start"]


class TestCalculateSummary:
    """Tests for summary calculation."""
    
    def test_empty_records(self):
        """Empty records returns zeros."""
        summary = calculate_summary([])
        
        assert summary["record_count"] == 0
        assert summary["total_production_kwh"] == 0.0
    
    def test_summary_with_records(self):
        """Summary calculated correctly."""
        records = [
            {
                "interval_start": datetime(2025, 6, 25, 0, 0),
                "interval_end": datetime(2025, 6, 25, 0, 15),
                "production_kwh": 1.0,
                "consumption_kwh": 0.5,
                "grid_export_kwh": None,
                "grid_import_kwh": None,
                "battery_charge_kwh": None,
                "battery_discharge_kwh": None,
            },
            {
                "interval_start": datetime(2025, 6, 25, 0, 15),
                "interval_end": datetime(2025, 6, 25, 0, 30),
                "production_kwh": 2.0,
                "consumption_kwh": 1.0,
                "grid_export_kwh": 0.5,
                "grid_import_kwh": None,
                "battery_charge_kwh": None,
                "battery_discharge_kwh": None,
            },
        ]
        
        summary = calculate_summary(records)
        
        assert summary["record_count"] == 2
        assert summary["total_production_kwh"] == 3.0
        assert summary["total_consumption_kwh"] == 1.5
        assert summary["total_grid_export_kwh"] == 0.5
        assert summary["date_range_start"] == datetime(2025, 6, 25, 0, 0)
        assert summary["date_range_end"] == datetime(2025, 6, 25, 0, 30)


class TestParseCSVEdgeCases:
    """Edge case tests for CSV parsing."""
    
    def test_minimal_valid_csv(self):
        """Minimal valid CSV with one record."""
        csv_content = b"Date/Time,Energy Produced (Wh)\n06/25/2025 00:00,1000"
        
        records, granularity, summary = parse_csv(csv_content)
        
        assert len(records) == 1
        assert records[0]["production_kwh"] == 1.0
    
    def test_csv_with_bom(self):
        """CSV with UTF-8 BOM parses correctly."""
        csv_content = b"\xef\xbb\xbfDate/Time,Energy Produced (Wh)\n06/25/2025 00:00,1000"
        
        records, _, _ = parse_csv(csv_content)
        assert len(records) == 1
    
    def test_empty_csv_raises(self):
        """Empty CSV raises error."""
        csv_content = b""
        
        with pytest.raises(EnphaseCSVParseError):
            parse_csv(csv_content)
    
    def test_headers_only_raises(self):
        """CSV with only headers (no data) raises error."""
        csv_content = b"Date/Time,Energy Produced (Wh)\n"
        
        with pytest.raises(EnphaseCSVParseError, match="No valid telemetry records"):
            parse_csv(csv_content)
