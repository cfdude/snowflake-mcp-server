-- Movement Mortgage Attribution-Corrected Analysis Queries
-- Implements timeline-based attribution methodology discovered through Joey Robinson analysis
-- Prevents false ROI claims and separates platform-correlated from organic success
-- Generated: 2025-08-19

-- ============================================================================
-- QUERY 1: MOVEMENT ATTRIBUTION REALITY CHECK
-- Separates platform-correlated from organic loan production
-- EXPORT FILENAME: movement_attribution_reality.csv
-- ============================================================================

WITH movement_platform_properties AS (
    -- Properties where Movement LOs had Highway platform activity
    SELECT DISTINCT 
        e.DISTINCT_ID as lo_id,
        UPPER(TRIM(SPLIT_PART(TRY_PARSE_JSON(e.PROPERTIES):address::STRING, ',', 1))) as street_address,
        COUNT(e.NAME) as platform_events,
        MIN(e.TIME) as first_activity,
        MAX(e.TIME) as last_activity
    FROM FIVETRAN.MIXPANEL.EVENT e
    INNER JOIN FIVETRAN.FACT_TABLES.LO_FACTS lf ON e.DISTINCT_ID = lf.lo_id
    WHERE e.TIME >= '2024-01-01'
      AND LOWER(lf.email) LIKE '%@movement.com'
      AND TRY_PARSE_JSON(e.PROPERTIES):address IS NOT NULL
    GROUP BY 1, 2
),

movement_loan_outcomes AS (
    -- All Movement loans with platform correlation validation
    SELECT 
        p.RECORDINGDATE as close_date,
        UPPER(TRIM(p.PROPERTYFULLSTREETADDRESS)) as street_address,
        p.PROPERTYFULLSTREETADDRESS as full_address,
        p.LOANAMOUNT as loan_amount,
        p.LOANOFFICERNMLS_ID as nmls_id,
        p.LOANOFFICERNAME as lo_name,
        p.LOANTYPE,
        p.PROPERTYCITYNAME as city,
        p.PROPERTYSTATE as state,
        mpp.lo_id,
        CASE 
            WHEN mpp.street_address IS NOT NULL THEN 'PLATFORM_CORRELATED'
            ELSE 'ORGANIC'
        END as attribution_type,
        mpp.platform_events,
        mpp.first_activity,
        mpp.last_activity,
        CASE 
            WHEN mpp.street_address IS NOT NULL 
            THEN DATEDIFF(day, mpp.last_activity, p.RECORDINGDATE)
            ELSE NULL
        END as days_platform_to_close
    FROM AURORA_MARKET_DATA_BLACKKNIGHT.LO_PRODUCTION_CLEAN p
    LEFT JOIN movement_platform_properties mpp 
        ON UPPER(TRIM(p.PROPERTYFULLSTREETADDRESS)) = mpp.street_address
    INNER JOIN FIVETRAN.VANDER.VANDER_LO_FACTS vlf 
        ON p.LOANOFFICERNMLS_ID = vlf.ENTERPRISE_NMLS_ID
    WHERE p.RECORDINGDATE >= '2024-01-01'
      AND vlf.enterprise_nmls_id = '39179'  -- Movement Mortgage enterprise ID
),

attribution_summary AS (
    SELECT 
        attribution_type,
        COUNT(*) as loan_count,
        COUNT(DISTINCT nmls_id) as unique_los,
        SUM(loan_amount) as total_volume,
        AVG(loan_amount) as avg_loan_size,
        AVG(platform_events) as avg_events_per_property,
        AVG(days_platform_to_close) as avg_days_to_close
    FROM movement_loan_outcomes
    GROUP BY attribution_type
)

