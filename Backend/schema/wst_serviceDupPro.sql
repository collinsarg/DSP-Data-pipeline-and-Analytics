IF COL_LENGTH('dsp.WST_ServiceDetails','row_sig') IS NULL
BEGIN
  ALTER TABLE dsp.WST_ServiceDetails
  ADD row_sig AS CONVERT(VARBINARY(32),
    HASHBYTES('SHA2_256',
      CONCAT(
        CONVERT(varchar(33), row_date, 126), '|',
        COALESCE(UPPER(LTRIM(RTRIM(route_code))),'#NULL#'), '|',
        COALESCE(UPPER(LTRIM(RTRIM(delivery_associate))),'#NULL#'), '|',
        COALESCE(UPPER(LTRIM(RTRIM(station))),'#NULL#')
      )
    )
  ) PERSISTED;
END
GO

;WITH d AS (
  SELECT row_id,
         ROW_NUMBER() OVER (
           PARTITION BY CONVERT(varchar(33), row_date, 126),
                        UPPER(LTRIM(RTRIM(route_code))),
                        UPPER(LTRIM(RTRIM(delivery_associate))),
                        UPPER(LTRIM(RTRIM(station)))
           ORDER BY row_id DESC
         ) rn
  FROM dsp.WST_ServiceDetails
)
DELETE FROM d WHERE rn > 1;
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='UX_WST_Service_RowSig' AND object_id=OBJECT_ID('dsp.WST_ServiceDetails'))
  CREATE UNIQUE INDEX UX_WST_Service_RowSig ON dsp.WST_ServiceDetails(row_sig) WITH (IGNORE_DUP_KEY=ON);
GO
