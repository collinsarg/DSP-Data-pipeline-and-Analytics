-- 1) Drop old tenure table (adjust name if needed)
IF OBJECT_ID('dsp.TenuredWorkforce', 'U') IS NOT NULL
BEGIN
    DROP TABLE dsp.TenuredWorkforce;
END;
GO

-- 2) Create new tenure table
CREATE TABLE dsp.TenuredWorkforce
(
    transporter_id      VARCHAR(16)      NOT NULL,    -- Amazon DA ID
    full_name           NVARCHAR(200)    NULL,        -- can be filled in from different sources
    is_active           BIT              NOT NULL 
        CONSTRAINT DF_TenuredWorkforce_is_active DEFAULT (1),
    lifetime_routes     INT              NOT NULL 
        CONSTRAINT DF_TenuredWorkforce_lifetime_routes DEFAULT (0),

    tenure_status       NVARCHAR(32)     NOT NULL 
        CONSTRAINT DF_TenuredWorkforce_tenure_status DEFAULT ('Not Tenured'),

    last_delivery_date  DATE             NULL,        -- must allow NULL (backlog has only week)
    last_delivery_week  INT              NULL,        -- WeekOfYear from dbo.WeekPointer

    CONSTRAINT PK_TenuredWorkforce PRIMARY KEY CLUSTERED (transporter_id)
);
GO
