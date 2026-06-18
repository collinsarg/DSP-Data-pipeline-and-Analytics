IF COL_LENGTH('dsp.NetradyneEvents','row_sig') IS NULL
BEGIN
  ALTER TABLE dsp.NetradyneEvents
  ADD row_sig AS CONVERT(VARBINARY(32),
    HASHBYTES('SHA2_256',
      CONCAT(
        COALESCE(CONVERT(varchar(40), event_id), '#NOID#'), '|',
        CONVERT(varchar(33), event_datetime, 126), '|',
        COALESCE(UPPER(LTRIM(RTRIM(transporter_id))),'#NULL#'), '|',
        COALESCE(UPPER(LTRIM(RTRIM(metric_type))),'#NULL#'), '|',
        COALESCE(UPPER(LTRIM(RTRIM(metric_subtype))),'#NULL#'), '|',
        COALESCE(UPPER(LTRIM(RTRIM(vin))),'#NULL#')
      )
    )
  ) PERSISTED;
END
GO

;WITH d AS (
  SELECT event_row_id,
         ROW_NUMBER() OVER (
           PARTITION BY
             COALESCE(CONVERT(varchar(40), event_id), '#NOID#'),
             CONVERT(varchar(33), event_datetime, 126),
             UPPER(LTRIM(RTRIM(transporter_id))),
             UPPER(LTRIM(RTRIM(metric_type))),
             UPPER(LTRIM(RTRIM(metric_subtype))),
             UPPER(LTRIM(RTRIM(vin)))
           ORDER BY event_row_id DESC
         ) rn
  FROM dsp.NetradyneEvents
)
DELETE FROM d WHERE rn > 1;
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='UX_Netradyne_RowSig' AND object_id=OBJECT_ID('dsp.NetradyneEvents'))
  CREATE UNIQUE INDEX UX_Netradyne_RowSig ON dsp.NetradyneEvents(row_sig) WITH (IGNORE_DUP_KEY=ON);
GO
