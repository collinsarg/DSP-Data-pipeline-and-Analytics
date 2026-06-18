-- #4

CREATE OR ALTER PROCEDURE [dsp].[usp_UpdateTenuredFromServiceDetails]
AS
BEGIN
    SET NOCOUNT ON;

    -- 1. Base set: all active associates with valid service-detail rows
    ;WITH ServiceDetailsWithAssoc AS (
        SELECT
            a.transporter_id,
            a.full_name,
            a.working_status,
            s.row_id,
            s.row_date
        FROM [dsp].[WST_ServiceDetails] s
        INNER JOIN [dsp].[Associate] a
            ON UPPER(LTRIM(RTRIM(s.delivery_associate))) = UPPER(LTRIM(RTRIM(a.full_name)))
        WHERE
            (s.excluded_flag IS NULL OR s.excluded_flag <> 'Y')
            AND a.working_status IN ('Active', 'ACTIVE')
    ),

    -- 2. Add route ordinal and total route count per transporter
    RoutesWithOrdinal AS (
        SELECT
            transporter_id,
            full_name,
            row_date,
            ROW_NUMBER() OVER (
                PARTITION BY transporter_id
                ORDER BY row_date, row_id
            ) AS route_ordinal,
            COUNT(*) OVER (
                PARTITION BY transporter_id
            ) AS total_routes
        FROM ServiceDetailsWithAssoc
    ),

    -- 3. For each transporter, find the date of the 30th route (if it exists)
    TenureCandidates AS (
        SELECT
            transporter_id,
            full_name,
            MIN(CASE WHEN route_ordinal = 30 THEN row_date END) AS tenured_date,
            MAX(total_routes) AS total_routes
        FROM RoutesWithOrdinal
        GROUP BY transporter_id, full_name
    )

    -- 4. Insert any new tenured employees (>=30 routes, not already in TenuredWorkforce)
    INSERT INTO [dsp].[TenuredWorkforce] (
        transporter_id,
        full_name,
        tenured_date,
        tenured_source,
        tenured_year,
        tenured_week,
        routes_at_tenure,
        is_active
    )
    SELECT
        c.transporter_id,
        c.full_name,
        c.tenured_date,
        'WST_SERVICEDETAILS',
        YEAR(c.tenured_date),
        DATEPART(ISO_WEEK, c.tenured_date),
        c.total_routes,
        1
    FROM TenureCandidates c
    LEFT JOIN [dsp].[TenuredWorkforce] t
        ON t.transporter_id = c.transporter_id
    WHERE
        t.transporter_id IS NULL          -- not already tenured
        AND c.total_routes >= 30          -- actually crossed 30 routes
        AND c.tenured_date IS NOT NULL;   -- should be non-null when total_routes >= 30
END;
GO


-- Add to Ingestion after WST_ServiceDetails
--EXEC [dsp].[usp_UpdateTenuredFromServiceDetails];
