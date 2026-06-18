-- #3

MERGE [dsp].[TenuredWorkforce] AS tgt
USING (
    SELECT DISTINCT
        LTRIM(RTRIM([transporter_id])) AS transporter_id,
        [name]                         AS full_name,
        [year],
        [week],
        [lifetime_routes],
        [tenure_status],
        [driver_status]
    FROM [stg].[TenureReport]
) AS src
ON tgt.transporter_id = src.transporter_id
WHEN NOT MATCHED BY TARGET
     AND UPPER(src.tenure_status) = 'TENURED'
THEN
    INSERT (
        transporter_id,
        full_name,
        tenured_date,
        tenured_source,
        tenured_year,
        tenured_week,
        routes_at_tenure,
        is_active
    )
    VALUES (
        src.transporter_id,
        src.full_name,
        NULL,                    -- you could map this via your WeekPointer if you want an exact date
        'AMZ_TENURE_REPORT',
        src.[year],
        src.[week],
        src.[lifetime_routes],
        CASE WHEN UPPER(src.driver_status) = 'ACTIVE' THEN 1 ELSE 0 END
    )
WHEN MATCHED THEN
    UPDATE SET
        tgt.full_name        = src.full_name,
        tgt.routes_at_tenure = COALESCE(tgt.routes_at_tenure, src.[lifetime_routes]),
        tgt.is_active        = CASE 
                                  WHEN UPPER(src.driver_status) = 'ACTIVE' THEN 1 
                                  ELSE tgt.is_active 
                               END,
        tgt.updated_utc      = sysutcdatetime();
