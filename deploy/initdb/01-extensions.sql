-- 工程监理质量智能评估系统 数据库初始化（SRS 5.2）
-- PostgreSQL 16；pg_trgm 用于条款号模糊匹配。
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

DO $$
BEGIN
  RAISE NOTICE 'supervision database initialized at %', now();
END $$;
