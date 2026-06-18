-- Add row_sig if missing
IF COL_LENGTH('dsp.Routes','row_sig') IS NULL
BEGIN
  ALTER TABLE dsp.Routes
  ADD row_sig AS CONVERT(VARBINARY(32),
    HASHBYTES('SHA2_256',
      CONCAT(
        CONVERT(varchar(33), snapshot_dt, 126), '|',
        COALESCE(UPPER(LTRIM(RTRIM(route_code))),      '#NULL#'), '|',
        COALESCE(UPPER(LTRIM(RTRIM(transporter_id))),  '#NULL#')
      )
    )
  ) PERSISTED;
END
GO
-- Purge existing duplicates (keep the newest per key)
;WITH d AS (
  SELECT route_row_id,
         ROW_NUMBER() OVER (
           PARTITION BY 
             CONVERT(varchar(33), snapshot_dt, 126),
             UPPER(LTRIM(RTRIM(route_code))),
             UPPER(LTRIM(RTRIM(transporter_id)))
           ORDER BY route_row_id DESC
         ) AS rn
  FROM dsp.Routes
)
DELETE FROM d WHERE rn > 1;
GO

-- Enforce uniqueness (skip dupes instead of failing)
IF NOT EXISTS (
  SELECT 1 FROM sys.indexes 
  WHERE name = N'UX_Routes_RowSig'
    AND object_id = OBJECT_ID(N'dsp.Routes')
)
BEGIN
  CREATE UNIQUE INDEX UX_Routes_RowSig
    ON dsp.Routes(row_sig)
    WITH (IGNORE_DUP_KEY = ON);
END
GO
