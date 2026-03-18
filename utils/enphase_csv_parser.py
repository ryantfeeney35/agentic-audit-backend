"""
Enphase CSV Parser for Enlighten Custom Report Format.

Parses CSV exports from Enphase Enlighten containing telemetry data including:
- Energy produced (solar production)
- Energy consumed (home consumption)
- Grid import/export
- Battery charge/discharge

Converts all Wh values to kWh and detects data granularity from interval spacing.
"""

import csv
import io
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Iterator, BinaryIO

# Expected column headers in Enlighten custom report
EXPECTED_HEADERS = [
    "Date/Time",
    "Energy Produced (Wh)",
    "Energy Consumed (Wh)", 
    "Exported to Grid (Wh)",
    "Imported from Grid (Wh)",
    "Stored in batteries (Wh)",
    "Discharged from batteries (Wh)",
]

# Column name mapping from CSV headers to database field names
COLUMN_MAPPING = {
    "Energy Produced (Wh)": "production_kwh",
    "Energy Consumed (Wh)": "consumption_kwh",
    "Exported to Grid (Wh)": "grid_export_kwh",
    "Imported from Grid (Wh)": "grid_import_kwh",
    "Stored in batteries (Wh)": "battery_charge_kwh",
    "Discharged from batteries (Wh)": "battery_discharge_kwh",
}


class EnphaseCSVParseError(Exception):
    """Raised when CSV parsing fails."""
    pass


def validate_headers(headers: List[str]) -> bool:
    """
    Validate that CSV headers match expected Enlighten custom report format.
    
    Args:
        headers: List of header strings from CSV
        
    Returns:
        True if headers match expected format
        
    Raises:
        EnphaseCSVParseError: If headers don't match expected format
    """
    # Normalize headers (strip whitespace)
    normalized = [h.strip() for h in headers]
    
    # Check for exact match
    if normalized == EXPECTED_HEADERS:
        return True
    
    # Check for subset match (allow fewer columns if some are missing)
    if "Date/Time" not in normalized:
        raise EnphaseCSVParseError("Missing required 'Date/Time' column")
    
    # At least one energy column required
    energy_cols = [h for h in normalized if h in COLUMN_MAPPING]
    if not energy_cols:
        raise EnphaseCSVParseError(
            f"No recognized energy columns found. Expected at least one of: {list(COLUMN_MAPPING.keys())}"
        )
    
    return True


def parse_datetime(date_str: str) -> datetime:
    """
    Parse date/time string from Enlighten format (MM/DD/YYYY HH:MM).
    
    Args:
        date_str: Date string like "06/25/2025 00:00"
        
    Returns:
        Parsed datetime object
        
    Raises:
        EnphaseCSVParseError: If date string doesn't match expected format
    """
    formats = [
        "%m/%d/%Y %H:%M",  # Primary format: 06/25/2025 00:00
        "%m/%d/%y %H:%M",  # Short year: 06/25/25 00:00
        "%Y-%m-%d %H:%M:%S",  # ISO format fallback
        "%Y-%m-%d %H:%M",  # ISO format without seconds
    ]
    
    for fmt in formats:
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except ValueError:
            continue
    
    raise EnphaseCSVParseError(
        f"Unable to parse date '{date_str}'. Expected format: MM/DD/YYYY HH:MM"
    )


def wh_to_kwh(wh_value: str) -> Optional[float]:
    """
    Convert Wh string value to kWh float.
    
    Args:
        wh_value: String value in Wh (e.g., "1500" or "1,500.5")
        
    Returns:
        Value in kWh, or None if empty/invalid
    """
    if not wh_value or wh_value.strip() == "":
        return None
    
    try:
        # Remove commas and whitespace
        cleaned = wh_value.strip().replace(",", "")
        wh = float(cleaned)
        return wh / 1000.0  # Convert Wh to kWh
    except (ValueError, TypeError):
        return None


def detect_granularity(intervals: List[datetime]) -> str:
    """
    Detect data granularity from interval spacing.
    
    Args:
        intervals: List of datetime values from parsed data
        
    Returns:
        Granularity string: '5m', '15m', 'hourly', or 'daily'
    """
    if len(intervals) < 2:
        return "15m"  # Default to 15-minute if insufficient data
    
    # Calculate most common interval
    deltas = []
    for i in range(1, min(len(intervals), 10)):  # Sample first 10 intervals
        delta = intervals[i] - intervals[i-1]
        deltas.append(delta.total_seconds())
    
    if not deltas:
        return "15m"
    
    avg_delta = sum(deltas) / len(deltas)
    
    # Classify based on average interval
    if avg_delta <= 6 * 60:  # Up to 6 minutes
        return "5m"
    elif avg_delta <= 20 * 60:  # Up to 20 minutes
        return "15m"
    elif avg_delta <= 90 * 60:  # Up to 90 minutes
        return "hourly"
    else:
        return "daily"


def calculate_interval_end(interval_start: datetime, granularity: str) -> datetime:
    """
    Calculate interval end time based on granularity.
    
    Args:
        interval_start: Start datetime of interval
        granularity: One of '5m', '15m', 'hourly', 'daily'
        
    Returns:
        End datetime for the interval
    """
    granularity_deltas = {
        "5m": timedelta(minutes=5),
        "15m": timedelta(minutes=15),
        "hourly": timedelta(hours=1),
        "daily": timedelta(days=1),
    }
    delta = granularity_deltas.get(granularity, timedelta(minutes=15))
    return interval_start + delta


