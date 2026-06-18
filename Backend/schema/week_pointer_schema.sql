/* Run this in your existing database */
SET NOCOUNT ON;
SET XACT_ABORT ON;
SET DATEFIRST 7; -- Sunday

/* ===== Optional: rebuild cleanly ===== */
IF OBJECT_ID('dbo.WeekPointer','U') IS NOT NULL
    DROP TABLE dbo.WeekPointer;

/* ===== Table ===== */
CREATE TABLE dbo.WeekPointer
(
    [Date]        date         NOT NULL PRIMARY KEY,   -- one row per calendar date
    WeekStart     date         NOT NULL,               -- Sunday
    WeekEnd       date         NOT NULL,               -- Saturday
    WeekYear      int          NOT NULL,               -- year whose Jan 1 lies in this week
    WeekOfYear    int          NOT NULL,               -- 1..N within WeekYear
    [Year]        int          NOT NULL,               -- calendar year of [Date]
    [Month]       tinyint      NOT NULL,               -- 1..12
    [DayOfMonth]  tinyint      NOT NULL,               -- 1..31
    [DayName]     nvarchar(10) NOT NULL                -- Sunday..Saturday
);

-- Helpful non-unique indexes
CREATE NONCLUSTERED INDEX IX_WeekPointer_Week
    ON dbo.WeekPointer(WeekYear, WeekOfYear)
    INCLUDE ([Date], WeekStart, WeekEnd);

CREATE NONCLUSTERED INDEX IX_WeekPointer_WeekStart
    ON dbo.WeekPointer(WeekStart)
    INCLUDE ([Date], WeekEnd, WeekYear, WeekOfYear);

CREATE NONCLUSTERED INDEX IX_WeekPointer_Date
    ON dbo.WeekPointer([Date]);

/* ===== Data fill params ===== */
DECLARE @StartYear  int = YEAR(GETDATE());   -- start Jan 1 of this year
DECLARE @YearsAhead int = 10;                -- cover next 10 full years
DECLARE @DateFrom   date = DATEFROMPARTS(@StartYear, 1, 1);
DECLARE @DateThru   date = DATEFROMPARTS(@StartYear + @YearsAhead, 12, 31);

/* ===== Generate and load ===== */
;WITH DateSeries AS
(
    SELECT @DateFrom AS d
    UNION ALL
    SELECT DATEADD(DAY, 1, d)
    FROM DateSeries
    WHERE d < @DateThru
),
Base AS
(
    SELECT
        d                                                       AS [Date],
        -- Sunday-based week start/end for date d
        DATEADD(DAY, 1 - DATEPART(WEEKDAY, d), d)               AS WeekStart,
        DATEADD(DAY, 6 - (DATEPART(WEEKDAY, d)-1), d)           AS WeekEnd,
        YEAR(d)                                                 AS [Year],
        MONTH(d)                                                AS [Month],
        DAY(d)                                                  AS [DayOfMonth],
        DATENAME(WEEKDAY, d)                                    AS [DayName]
    FROM DateSeries
),
WithYearRule AS
(
    SELECT
        [Date], WeekStart, WeekEnd, [Year], [Month], [DayOfMonth], [DayName],
        /* Your convention:
           WeekYear = the calendar year whose Jan 1 falls inside this Sunday–Saturday span. */
        CASE
            WHEN WeekEnd   >= DATEFROMPARTS(YEAR(WeekStart)+1,1,1)
             AND WeekStart <  DATEFROMPARTS(YEAR(WeekStart)+1,1,1)
                THEN YEAR(WeekStart) + 1
            ELSE YEAR(WeekStart)
        END AS WeekYear
    FROM Base
),
Final AS
(
    SELECT
        W.[Date],
        W.WeekStart,
        W.WeekEnd,
        W.WeekYear,
        /* Week 1 anchor = Sunday of the week that contains Jan 1 of WeekYear */
        DATEDIFF
        (
            WEEK,
            DATEADD(DAY, 1 - DATEPART(WEEKDAY, DATEFROMPARTS(W.WeekYear,1,1)),
                          DATEFROMPARTS(W.WeekYear,1,1)),
            W.WeekStart
        ) + 1 AS WeekOfYear,
        W.[Year], W.[Month], W.[DayOfMonth], W.[DayName]
    FROM WithYearRule AS W
)
INSERT dbo.WeekPointer
    ([Date], WeekStart, WeekEnd, WeekYear, WeekOfYear, [Year], [Month], [DayOfMonth], [DayName])
SELECT
    [Date], WeekStart, WeekEnd, WeekYear, WeekOfYear, [Year], [Month], [DayOfMonth], [DayName]
FROM Final
OPTION (MAXRECURSION 0);

/* ---- Optional quick check ----
SELECT [Date], WeekStart, WeekEnd, WeekYear, WeekOfYear
FROM dbo.WeekPointer
WHERE [Date] BETWEEN '2024-12-29' AND '2025-01-11'
ORDER BY [Date];
*/
