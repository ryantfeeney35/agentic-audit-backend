# backend/utils/green_button.py
"""
Green Button ESPI/Atom XML Parser and Normalizer

Parses Energy Services Provider Interface (ESPI) Atom feeds from
utilities like SDG&E that implement Green Button Connect My Data (CMD).

The ESPI format uses Atom syndication feeds with embedded usage data in
a standardized XML schema. This module:
- Parses ESPI Atom/XML feeds to extract usage intervals
- Normalizes data into billing periods with monthly aggregation
- Detects data quality issues (gaps, anomalies, partial coverage)

Reference: https://www.naesb.org//ESPI.asp
"""

import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, field
import logging
import statistics

logger = logging.getLogger(__name__)

# ESPI/Atom XML namespaces
NAMESPACES = {
    'atom': 'http://www.w3.org/2005/Atom',
    'espi': 'http://naesb.org/espi',
}


@dataclass
class UsageInterval:
    """A single usage reading interval."""
    start: datetime
    duration_seconds: int
    value: float  # kWh or therms
    cost_cents: Optional[int] = None
    quality: Optional[str] = None  # 'measured', 'estimated', 'projected'


@dataclass
class BillingPeriod:
    """A billing period with aggregated usage."""
    start_date: datetime
    end_date: datetime
    usage_amount: float  # kWh or therms
    cost_usd: Optional[float] = None
    num_intervals: int = 0
    quality_flags: List[str] = field(default_factory=list)


@dataclass
class ParsedUsageData:
    """Complete parsed usage data from ESPI feed."""
    fuel_type: str  # 'electric' or 'gas'
    unit: str  # 'kWh' or 'therms'
    intervals: List[UsageInterval]
    billing_periods: List[BillingPeriod]
    meter_id: Optional[str] = None
    service_kind: Optional[str] = None
    raw_metadata: Dict[str, Any] = field(default_factory=dict)


def parse_espi_atom_xml(xml_content: str) -> Dict[str, Any]:
    """
    Parse ESPI/Atom XML content into structured usage data.
    
    Extracts IntervalReading entries from the ESPI feed and groups
    them by billing period when available.
    
    Args:
        xml_content: Raw XML string from ESPI endpoint
        
    Returns:
        Dictionary with parsed usage data:
        {
            'fuel_type': 'electric' | 'gas',
            'unit': 'kWh' | 'therms',
            'intervals': [{'start': ..., 'duration': ..., 'value': ..., ...}, ...],
            'billing_periods': [{'start_date': ..., 'end_date': ..., 'usage': ..., ...}, ...],
            'meter_id': ...,
            'service_kind': ...,
            'metadata': {...}
        }
    """
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError as e:
        logger.error(f"Failed to parse ESPI XML: {e}")
        return {'error': f"XML parse error: {e}", 'intervals': [], 'billing_periods': []}
    
    result = {
        'fuel_type': 'electric',  # Default
        'unit': 'kWh',
        'intervals': [],
        'billing_periods': [],
        'meter_id': None,
        'service_kind': None,
        'metadata': {},
    }
    
    # Try to find ServiceCategory to determine fuel type
    service_kind = root.find('.//espi:ServiceCategory/espi:kind', NAMESPACES)
    if service_kind is not None and service_kind.text:
        kind_value = service_kind.text
        result['service_kind'] = kind_value
        # ESPI ServiceCategory kind values:
        # 0 = electricity, 1 = gas, 2 = water, etc.
        if kind_value == '1':
            result['fuel_type'] = 'gas'
            result['unit'] = 'therms'
    
    # Look for ReadingType to confirm unit/fuel
    uom = root.find('.//espi:ReadingType/espi:uom', NAMESPACES)
    if uom is not None and uom.text:
        uom_value = uom.text
        result['metadata']['uom'] = uom_value
        # ESPI UOM values: 72 = Wh, 169 = therms, etc.
        if uom_value == '169':
            result['fuel_type'] = 'gas'
            result['unit'] = 'therms'
    
    # Extract meter reading ID
    meter_reading = root.find('.//espi:MeterReading', NAMESPACES)
    if meter_reading is not None:
        # Look for mRID (meter reading ID)
        mrid = meter_reading.find('.//espi:mRID', NAMESPACES)
        if mrid is not None and mrid.text:
            result['meter_id'] = mrid.text
    
    # Parse interval blocks and readings
    intervals = []
    for interval_block in root.findall('.//espi:IntervalBlock', NAMESPACES):
        for interval_reading in interval_block.findall('espi:IntervalReading', NAMESPACES):
            interval = _parse_interval_reading(interval_reading)
            if interval:
                intervals.append(interval)
    
    # Sort intervals by start time
    intervals.sort(key=lambda x: x['start'])
    result['intervals'] = intervals
    
    # Aggregate into billing periods (monthly)
    result['billing_periods'] = _aggregate_to_billing_periods(intervals, result['unit'])
    
    return result


