# ADR-002: Homeowner Portal Authentication via Google Place ID + Phone

## Status
Accepted

## Context
The Sustainrgy platform needed a way for homeowners to contribute their energy data (utility bills, utility connections, solar telemetry) before or without an in-person audit. This required a low-friction authentication mechanism that:

1. Does not require account creation (email, password)
2. Associates data with the correct property/audit
3. Allows homeowners to return and add more data
4. Maintains data integrity without revealing personal information

## Decision
We chose to authenticate homeowners using **Google Place ID + phone number** rather than traditional account creation, SMS OTP, or magic links.

### How It Works

1. Homeowner visits the portal and enters their address using Google Places autocomplete
2. System captures the Google `place_id` (unique identifier for that address)
3. Homeowner enters their phone number
4. Backend looks up `Property` by `google_place_id` + `phone_number`:
   - Match found → Issue JWT scoped to existing property/audit
   - No match → Create new Property + Audit, store place_id and phone, issue JWT
5. JWT expires after 24 hours; homeowner can re-authenticate anytime

## Rationale

### Why Google Place ID

1. **Address Standardization**
   - Google Places normalizes address formats ("123 Main Street" vs "123 Main St")
   - `place_id` is a stable unique identifier—no string matching edge cases
   - Same autocomplete UX used in mobile app's PropertyFormPage

2. **Data Quality**
   - Ensures addresses are valid, real locations
   - Captures structured `address_components` (street, city, state, zip)
   - Prevents typos and invalid entries

3. **Exact Matching**
   - No fuzzy matching needed—`place_id` is exact
   - No need for address normalization utilities
   - Works across different representations of the same address

### Why Phone Number (Not OTP/Magic Link)

1. **Low Friction**
   - No SMS costs or delivery delays
   - No email collection required
   - Instant authentication

2. **Appropriate Security Level**
   - Data submitted is informational (bills, solar data)
   - No PII access, payment info, or account management
   - Phone number is soft verification, not high-security auth

3. **Re-authentication Simplicity**
   - Homeowner remembers their own address + phone
   - No "forgot password" flows needed
   - No magic link expiration issues

### Alternatives Considered

**SMS OTP Verification**
- Rejected: Adds cost (~$0.01/SMS) and complexity
- Rejected: Delivery delays hurt UX
- Rejected: Overkill for informational data submission
- Would reconsider if: we add sensitive features (payment, PII editing)

**Magic Link via Email**
- Rejected: Requires email collection (friction)
- Rejected: Email delivery/spam folder issues
- Rejected: Link expiration causes confusion
- Would reconsider if: we add account features

**Traditional Account (Email + Password)**
- Rejected: Maximum friction for minimal benefit
- Rejected: Password management overhead
- Rejected: Homeowners may only interact once per audit
- Would reconsider if: we add persistent homeowner features

**Address String Matching**
- Rejected: Brittle—formatting variations cause mismatches
- Rejected: Requires complex normalization ("St" vs "Street", etc.)
- Rejected: False negatives frustrate returning users

## Consequences

### Positive
- Zero friction account creation—address + phone = done
- Standardized address data via Google Places
- Easy re-authentication for returning homeowners
- No password reset flows or email verification
- Consistent with mobile app address entry UX

### Negative
- Phone number could theoretically be guessed (acceptable for current data sensitivity)
- Google Places API has usage costs (minimal for auth flows)
- Property must store `google_place_id` (new column)

### Mitigations
- Rate limit auth endpoint (10 req/min per IP)
- If data sensitivity increases, can layer on SMS OTP without changing flow
- JWT expiration limits exposure window

## Related
- Backend: `Property.google_place_id`, `Property.phone_number` columns
- Backend: `POST /api/homeowner/auth` endpoint
- Web: Google Places Autocomplete integration
- Mobile: Existing `useAddressAutocomplete` hook (reference implementation)
