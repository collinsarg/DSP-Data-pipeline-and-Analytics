/* 1A) Normalize your key parts into a deterministic signature.
       Use a NULL sentinel so NULL ≠ '' and timezone/format are stable. */
IF COL_LENGTH('dsp.Itineraries','row_sig') IS NULL
BEGIN
  ALTER TABLE dsp.Itineraries
  ADD row_sig AS CONVERT(VARBINARY(32),
    HASHBYTES('SHA2_256',
      CONCAT(
        /* ISO8601 for exact datetime text; adjust to your column name */
        CONVERT(varchar(33), file_datetime, 126), '|',
        /* Trim + force consistent case on IDs/codes to avoid whitespace/case dupes */
        COALESCE(UPPER(LTRIM(RTRIM(transporter_id))), '#NULL#'), '|',
        COALESCE(UPPER(LTRIM(RTRIM(route_code))),      '#NULL#')
      )
    )
  ) PERSISTED;
END
GO

/* 1B) (One-time) remove existing duplicates BEFORE adding the unique index. */
;WITH d AS (
  SELECT itin_row_id,
         ROW_NUMBER() OVER (
           PARTITION BY 
             CONVERT(varchar(33), file_datetime, 126),
             UPPER(LTRIM(RTRIM(transporter_id))),
             UPPER(LTRIM(RTRIM(route_code)))
           ORDER BY itin_row_id DESC
         ) AS rn
  FROM dsp.Itineraries
)
DELETE FROM d WHERE rn > 1;
GO

/* 1C) Enforce uniqueness at the storage layer.
       IGNORE_DUP_KEY = ON lets bulk inserts “skip” dupes instead of erroring. */
IF NOT EXISTS (SELECT 1 FROM sys.indexes 
               WHERE name = 'UX_Itin_RowSig' AND object_id = OBJECT_ID('dsp.Itineraries'))
BEGIN
  CREATE UNIQUE INDEX UX_Itin_RowSig
  ON dsp.Itineraries(row_sig)
  WITH (IGNORE_DUP_KEY = ON);
END
GO