def parse_row(row: Dict[str, str], headers: List[str]) -> Dict:
    """
    Parse a single CSV row into telemetry data.
    
    Args:
        row: Dictionary mapping column headers to values
        headers: List of column headers (for ordering)
        
    Returns:
        Dictionary with parsed values
        
    Raises:
        EnphaseCSVParseError: If required date/time is missing or invalid
    """
    date_str = row.get("Date/Time", "").strip()
    if not date_str:
        raise EnphaseCSVParseError("Row missing Date/Time value")
    
    parsed = {
        "interval_start": parse_datetime(date_str),
    }
    
    # Convert energy values from Wh to kWh
    for csv_col, db_col in COLUMN_MAPPING.items():
        if csv_col in row:
            parsed[db_col] = wh_to_kwh(row[csv_col])
    
    return parsed


def parse_csv_stream(
    file_stream: BinaryIO,
    chunk_size: int = 1000
) -> Iterator[List[Dict]]:
    """
    Parse CSV file in streaming chunks to handle large files.
    
    Args:
        file_stream: Binary file stream of CSV content
        chunk_size: Number of rows per chunk (default 1000)
        
    Yields:
        Lists of parsed row dictionaries
        
    Raises:
        EnphaseCSVParseError: If CSV format is invalid
    """
    # Read and decode entire stream (for initial implementation)
    # TODO: True streaming for very large files (>100MB)
    content = file_stream.read()
    
    # Handle BOM and detect encoding
    if isinstance(content, bytes):
        # Try UTF-8 first, fall back to latin-1
        try:
            text = content.decode('utf-8-sig')  # Handles BOM
        except UnicodeDecodeError:
            text = content.decode('latin-1')
    else:
        text = content
    
    text_stream = io.StringIO(text)
    reader = csv.DictReader(text_stream)
    
    if not reader.fieldnames:
        raise EnphaseCSVParseError("CSV file is empty or has no headers")
    
    # Validate headers
    validate_headers(list(reader.fieldnames))
    
    chunk = []
    for row in reader:
        try:
            parsed = parse_row(row, list(reader.fieldnames))
            chunk.append(parsed)
            
            if len(chunk) >= chunk_size:
                yield chunk
                chunk = []
        except EnphaseCSVParseError:
            # Skip malformed rows
            continue
    
    # Yield remaining rows
    if chunk:
        yield chunk


def parse_csv(file_content: bytes) -> Tuple[List[Dict], str, Dict]:
    """
    Parse entire CSV file and return all telemetry records.
    
    Args:
        file_content: Raw CSV file content as bytes
        
    Returns:
        Tuple of:
        - List of parsed telemetry records
        - Detected granularity string
        - Summary statistics dict
        
    Raises:
        EnphaseCSVParseError: If CSV format is invalid
    """
    stream = io.BytesIO(file_content)
    all_records = []
    
    for chunk in parse_csv_stream(stream):
        all_records.extend(chunk)
    
    if not all_records:
        raise EnphaseCSVParseError("No valid telemetry records found in CSV")
    
    # Sort by interval_start
    all_records.sort(key=lambda x: x["interval_start"])
    
    # Detect granularity
    intervals = [r["interval_start"] for r in all_records]
    granularity = detect_granularity(intervals)
    
    # Add interval_end based on detected granularity
    for record in all_records:
        record["interval_end"] = calculate_interval_end(
            record["interval_start"], 
            granularity
        )
        record["granularity"] = granularity
    
    # Calculate summary statistics
    summary = calculate_summary(all_records)
    
    return all_records, granularity, summary


def calculate_summary(records: List[Dict]) -> Dict:
    """
    Calculate summary statistics for parsed telemetry data.
    
    Args:
        records: List of parsed telemetry records
        
    Returns:
        Summary dictionary with totals and date range
    """
    if not records:
        return {
            "record_count": 0,
            "date_range_start": None,
            "date_range_end": None,
            "total_production_kwh": 0.0,
            "total_consumption_kwh": 0.0,
            "total_grid_export_kwh": 0.0,
            "total_grid_import_kwh": 0.0,
            "total_battery_charge_kwh": 0.0,
            "total_battery_discharge_kwh": 0.0,
        }
    
    def safe_sum(key: str) -> float:
        return sum(r.get(key) or 0.0 for r in records)
    
    return {
        "record_count": len(records),
        "date_range_start": records[0]["interval_start"],
        "date_range_end": records[-1]["interval_end"],
        "total_production_kwh": round(safe_sum("production_kwh"), 3),
        "total_consumption_kwh": round(safe_sum("consumption_kwh"), 3),
        "total_grid_export_kwh": round(safe_sum("grid_export_kwh"), 3),
        "total_grid_import_kwh": round(safe_sum("grid_import_kwh"), 3),
        "total_battery_charge_kwh": round(safe_sum("battery_charge_kwh"), 3),
        "total_battery_discharge_kwh": round(safe_sum("battery_discharge_kwh"), 3),
    }