def _parse_interval_reading(reading_elem: ET.Element) -> Optional[Dict[str, Any]]:
    """
    Parse a single IntervalReading element.
    
    Args:
        reading_elem: XML Element for espi:IntervalReading
        
    Returns:
        Dictionary with interval data or None if invalid
    """
    try:
        # Get time period
        time_period = reading_elem.find('espi:timePeriod', NAMESPACES)
        if time_period is None:
            return None
        
        start_elem = time_period.find('espi:start', NAMESPACES)
        duration_elem = time_period.find('espi:duration', NAMESPACES)
        
        if start_elem is None or start_elem.text is None:
            return None
        
        # Start is Unix timestamp in seconds
        start_ts = int(start_elem.text)
        start_dt = datetime.utcfromtimestamp(start_ts)
        
        duration = int(duration_elem.text) if duration_elem is not None and duration_elem.text else 3600
        
        # Get value
        value_elem = reading_elem.find('espi:value', NAMESPACES)
        if value_elem is None or value_elem.text is None:
            return None
        
        # Value is in Wh for electricity, need to convert to kWh
        raw_value = float(value_elem.text)
        value_kwh = raw_value / 1000.0  # Wh to kWh (or raw for therms)
        
        # Check for cost
        cost_elem = reading_elem.find('espi:cost', NAMESPACES)
        cost_cents = int(cost_elem.text) if cost_elem is not None and cost_elem.text else None
        
        # Check for reading quality
        quality = None
        quality_elem = reading_elem.find('espi:ReadingQuality/espi:quality', NAMESPACES)
        if quality_elem is not None and quality_elem.text:
            quality_map = {
                '0': 'measured',
                '1': 'measured',
                '7': 'estimated',
                '8': 'estimated',
            }
            quality = quality_map.get(quality_elem.text, 'unknown')
        
        return {
            'start': start_dt.isoformat(),
            'duration_seconds': duration,
            'value': value_kwh,
            'cost_cents': cost_cents,
            'quality': quality,
        }
    except (ValueError, TypeError) as e:
        logger.warning(f"Failed to parse interval reading: {e}")
        return None


def _aggregate_to_billing_periods(
    intervals: List[Dict[str, Any]], 
    unit: str
) -> List[Dict[str, Any]]:
    """
    Aggregate interval readings into monthly billing periods.
    
    Args:
        intervals: List of interval dictionaries
        unit: Unit of measurement ('kWh' or 'therms')
        
    Returns:
        List of billing period dictionaries
    """
    if not intervals:
        return []
    
    # Group intervals by month
    monthly_data: Dict[str, List[Dict]] = {}
    
    for interval in intervals:
        start_dt = datetime.fromisoformat(interval['start'])
        month_key = start_dt.strftime('%Y-%m')
        
        if month_key not in monthly_data:
            monthly_data[month_key] = []
        monthly_data[month_key].append(interval)
    
    # Build billing periods
    billing_periods = []
    
    for month_key in sorted(monthly_data.keys()):
        month_intervals = monthly_data[month_key]
        
        # Calculate aggregates
        total_usage = sum(i['value'] for i in month_intervals)
        total_cost_cents = sum(i.get('cost_cents') or 0 for i in month_intervals)
        
        # Get date range for this month
        start_dates = [datetime.fromisoformat(i['start']) for i in month_intervals]
        start_date = min(start_dates)
        end_date = max(start_dates) + timedelta(seconds=month_intervals[-1]['duration_seconds'])
        
        # Check quality flags
        quality_flags = []
        estimated_count = sum(1 for i in month_intervals if i.get('quality') == 'estimated')
        if estimated_count > len(month_intervals) * 0.1:
            quality_flags.append('contains_estimates')
        
        # Check for coverage (rough check based on interval count)
        # Assuming hourly intervals, we'd expect ~720 intervals for a 30-day month
        expected_intervals = 30 * 24  # hourly
        if len(month_intervals) < expected_intervals * 0.9:
            quality_flags.append('partial_coverage')
        
        billing_periods.append({
            'month': month_key,
            'start_date': start_date.date().isoformat(),
            'end_date': end_date.date().isoformat(),
            'usage': round(total_usage, 2),
            'cost_usd': round(total_cost_cents / 100.0, 2) if total_cost_cents else None,
            'unit': unit,
            'num_intervals': len(month_intervals),
            'quality_flags': quality_flags,
        })
    
    return billing_periods


