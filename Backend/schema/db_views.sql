-- db_views.sql
-- Creates a simple view to see all user tables in the current database, with row counts.

IF OBJECT_ID(N'dbo.vw_AllTables', N'V') IS NOT NULL
    DROP VIEW dbo.vw_AllTables;
GO
CREATE VIEW dbo.vw_AllTables
AS
SELECT
    s.name  AS schema_name,
    t.name  AS table_name,
    SUM(ps.row_count) AS row_count,
    t.create_date,
    t.modify_date
FROM sys.tables AS t
JOIN sys.schemas AS s
    ON s.schema_id = t.schema_id
LEFT JOIN sys.dm_db_partition_stats AS ps
    ON ps.object_id = t.object_id
   AND ps.index_id IN (0,1)       -- heap or clustered index
GROUP BY s.name, t.name, t.create_date, t.modify_date;
GO

-- Usage:
-- SELECT * FROM dbo.vw_AllTables ORDER BY row_count DESC, schema_name, table_name;