SELECT 
    'MOVEMENT ATTRIBUTION REALITY' as analysis_type,
    as1.attribution_type,
    as1.loan_count,
    as1.unique_los,
    ROUND(as1.total_volume, 0) as total_volume,
    ROUND(as1.avg_loan_size, 0) as avg_loan_size,
    ROUND((as1.loan_count::FLOAT / (SELECT SUM(loan_count) FROM attribution_summary)) * 100, 1) as percentage_of_loans,
    ROUND((as1.total_volume::FLOAT / (SELECT SUM(total_volume) FROM attribution_summary)) * 100, 1) as percentage_of_volume,
    as1.avg_events_per_property,
    as1.avg_days_to_close,
    CASE 
        WHEN as1.attribution_type = 'PLATFORM_CORRELATED' 
        THEN ROUND(as1.total_volume / NULLIF(as1.avg_events_per_property * as1.loan_count, 0), 0)
        ELSE NULL
    END as volume_per_platform_event
FROM attribution_summary as1
ORDER BY as1.total_volume DESC;

-- ============================================================================
-- QUERY 2: MOVEMENT PLATFORM ROI VALIDATION
-- Calculates ROI only on platform-attributable volume (prevents Joey Robinson error)
-- EXPORT FILENAME: movement_platform_roi_validation.csv
-- ============================================================================

WITH movement_attribution AS (
    -- Reuse attribution logic from Query 1
    SELECT 
        CASE 
            WHEN mpp.street_address IS NOT NULL THEN 'PLATFORM_CORRELATED'
            ELSE 'ORGANIC'
        END as attribution_type,
        p.LOANAMOUNT as loan_amount,
        p.LOANOFFICERNMLS_ID as nmls_id
    FROM AURORA_MARKET_DATA_BLACKKNIGHT.LO_PRODUCTION_CLEAN p
    LEFT JOIN (
        SELECT DISTINCT 
            UPPER(TRIM(SPLIT_PART(TRY_PARSE_JSON(e.PROPERTIES):address::STRING, ',', 1))) as street_address
        FROM FIVETRAN.MIXPANEL.EVENT e
        INNER JOIN FIVETRAN.FACT_TABLES.LO_FACTS lf ON e.DISTINCT_ID = lf.lo_id
        WHERE e.TIME >= '2024-01-01'
          AND LOWER(lf.email) LIKE '%@movement.com'
          AND TRY_PARSE_JSON(e.PROPERTIES):address IS NOT NULL
    ) mpp ON UPPER(TRIM(p.PROPERTYFULLSTREETADDRESS)) = mpp.street_address
    INNER JOIN FIVETRAN.VANDER.VANDER_LO_FACTS vlf 
        ON p.LOANOFFICERNMLS_ID = vlf.ENTERPRISE_NMLS_ID
    WHERE p.RECORDINGDATE >= '2024-01-01'
      AND vlf.enterprise_nmls_id = '39179'
),

movement_subscription_costs AS (
    -- Get Movement's actual Highway platform investment
    SELECT 
        SUM(s.LIST_PRICE * DATEDIFF('month', s.SERVICE_START_DATE, 
            COALESCE(s.CANCELLATION_DATE, CURRENT_DATE()))) as total_investment
    FROM FIVETRAN.ORDWAY_LABS.SUBSCRIPTION s
    JOIN FIVETRAN.ORDWAY_LABS.CUSTOMER c ON s.CUSTOMER_ID = c.ID
    WHERE (LOWER(c.LR_USER_EMAIL) LIKE '%@movement.com'
           OR LOWER(c.CONTACT_EMAIL) LIKE '%@movement.com'
           OR c.ENTERPRISE_NMLS_ID = '39179')
      AND s.CREATED_DATE >= '2024-01-01'
),

attribution_metrics AS (
    SELECT 
        SUM(CASE WHEN attribution_type = 'PLATFORM_CORRELATED' THEN loan_amount ELSE 0 END) as platform_volume,
        SUM(CASE WHEN attribution_type = 'ORGANIC' THEN loan_amount ELSE 0 END) as organic_volume,
        COUNT(CASE WHEN attribution_type = 'PLATFORM_CORRELATED' THEN 1 END) as platform_loans,
        COUNT(CASE WHEN attribution_type = 'ORGANIC' THEN 1 END) as organic_loans,
        COUNT(DISTINCT CASE WHEN attribution_type = 'PLATFORM_CORRELATED' THEN nmls_id END) as platform_los,
        COUNT(DISTINCT CASE WHEN attribution_type = 'ORGANIC' THEN nmls_id END) as organic_los
    FROM movement_attribution
)