def normalize_usage_to_summary(
    raw_data: Dict[str, Any], 
    fuel_type: str = 'electric',
    data_format: str = 'espi_atom_xml'
) -> Dict[str, Any]:
    """
    Convert parsed usage data into the normalized summary structure.
    
    This creates the format expected by UtilityUsageSummary model and
    the Energy Usage Agent.
    
    Args:
        raw_data: Parsed data from parse_espi_atom_xml or aggregator
        fuel_type: 'electric' or 'gas' (override if needed)
        data_format: Source format identifier
        
    Returns:
        Dictionary suitable for UtilityUsageSummary:
        {
            'fuel_type': 'electric' | 'gas',
            'start_date': '2024-01-01',
            'end_date': '2024-12-31',
            'annual_usage': 12000.0,  # kWh or therms
            'annual_cost_usd': 1800.0,
            'unit': 'kWh' | 'therms',
            'monthly_breakdown': [...],
            'seasonal_pattern': {...},
            'data_quality_flags': [...]
        }
    """
    if not raw_data or 'error' in raw_data:
        return {
            'fuel_type': fuel_type,
            'error': raw_data.get('error', 'No data available'),
            'monthly_breakdown': [],
            'data_quality_flags': ['no_data'],
        }
    
    billing_periods = raw_data.get('billing_periods', [])
    
    if not billing_periods:
        return {
            'fuel_type': raw_data.get('fuel_type', fuel_type),
            'unit': raw_data.get('unit', 'kWh'),
            'monthly_breakdown': [],
            'data_quality_flags': ['no_billing_periods'],
        }
    
    # Extract date range
    start_dates = [bp['start_date'] for bp in billing_periods]
    end_dates = [bp['end_date'] for bp in billing_periods]
    
    # Calculate totals
    total_usage = sum(bp['usage'] for bp in billing_periods)
    total_cost = sum(bp.get('cost_usd') or 0 for bp in billing_periods)
    
    # Annualize if less than 12 months
    months_covered = len(billing_periods)
    if months_covered < 12 and months_covered > 0:
        annual_usage = (total_usage / months_covered) * 12
        annual_cost = (total_cost / months_covered) * 12 if total_cost else None
    else:
        annual_usage = total_usage
        annual_cost = total_cost if total_cost else None
    
    # Build monthly breakdown
    monthly_breakdown = []
    for bp in billing_periods:
        monthly_breakdown.append({
            'month': bp['month'],
            'usage': bp['usage'],
            'cost_usd': bp.get('cost_usd'),
            'unit': bp['unit'],
        })
    
    # Calculate seasonal pattern
    seasonal_pattern = _calculate_seasonal_pattern(billing_periods)
    
    # Collect quality flags
    all_flags = []
    for bp in billing_periods:
        all_flags.extend(bp.get('quality_flags', []))
    
    # Add summary-level flags
    quality_flags = detect_data_quality_issues({
        'billing_periods': billing_periods,
        'months_covered': months_covered,
    })
    
    # Deduplicate flags
    quality_flags = list(set(all_flags + quality_flags))
    
    return {
        'fuel_type': raw_data.get('fuel_type', fuel_type),
        'start_date': min(start_dates),
        'end_date': max(end_dates),
        'annual_usage': round(annual_usage, 2),
        'annual_cost_usd': round(annual_cost, 2) if annual_cost else None,
        'unit': raw_data.get('unit', 'kWh'),
        'monthly_breakdown': monthly_breakdown,
        'seasonal_pattern': seasonal_pattern,
        'tou_data': None,  # TOU parsing would require additional data
        'data_quality_flags': quality_flags,
        'months_covered': months_covered,
    }


def _calculate_seasonal_pattern(billing_periods: List[Dict[str, Any]]) -> Optional[Dict[str, float]]:
    """
    Calculate seasonal usage averages.
    
    Args:
        billing_periods: List of billing period dictionaries
        
    Returns:
        Dictionary with seasonal averages or None if insufficient data
    """
    if len(billing_periods) < 3:
        return None
    
    seasons = {
        'winter': [],    # Dec, Jan, Feb
        'spring': [],    # Mar, Apr, May
        'summer': [],    # Jun, Jul, Aug
        'fall': [],      # Sep, Oct, Nov
    }
    
    season_map = {
        '12': 'winter', '01': 'winter', '02': 'winter',
        '03': 'spring', '04': 'spring', '05': 'spring',
        '06': 'summer', '07': 'summer', '08': 'summer',
        '09': 'fall', '10': 'fall', '11': 'fall',
    }
    
    for bp in billing_periods:
        month = bp['month'].split('-')[1]  # Extract MM from YYYY-MM
        season = season_map.get(month)
        if season:
            seasons[season].append(bp['usage'])
    
    # Calculate averages for seasons with data
    pattern = {}
    for season, values in seasons.items():
        if values:
            pattern[season] = round(sum(values) / len(values), 2)
    
    return pattern if pattern else None


