# backend/utils/providers/manual.py
"""
Manual Entry Provider

Fallback provider for manual utility bill entry when direct
integrations are not available or user prefers manual input.

This provider:
- Creates connections without OAuth
- Accepts manually entered monthly usage data
- Normalizes data to the same format as other providers
"""

from typing import Optional, List, Dict, Any
from datetime import datetime, date
import logging

from . import (
    UtilityProvider,
    ProviderType,
    AuthResult,
    SyncResult,
    UsageSummary,
    ConnectionStatus,
)

logger = logging.getLogger(__name__)


class ManualEntryProvider(UtilityProvider):
    """
    Provider for manual utility bill entry.
    
    Always available as the final fallback in the waterfall chain.
    Does not require OAuth - user directly enters their usage data.
    """
    
    provider_name = "manual"
    provider_type = ProviderType.MANUAL
    display_name = "Manual Entry"
    supported_utilities = ["*"]  # Supports all utilities
    
    def connect(
        self, 
        audit_id: int, 
        user_id: str, 
        utility_name: str,
        data_scope: str = 'electric'
    ) -> AuthResult:
        """
        Create a connection record for manual entry.
        
        Unlike OAuth providers, this immediately creates a connection
        ready for data entry without requiring user redirect.
        
        Args:
            audit_id: ID of the audit
            user_id: ID of the user
            utility_name: Name of the utility
            data_scope: Type of data ('electric', 'gas', 'both')
            
        Returns:
            AuthResult with connection_id, no redirect needed
        """
        from app import db
        from models import UtilityConnection
        
        try:
            # Create connection record
            connection = UtilityConnection(
                audit_id=audit_id,
                user_id=user_id,
                utility_name=utility_name,
                provider_name=self.provider_name,
                data_scope=data_scope,
                status=ConnectionStatus.CONNECTED.value,  # Ready immediately
                # No tokens needed for manual entry
            )
            db.session.add(connection)
            db.session.commit()
            
            logger.info(f"Manual connection created: {connection.id} for audit {audit_id}")
            
            return AuthResult(
                success=True,
                connection_id=connection.id,
                provider_name=self.provider_name,
                requires_redirect=False,  # No OAuth needed
            )
        except Exception as e:
            logger.exception(f"Failed to create manual connection: {e}")
            db.session.rollback()
            return AuthResult(
                success=False,
                error=f"Failed to create connection: {str(e)}"
            )
    
    def handle_callback(self, code: str, state: str) -> AuthResult:
        """
        Manual provider does not use OAuth, so callback is not applicable.
        
        Returns:
            AuthResult with error
        """
        return AuthResult(
            success=False,
            error="Manual entry provider does not support OAuth callbacks"
        )
    
    def sync_usage(self, connection_id: int) -> SyncResult:
        """
        Calculate summary from manually entered data.
        
        Unlike OAuth providers that fetch data, this method
        re-summarizes the existing manually entered records.
        
        Args:
            connection_id: ID of the UtilityConnection
            
        Returns:
            SyncResult with summary statistics
        """
        from app import db
        from models import UtilityConnection, UtilityUsageData, UtilityUsageSummary
        
        connection = UtilityConnection.query.get(connection_id)
        if not connection:
            return SyncResult(
                success=False,
                error=f"Connection {connection_id} not found"
            )
        
        try:
            # Get all usage records for this connection
            records = UtilityUsageData.query.filter_by(
                connection_id=connection_id
            ).order_by(UtilityUsageData.period_start.asc()).all()
            
            if not records:
                return SyncResult(
                    success=True,
                    records_imported=0,
                    warnings=["No usage data has been entered yet"]
                )
            
            # Calculate summary
            total_usage = sum(r.usage_amount for r in records)
            total_cost = sum(r.cost_usd or 0 for r in records)
            
            start_date = min(r.period_start for r in records)
            end_date = max(r.period_end for r in records)
            
            # Build monthly breakdown
            monthly_breakdown = []
            for record in records:
                monthly_breakdown.append({
                    "month": record.period_start.strftime("%Y-%m"),
                    "usage": record.usage_amount,
                    "cost_usd": record.cost_usd,
                    "unit": record.unit,
                })
            
            # Calculate months covered
            months_covered = len(records)
            
            # Annualize if we have partial data
            if months_covered < 12:
                annual_usage = (total_usage / months_covered) * 12
                annual_cost = (total_cost / months_covered) * 12 if total_cost else None
            else:
                annual_usage = total_usage
                annual_cost = total_cost if total_cost else None
            
            # Calculate seasonal patterns (if enough data)
            seasonal_pattern = self._calculate_seasonal_pattern(records)
            
            # Determine unit from records
            unit = records[0].unit if records else "kWh"
            fuel_type = connection.data_scope or "electric"
            
            # Create or update summary
            summary = UtilityUsageSummary.query.filter_by(
                connection_id=connection_id
            ).first()
            
            if not summary:
                summary = UtilityUsageSummary(
                    connection_id=connection_id,
                    audit_id=connection.audit_id,
                    user_id=connection.user_id,
                )
                db.session.add(summary)
            
            summary.fuel_type = fuel_type
            summary.start_date = start_date
            summary.end_date = end_date
            summary.annual_usage = annual_usage
            summary.annual_cost_usd = annual_cost
            summary.unit = unit
            summary.monthly_breakdown = monthly_breakdown
            summary.seasonal_pattern = seasonal_pattern
            summary.data_quality_flags = self._check_data_quality(records, months_covered)
            summary.updated_at = datetime.utcnow()
            
            # Update connection status
            connection.status = ConnectionStatus.CONNECTED.value
            connection.last_sync_at = datetime.utcnow()
            
            db.session.commit()
            
            return SyncResult(
                success=True,
                records_imported=len(records),
                date_range_start=start_date.isoformat(),
                date_range_end=end_date.isoformat(),
                months_covered=months_covered,
            )
        except Exception as e:
            logger.exception(f"Failed to sync manual usage data: {e}")
            db.session.rollback()
            return SyncResult(
                success=False,
                error=f"Failed to calculate summary: {str(e)}"
            )
    
    def get_normalized_usage(self, connection_id: int) -> Optional[UsageSummary]:
        """
        Get normalized usage summary for manual entry.
        
        Args:
            connection_id: ID of the UtilityConnection
            
        Returns:
            UsageSummary or None if no data
        """
        from models import UtilityUsageSummary
        
        summary = UtilityUsageSummary.query.filter_by(
            connection_id=connection_id
        ).first()
        
        if not summary:
            return None
        
        return UsageSummary(
            fuel_type=summary.fuel_type or "electric",
            start_date=summary.start_date.isoformat() if summary.start_date else "",
            end_date=summary.end_date.isoformat() if summary.end_date else "",
            annual_usage=summary.annual_usage or 0,
            annual_cost_usd=summary.annual_cost_usd,
            monthly_breakdown=summary.monthly_breakdown or [],
            seasonal_pattern=summary.seasonal_pattern,
            tou_data=None,  # Manual entry doesn't support TOU
            data_quality_flags=summary.data_quality_flags or [],
            unit=summary.unit or "kWh",
        )
    
    def revoke(self, connection_id: int) -> bool:
        """
        Revoke manual entry connection.
        
        Marks connection as revoked but preserves data.
        
        Args:
            connection_id: ID of the UtilityConnection
            
        Returns:
            True if successful
        """
        from app import db
        from models import UtilityConnection
        
        try:
            connection = UtilityConnection.query.get(connection_id)
            if connection:
                connection.status = ConnectionStatus.REVOKED.value
                db.session.commit()
                return True
            return False
        except Exception as e:
            logger.exception(f"Failed to revoke connection: {e}")
            db.session.rollback()
            return False
    
    def is_available(self) -> bool:
        """Manual entry is always available."""
        return True
    
    def add_usage_record(
        self,
        connection_id: int,
        period_start: date,
        period_end: date,
        usage_amount: float,
        unit: str = "kWh",
        cost_usd: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Add a manual usage record.
        
        This method is specific to the manual provider.
        
        Args:
            connection_id: ID of the UtilityConnection
            period_start: Start date of the billing period
            period_end: End date of the billing period
            usage_amount: Usage amount (kWh or therms)
            unit: Unit of measurement ('kWh' or 'therms')
            cost_usd: Optional cost in USD
            
        Returns:
            Dictionary with created record info or error
        """
        from app import db
        from models import UtilityConnection, UtilityUsageData
        
        connection = UtilityConnection.query.get(connection_id)
        if not connection:
            return {"success": False, "error": f"Connection {connection_id} not found"}
        
        try:
            record = UtilityUsageData(
                connection_id=connection_id,
                audit_id=connection.audit_id,
                user_id=connection.user_id,
                period_start=period_start,
                period_end=period_end,
                usage_amount=usage_amount,
                unit=unit,
                cost_usd=cost_usd,
                source="manual",
                created_at=datetime.utcnow(),
            )
            db.session.add(record)
            db.session.commit()
            
            logger.info(f"Manual usage record added: {record.id}")
            
            return {
                "success": True,
                "record_id": record.id,
            }
        except Exception as e:
            logger.exception(f"Failed to add usage record: {e}")
            db.session.rollback()
            return {"success": False, "error": str(e)}
    
    def _calculate_seasonal_pattern(self, records: list) -> Optional[Dict[str, float]]:
        """
        Calculate seasonal usage pattern from records.
        
        Groups usage by season (winter, spring, summer, fall).
        
        Args:
            records: List of UtilityUsageData records
            
        Returns:
            Dictionary with seasonal averages or None
        """
        if len(records) < 3:
            return None
        
        seasons = {
            "winter": [],    # Dec, Jan, Feb
            "spring": [],    # Mar, Apr, May
            "summer": [],    # Jun, Jul, Aug
            "fall": [],      # Sep, Oct, Nov
        }
        
        season_map = {
            12: "winter", 1: "winter", 2: "winter",
            3: "spring", 4: "spring", 5: "spring",
            6: "summer", 7: "summer", 8: "summer",
            9: "fall", 10: "fall", 11: "fall",
        }
        
        for record in records:
            month = record.period_start.month
            season = season_map[month]
            seasons[season].append(record.usage_amount)
        
        # Calculate averages for seasons with data
        pattern = {}
        for season, values in seasons.items():
            if values:
                pattern[season] = sum(values) / len(values)
        
        return pattern if pattern else None
    
    def _check_data_quality(self, records: list, months_covered: int) -> List[str]:
        """
        Check data quality and return flags.
        
        Args:
            records: List of UtilityUsageData records
            months_covered: Number of months with data
            
        Returns:
            List of data quality flags
        """
        flags = []
        
        if months_covered < 12:
            flags.append(f"partial_data_{months_covered}_months")
        
        # Check for missing months (gaps)
        if len(records) >= 2:
            dates = sorted([r.period_start for r in records])
            for i in range(1, len(dates)):
                diff_days = (dates[i] - dates[i-1]).days
                if diff_days > 45:  # More than 45 days between records
                    flags.append("data_gaps_detected")
                    break
        
        # Check for unusually high variance
        usages = [r.usage_amount for r in records]
        if len(usages) >= 3:
            avg = sum(usages) / len(usages)
            variance = sum((x - avg) ** 2 for x in usages) / len(usages)
            std_dev = variance ** 0.5
            if std_dev > avg:  # Coefficient of variation > 100%
                flags.append("high_variance")
        
        return flags


# Export
__all__ = ["ManualEntryProvider"]