SELECT 
    'MOVEMENT PLATFORM ROI VALIDATION' as analysis_type,
    
    -- Attribution Breakdown
    am.platform_volume,
    am.organic_volume,
    (am.platform_volume + am.organic_volume) as total_volume,
    am.platform_loans,
    am.organic_loans,
    (am.platform_loans + am.organic_loans) as total_loans,
    am.platform_los,
    am.organic_los,
    
    -- Platform Investment & ROI (Honest Calculation)
    msc.total_investment as platform_investment,
    (am.platform_volume * 0.025) as estimated_platform_commission,  -- 2.5% commission
    ROUND((am.platform_volume * 0.025) / NULLIF(msc.total_investment, 0), 1) as platform_roi_multiple,
    
    -- Total Business Context (NOT for ROI calculation)
    ((am.platform_volume + am.organic_volume) * 0.025) as total_estimated_commission,
    ROUND(((am.platform_volume + am.organic_volume) * 0.025) / NULLIF(msc.total_investment, 0), 1) as total_business_multiple,
    
    -- Attribution Rates
    ROUND((am.platform_volume::FLOAT / (am.platform_volume + am.organic_volume)) * 100, 1) as platform_attribution_percentage,
    ROUND((am.platform_loans::FLOAT / (am.platform_loans + am.organic_loans)) * 100, 1) as platform_loan_percentage

FROM attribution_metrics am
CROSS JOIN movement_subscription_costs msc;

-- ============================================================================
-- QUERY 3: MOVEMENT TIMELINE-BASED COMPETITIVE INTELLIGENCE
-- Properties where Movement LOs had platform activity but competitors closed
-- EXPORT FILENAME: movement_competitive_intelligence.csv
-- ============================================================================

WITH movement_platform_properties AS (
    -- Properties where Movement LOs had platform activity
    SELECT DISTINCT 
        UPPER(TRIM(SPLIT_PART(TRY_PARSE_JSON(e.PROPERTIES):address::STRING, ',', 1))) as street_address,
        e.DISTINCT_ID as lo_id,
        MAX(e.TIME) as last_movement_activity,
        COUNT(e.NAME) as movement_activity_count
    FROM FIVETRAN.MIXPANEL.EVENT e
    INNER JOIN FIVETRAN.FACT_TABLES.LO_FACTS lf ON e.DISTINCT_ID = lf.lo_id
    WHERE e.TIME >= '2024-01-01'
      AND LOWER(lf.email) LIKE '%@movement.com'
      AND TRY_PARSE_JSON(e.PROPERTIES):address IS NOT NULL
    GROUP BY 1, 2
),

movement_wins AS (
    -- Properties Movement actually closed
    SELECT DISTINCT 
        UPPER(TRIM(p.PROPERTYFULLSTREETADDRESS)) as street_address
    FROM AURORA_MARKET_DATA_BLACKKNIGHT.LO_PRODUCTION_CLEAN p
    INNER JOIN FIVETRAN.VANDER.VANDER_LO_FACTS vlf 
        ON p.LOANOFFICERNMLS_ID = vlf.ENTERPRISE_NMLS_ID
    WHERE p.RECORDINGDATE >= '2024-01-01'
      AND vlf.enterprise_nmls_id = '39179'
)

SELECT 
    p.RECORDINGDATE as competitor_close_date,
    p.PROPERTYFULLSTREETADDRESS as property_address,
    p.PROPERTYCITYNAME as city,
    p.PROPERTYSTATE as state,
    p.LOANAMOUNT as lost_volume,
    p.LOANOFFICERNAME as competitor_lo_name,
    p.LOANORGANIZATIONNAME as competitor_company,
    p.LOANTYPE as loan_type,
    p.INTERESTRATE as competitor_rate,
    mpp.last_movement_activity,
    mpp.movement_activity_count,
    DATEDIFF(day, mpp.last_movement_activity, p.RECORDINGDATE) as timeline_gap_days,
    CASE 
        WHEN DATEDIFF(day, mpp.last_movement_activity, p.RECORDINGDATE) <= 30 THEN 'HIGH RISK - Short Gap'
        WHEN DATEDIFF(day, mpp.last_movement_activity, p.RECORDINGDATE) <= 60 THEN 'MEDIUM RISK - Medium Gap'
        ELSE 'LOW RISK - Long Gap'
    END as competitive_risk_level,
    
    -- LO who had the Movement activity
    lf.email as movement_lo_email,
    lf.name as movement_lo_name