def detect_data_quality_issues(summary: Dict[str, Any]) -> List[str]:
    """
    Detect data quality issues in usage summary.
    
    Flags:
    - partial_data_N_months: Less than 12 months of data
    - data_gaps_detected: Missing months in the date range
    - high_variance: Coefficient of variation > 100%
    - anomalous_spikes: Values > 2 standard deviations from mean
    - contains_estimates: Significant estimated readings
    
    Args:
        summary: Dictionary with billing_periods and/or monthly_breakdown
        
    Returns:
        List of data quality flag strings
    """
    flags = []
    
    # Get billing periods
    billing_periods = summary.get('billing_periods', [])
    if not billing_periods:
        billing_periods = summary.get('monthly_breakdown', [])
    
    if not billing_periods:
        flags.append('no_data')
        return flags
    
    months_covered = summary.get('months_covered', len(billing_periods))
    
    # Check for partial data
    if months_covered < 12:
        flags.append(f'partial_data_{months_covered}_months')
    
    # Check for gaps
    if len(billing_periods) >= 2:
        months = sorted([bp.get('month', bp.get('start_date', ''))[:7] for bp in billing_periods])
        
        # Check for missing months
        for i in range(1, len(months)):
            prev_year, prev_month = map(int, months[i-1].split('-'))
            curr_year, curr_month = map(int, months[i].split('-'))
            
            # Calculate expected next month
            expected_month = prev_month + 1
            expected_year = prev_year
            if expected_month > 12:
                expected_month = 1
                expected_year += 1
            
            if curr_year != expected_year or curr_month != expected_month:
                flags.append('data_gaps_detected')
                break
    
    # Check for variance and anomalies
    usages = [bp.get('usage', 0) for bp in billing_periods if bp.get('usage')]
    
    if len(usages) >= 3:
        avg = statistics.mean(usages)
        std_dev = statistics.stdev(usages)
        
        # High variance check
        if avg > 0 and std_dev / avg > 1.0:
            flags.append('high_variance')
        
        # Anomalous spike check
        if std_dev > 0:
            for usage in usages:
                if abs(usage - avg) > 2 * std_dev:
                    flags.append('anomalous_spikes')
                    break
    
    return flags


def parse_aggregator_response(response_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parse response from UtilityAPI or similar aggregator.
    
    Aggregators typically return JSON with a different structure than
    ESPI. This normalizes that to our internal format.
    
    Args:
        response_data: JSON response from aggregator API
        
    Returns:
        Normalized data dictionary matching parse_espi_atom_xml output
    """
    # This is a placeholder for UtilityAPI-specific parsing
    # Will be implemented in Phase 3 when UtilityAPI provider is added
    result = {
        'fuel_type': 'electric',
        'unit': 'kWh',
        'intervals': [],
        'billing_periods': [],
        'meter_id': None,
        'service_kind': None,
        'metadata': {},
    }
    
    # UtilityAPI typically returns bills with usage data
    bills = response_data.get('bills', [])
    
    for bill in bills:
        start_date = bill.get('bill_start_date') or bill.get('start')
        end_date = bill.get('bill_end_date') or bill.get('end')
        
        if not start_date or not end_date:
            continue
        
        # Determine month key
        start_dt = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
        month_key = start_dt.strftime('%Y-%m')
        
        result['billing_periods'].append({
            'month': month_key,
            'start_date': start_date[:10],
            'end_date': end_date[:10],
            'usage': bill.get('bill_total_kwh', 0),
            'cost_usd': bill.get('bill_total_cost'),
            'unit': 'kWh',
            'num_intervals': 0,
            'quality_flags': [],
        })
    
    # Sort by month
    result['billing_periods'].sort(key=lambda x: x['month'])
    
    return result


# Export public interface
__all__ = [
    'parse_espi_atom_xml',
    'normalize_usage_to_summary',
    'detect_data_quality_issues',
    'parse_aggregator_response',
    'UsageInterval',
    'BillingPeriod',
    'ParsedUsageData',
]
