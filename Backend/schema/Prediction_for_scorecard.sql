CREATE VIEW dsp.v_WeeklyForScorecard_FromScorecard
AS
WITH base AS (
    SELECT
        year,
        week,
        drivers_on_route,
        overall_score_average,
        speeding_event_rate_avg,
        seatbelt_off_event_rate_avg,
        sign_signal_violation_rate_avg,
        distraction_event_rate_avg,
        following_distance_event_rate_avg,
        dcr_pct_avg,
        dsb_dpmo_avg,
        pod_success_rate_avg,
        cdf_dpmo_avg,
        ced_dpmo_avg,
        tenured_workforce_pct_avg,
        fleet_defect_rate_avg
    FROM dsp.v_WeeklyScorecard_Aggregates
)

-- SAFETY
SELECT
    year,
    week,
    'DSP Overall Average Safty Score'       AS metric_name,
    CAST(overall_score_avg AS decimal(18,4)) AS metric_value
FROM base

UNION ALL
SELECT
    year,
    week,
    'Speeding Event Rate'
    CAST(speeding_event_rate_avg AS decimal(18,4))
FROM base

UNION ALL
SELECT
    year,
    week,
    'Seatbelt Off Event Rate',
    CAST(seatbelt_off_event_rate_avg AS decimal(18,4))
FROM base

UNION ALL
SELECT
    year,
    week,
    'Sign/Signal Violation Rate',
    CAST(sign_signal_violation_rate_avg AS decimal(18,4))
FROM base

UNION ALL
SELECT
    year,
    week,
    'Distraction Event Rate',
    CAST(distraction_event_rate_avg AS decimal(18,4))
FROM base

UNION ALL
SELECT
    year,
    week,
    'Following Distance Event Rate',
    CAST(following_distance_event_rate_avg AS decimal(18,4))
FROM base

-- DELIVERY QUALITY
UNION ALL
SELECT
    year,
    week,
    'Delivery Completion Rate %',
    CAST(dcr_pct_avg AS decimal(18,4))
FROM base

UNION ALL
SELECT
    year,
    week,
    'DSB DPMO',
    CAST(dsb_dpmo_avg AS decimal(18,4))
FROM base

UNION ALL
SELECT
    year,
    week,
    'POD Success Rate',
    CAST(pod_success_rate_avg AS decimal(18,4))
FROM base

-- CUSTOMER DELIVERY EXPERIENCE
UNION ALL
SELECT
    year,
    week,
    'CDF DPMO',
    CAST(cdf_dpmo_avg AS decimal(18,4))
FROM base

UNION ALL
SELECT
    year,
    week,
    'CED DPMO',
    CAST(ced_dpmo_avg AS decimal(18,4))
FROM base

-- TEAM & FLEET
UNION ALL
SELECT
    year,
    week,
    'Tenured Workforce %',
    CAST(tenured_workforce_pct_avg AS decimal(18,4))
FROM base

UNION ALL
SELECT
    year,
    week,
    'Fleet Defect Rate',
    CAST(fleet_defect_rate_avg AS decimal(18,4))
FROM base;
GO