FROM AURORA_MARKET_DATA_BLACKKNIGHT.LO_PRODUCTION_CLEAN p
INNER JOIN movement_platform_properties mpp 
    ON UPPER(TRIM(p.PROPERTYFULLSTREETADDRESS)) = mpp.street_address
LEFT JOIN movement_wins mw 
    ON UPPER(TRIM(p.PROPERTYFULLSTREETADDRESS)) = mw.street_address
LEFT JOIN FIVETRAN.FACT_TABLES.LO_FACTS lf ON mpp.lo_id = lf.lo_id

WHERE p.RECORDINGDATE >= '2024-01-01'
  AND p.LOANORGANIZATIONNAME != 'MOVEMENT MORTGAGE, LLC'  -- Not Movement wins
  AND mw.street_address IS NULL  -- Movement did NOT win

ORDER BY timeline_gap_days ASC;

-- ============================================================================
-- QUERY 4: MOVEMENT INDIVIDUAL LO ATTRIBUTION ANALYSIS
-- Shows platform correlation by individual LO (identifies high/low attribution LOs)
-- EXPORT FILENAME: movement_individual_lo_attribution.csv
-- ============================================================================

WITH lo_attribution_analysis AS (
    SELECT 
        p.LOANOFFICERNMLS_ID as nmls_id,
        p.LOANOFFICERNAME as lo_name,
        lf.email as lo_email,
        
        -- Platform-correlated metrics
        COUNT(CASE WHEN mpp.street_address IS NOT NULL THEN 1 END) as platform_correlated_loans,
        SUM(CASE WHEN mpp.street_address IS NOT NULL THEN p.LOANAMOUNT ELSE 0 END) as platform_correlated_volume,
        
        -- Organic metrics
        COUNT(CASE WHEN mpp.street_address IS NULL THEN 1 END) as organic_loans,
        SUM(CASE WHEN mpp.street_address IS NULL THEN p.LOANAMOUNT ELSE 0 END) as organic_volume,
        
        -- Total metrics
        COUNT(*) as total_loans,
        SUM(p.LOANAMOUNT) as total_volume,
        AVG(p.LOANAMOUNT) as avg_loan_size,
        
        -- Platform activity metrics
        AVG(mpp.platform_events) as avg_events_per_platform_property,
        AVG(mpp.days_platform_to_close) as avg_days_platform_to_close

    FROM AURORA_MARKET_DATA_BLACKKNIGHT.LO_PRODUCTION_CLEAN p
    INNER JOIN FIVETRAN.VANDER.VANDER_LO_FACTS vlf 
        ON p.LOANOFFICERNMLS_ID = vlf.ENTERPRISE_NMLS_ID
    LEFT JOIN FIVETRAN.FACT_TABLES.LO_FACTS lf 
        ON LOWER(vlf.LO_EMAIL) = LOWER(lf.email)
    LEFT JOIN (
        -- Platform properties with activity metrics
        SELECT 
            UPPER(TRIM(SPLIT_PART(TRY_PARSE_JSON(e.PROPERTIES):address::STRING, ',', 1))) as street_address,
            e.DISTINCT_ID as lo_id,
            COUNT(e.NAME) as platform_events,
            MAX(e.TIME) as last_activity,
            MIN(e.TIME) as first_activity
        FROM FIVETRAN.MIXPANEL.EVENT e
        INNER JOIN FIVETRAN.FACT_TABLES.LO_FACTS lf ON e.DISTINCT_ID = lf.lo_id
        WHERE e.TIME >= '2024-01-01'
          AND LOWER(lf.email) LIKE '%@movement.com'
          AND TRY_PARSE_JSON(e.PROPERTIES):address IS NOT NULL
        GROUP BY 1, 2
    ) mpp ON UPPER(TRIM(p.PROPERTYFULLSTREETADDRESS)) = mpp.street_address 
         AND lf.lo_id = mpp.lo_id
    
    WHERE p.RECORDINGDATE >= '2024-01-01'
      AND vlf.enterprise_nmls_id = '39179'
    
    GROUP BY 1, 2, 3
),

