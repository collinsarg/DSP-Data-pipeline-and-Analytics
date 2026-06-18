/* A) Add a period discriminator if it's missing. 
      Use week_label NVARCHAR(32). Keep it NULLable so you can backfill, 
      then you can tighten to NOT NULL later if desired. */

/* B) Add persisted hash over (week_label, transporter_id) */
IF COL_LENGTH('dsp.WeeklyScorecard','row_sig') IS NULL
BEGIN
  ALTER TABLE dsp.WeeklyScorecard
  ADD row_sig AS CONVERT(VARBINARY(32),
    HASHBYTES('SHA2_256',
      CONCAT(
        COALESCE(UPPER(LTRIM(RTRIM(year_week_label))),'#NULL#'), '|',
        COALESCE(UPPER(LTRIM(RTRIM(transporter_id))),'#NULL#')
      )
    )
  ) PERSISTED;
END
GO

/* C) One-time de-dupe (keeps newest row per (week_label, transporter_id)) */
;WITH d AS (
  SELECT row_id,
         ROW_NUMBER() OVER (
           PARTITION BY UPPER(LTRIM(RTRIM(year_week_label))),
                        UPPER(LTRIM(RTRIM(transporter_id)))
           ORDER BY row_id DESC
         ) AS rn
  FROM dsp.WeeklyScorecard
)
DELETE FROM d WHERE rn > 1;
GO

/* D) Unique index to block future duplicates (ingests become idempotent) */
IF NOT EXISTS (
  SELECT 1 FROM sys.indexes 
  WHERE name = N'UX_WeeklyScorecard_RowSig'
    AND object_id = OBJECT_ID(N'dsp.WeeklyScorecard')
)
  CREATE UNIQUE INDEX UX_WeeklyScorecard_RowSig
    ON dsp.WeeklyScorecard(row_sig)
    WITH (IGNORE_DUP_KEY = ON);
GO
