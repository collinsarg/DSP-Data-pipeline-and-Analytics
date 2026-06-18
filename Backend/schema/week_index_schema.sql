/* ================== Week Index (one row per week) =======================
   Convention:
     - Sunday = week start, Saturday = week end
     - Week 1 of Y = the Sunday–Saturday week that CONTAINS Jan 1 of Y
   Range:
     - From Week 1 of @StartYear through the last week of @EndYear
========================================================================= */

SET NOCOUNT ON;
SET XACT_ABORT ON;
SET DATEFIRST 7; -- Sunday

DECLARE @StartYear  int = YEAR(GETDATE());
DECLARE @EndYear    int = @StartYear + 10;

-- Sunday of the week that contains Jan 1 of a given year
DECLARE @FirstWeekStart_StartYear date =
    DATEADD(DAY, 1 - DATEPART(WEEKDAY, DATEFROMPARTS(@StartYear,1,1)),
                 DATEFROMPARTS(@StartYear,1,1));

-- First Sunday of Week 1 of the year AFTER @EndYear (exclusive upper bound)
DECLARE @FirstWeekStart_AfterEnd date =
    DATEADD(DAY, 1 - DATEPART(WEEKDAY, DATEFROMPARTS(@EndYear+1,1,1)),
                 DATEFROMPARTS(@EndYear+1,1,1));

/* ---- (Re)create the table (drop if you want a clean rebuild) ---- */
IF OBJECT_ID('dbo.WeekIndex','U') IS NOT NULL
    DROP TABLE dbo.WeekIndex;

CREATE TABLE dbo.WeekIndex
(
    WeekYear   int  NOT NULL,      -- the year whose Jan 1 lies inside this week
    WeekOfYear int  NOT NULL,      -- 1..N within WeekYear
    WeekStart  date NOT NULL,      -- Sunday
    WeekEnd    date NOT NULL,      -- Saturday
    CONSTRAINT PK_WeekIndex PRIMARY KEY (WeekYear, WeekOfYear)
);
CREATE UNIQUE INDEX UX_WeekIndex_WeekStart ON dbo.WeekIndex(WeekStart);

/* ---- Generate the list of weeks, Sunday to Saturday ---- */
;WITH Weeks AS
(
    SELECT @FirstWeekStart_StartYear AS WeekStart
    UNION ALL
    SELECT DATEADD(DAY, 7, WeekStart)
    FROM Weeks
    WHERE WeekStart < DATEADD(DAY, -7, @FirstWeekStart_AfterEnd)
),
-- Determine WeekYear by the "week that contains Jan 1" rule
WithYearRule AS
(
    SELECT
        W.WeekStart,
        DATEADD(DAY, 6, W.WeekStart) AS WeekEnd,
        CASE
            WHEN DATEADD(DAY, 6, W.WeekStart) >= DATEFROMPARTS(YEAR(W.WeekStart)+1,1,1)
             AND W.WeekStart          <  DATEFROMPARTS(YEAR(W.WeekStart)+1,1,1)
                THEN YEAR(W.WeekStart) + 1  -- week spans Jan 1 of next year → next year
            ELSE YEAR(W.WeekStart)          -- otherwise the current year
        END AS WeekYear
    FROM Weeks AS W
),
-- Anchor (first Sunday of Week 1) for every WeekYear in range
Anchors AS
(
    SELECT DISTINCT
        wy.WeekYear,
        DATEADD(DAY, 1 - DATEPART(WEEKDAY, DATEFROMPARTS(wy.WeekYear,1,1)),
                     DATEFROMPARTS(wy.WeekYear,1,1)) AS FirstWeekStart
    FROM WithYearRule AS wy
),
Final AS
(
    SELECT
        wyr.WeekYear,
        /* WeekOfYear = #weeks since the first Sunday of Week 1, +1 */
        DATEDIFF(WEEK, a.FirstWeekStart, wyr.WeekStart) + 1 AS WeekOfYear,
        wyr.WeekStart,
        wyr.WeekEnd
    FROM WithYearRule AS wyr
    JOIN Anchors     AS a
      ON a.WeekYear = wyr.WeekYear
)
INSERT dbo.WeekIndex (WeekYear, WeekOfYear, WeekStart, WeekEnd)
SELECT WeekYear, WeekOfYear, WeekStart, WeekEnd
FROM Final
ORDER BY WeekStart
OPTION (MAXRECURSION 0);

/* --------- Quick checks (optional) ---------
-- Expect: 2024-12-29..2025-01-04 is 2025 Week 1
SELECT * FROM dbo.WeekIndex
WHERE WeekStart BETWEEN '2024-12-22' AND '2025-01-12'
ORDER BY WeekStart;

-- How many weeks per year?
SELECT WeekYear, COUNT(*) AS WeeksInYear
FROM dbo.WeekIndex
GROUP BY WeekYear
ORDER BY WeekYear;
*/