highway_subscription_status AS (
    -- Get Highway subscription status for each LO
    SELECT 
        muf.nmls_id,
        muf.lo_email,
        muf.substatus as subscription_status,
        muf.total_views_all_time,
        muf.total_views_last_30_days
    FROM FIVETRAN.FACT_TABLES.MBS_USER_FACTS muf
    WHERE LOWER(muf.lo_email) LIKE '%@movement.com'
      AND muf.substatus = 'Active'
)

SELECT 
    laa.nmls_id,
    laa.lo_name,
    laa.lo_email,
    COALESCE(hss.subscription_status, 'Non-Subscriber') as highway_status,
    COALESCE(hss.total_views_all_time, 0) as platform_views_all_time,
    COALESCE(hss.total_views_last_30_days, 0) as platform_views_30_days,
    
    -- Attribution breakdown
    laa.platform_correlated_loans,
    laa.organic_loans,
    laa.total_loans,
    ROUND(laa.platform_correlated_volume, 0) as platform_correlated_volume,
    ROUND(laa.organic_volume, 0) as organic_volume,
    ROUND(laa.total_volume, 0) as total_volume,
    ROUND(laa.avg_loan_size, 0) as avg_loan_size,
    
    -- Attribution rates
    ROUND((laa.platform_correlated_loans::FLOAT / laa.total_loans) * 100, 1) as platform_correlation_rate,
    ROUND((laa.platform_correlated_volume::FLOAT / laa.total_volume) * 100, 1) as platform_volume_rate,
    
    -- Platform efficiency metrics
    laa.avg_events_per_platform_property,
    laa.avg_days_platform_to_close,
    
    -- Performance categorization
    CASE 
        WHEN laa.platform_correlated_loans >= 10 AND (laa.platform_correlated_loans::FLOAT / laa.total_loans) >= 0.3 
        THEN 'HIGH_PLATFORM_CORRELATION'
        WHEN laa.platform_correlated_loans >= 5 AND (laa.platform_correlated_loans::FLOAT / laa.total_loans) >= 0.2
        THEN 'MEDIUM_PLATFORM_CORRELATION'
        WHEN laa.total_loans >= 10 AND laa.platform_correlated_loans <= 2
        THEN 'HIGH_ORGANIC_STRENGTH'
        WHEN laa.total_loans >= 20 AND laa.platform_correlated_loans = 0
        THEN 'PURE_ORGANIC_SUCCESS'
        ELSE 'MIXED_PERFORMANCE'
    END as performance_category

FROM lo_attribution_analysis laa
LEFT JOIN highway_subscription_status hss ON laa.nmls_id = hss.nmls_id

WHERE laa.total_loans >= 5  -- Focus on LOs with meaningful loan volume

ORDER BY laa.total_volume DESC;

-- ============================================================================
-- QUERY 5: MOVEMENT ADDRESS MATCHING VALIDATION
-- Tests address correlation success between Mixpanel and BlackKnight for Movement
-- EXPORT FILENAME: movement_address_matching_validation.csv
-- ============================================================================

WITH movement_mixpanel_addresses AS (
    SELECT DISTINCT 
        e.DISTINCT_ID as lo_id,
        lf.email as lo_email,
        TRY_PARSE_JSON(e.PROPERTIES):address::STRING as full_address,
        UPPER(TRIM(SPLIT_PART(TRY_PARSE_JSON(e.PROPERTIES):address::STRING, ',', 1))) as street_address
    FROM FIVETRAN.MIXPANEL.EVENT e
    INNER JOIN FIVETRAN.FACT_TABLES.LO_FACTS lf ON e.DISTINCT_ID = lf.lo_id
    WHERE e.TIME >= '2024-01-01'
      AND LOWER(lf.email) LIKE '%@movement.com'
      AND TRY_PARSE_JSON(e.PROPERTIES):address IS NOT NULL
),

movement_blackknight_addresses AS (
    SELECT DISTINCT 
        p.LOANOFFICERNMLS_ID as nmls_id,
        p.PROPERTYFULLSTREETADDRESS as full_address,
        UPPER(TRIM(p.PROPERTYFULLSTREETADDRESS)) as street_address
    FROM AURORA_MARKET_DATA_BLACKKNIGHT.LO_PRODUCTION_CLEAN p
    INNER JOIN FIVETRAN.VANDER.VANDER_LO_FACTS vlf 
        ON p.LOANOFFICERNMLS_ID = vlf.ENTERPRISE_NMLS_ID
    WHERE p.RECORDINGDATE >= '2024-01-01'
      AND vlf.enterprise_nmls_id = '39179'
),

match_analysis AS (
    SELECT 
        COUNT(DISTINCT mma.street_address) as mixpanel_unique_addresses,
        COUNT(DISTINCT mba.street_address) as blackknight_unique_addresses,
        COUNT(DISTINCT CASE WHEN mba.street_address IS NOT NULL THEN mma.street_address END) as successful_matches,
        COUNT(DISTINCT mma.lo_email) as los_with_mixpanel_addresses,
        COUNT(DISTINCT CASE WHEN mba.street_address IS NOT NULL THEN mma.lo_email END) as los_with_successful_matches
    FROM movement_mixpanel_addresses mma
    LEFT JOIN movement_blackknight_addresses mba ON mma.street_address = mba.street_address
)

SELECT 
    'MOVEMENT ADDRESS MATCHING VALIDATION' as analysis_type,
    ma.mixpanel_unique_addresses,
    ma.blackknight_unique_addresses,
    ma.successful_matches,
    ma.los_with_mixpanel_addresses,
    ma.los_with_successful_matches,
    ROUND((ma.successful_matches::FLOAT / ma.mixpanel_unique_addresses) * 100, 1) as match_success_rate,
    ROUND((ma.los_with_successful_matches::FLOAT / ma.los_with_mixpanel_addresses) * 100, 1) as lo_success_rate,
    CASE 
        WHEN (ma.successful_matches::FLOAT / ma.mixpanel_unique_addresses) >= 0.30 THEN 'EXCELLENT CORRELATION'
        WHEN (ma.successful_matches::FLOAT / ma.mixpanel_unique_addresses) >= 0.20 THEN 'GOOD CORRELATION'
        WHEN (ma.successful_matches::FLOAT / ma.mixpanel_unique_addresses) >= 0.15 THEN 'ACCEPTABLE CORRELATION'
        ELSE 'POOR CORRELATION - ATTRIBUTION UNRELIABLE'
    END as correlation_quality
FROM match_analysis ma;

-- ============================================================================
-- VALIDATION CHECKLIST FOR MOVEMENT ATTRIBUTION ANALYSIS
-- ============================================================================

-- Before using these results for ROI claims:
-- 1. Run Query 5 first - ensure match success rate >15%
-- 2. Validate sample of matched addresses manually
-- 3. Check Query 1 attribution rates - >50% platform attribution is suspicious
-- 4. Compare Query 2 ROI with Joey Robinson methodology (should be 20-100x range)
-- 5. Review Query 3 competitive losses for timeline patterns
-- 6. Use Query 4 to identify LOs for case study validation

-- KEY INSIGHT: Movement's attribution will likely show:
-- - Lower platform correlation than previously claimed
-- - Significant organic success (traditional relationship-driven)
-- - Platform enhancement rather than platform dependency
-- - Geographic and LO-specific correlation patterns

-- This methodology prevents the Joey Robinson attribution error
-- and provides honest platform value assessment for Movement Mortgage.