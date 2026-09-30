-- 业务库 DDL（MySQL 8）。与 app/db/models.py 保持一致，是生产环境的权威 schema。
-- 特性：全程幂等、非破坏——可对全新库初始化，也可对已存在的旧库重复执行来补齐缺失的表/列，
--       不会删除或覆盖已有数据。docker 首次起库会自动执行；旧库用下面命令手动补齐：
--   docker exec -i pyfin-mysql mysql -upyfin -ppyfin pyfinagent < sql/mysql_schema.sql

SET NAMES utf8mb4;

-- ========== 会话 / 消息 ==========
CREATE TABLE IF NOT EXISTS chat_session (
    id          VARCHAR(64)  NOT NULL PRIMARY KEY,
    user_id     BIGINT       NOT NULL DEFAULT 0,
    owner       VARCHAR(64)  NULL,               -- 归属登录用户名（按用户隔离历史；auth 关闭时为 anonymous）
    title       VARCHAR(255) NOT NULL,
    created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_session_user (user_id),
    INDEX idx_session_owner (owner)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS chat_message (
    id           BIGINT       NOT NULL AUTO_INCREMENT PRIMARY KEY,
    session_id   VARCHAR(64)  NOT NULL,
    role         VARCHAR(32)  NOT NULL,          -- user / assistant / system / tool
    content      TEXT         NOT NULL,
    agent_type   VARCHAR(32)  NULL,              -- education/product/recommend/assessment/chat
    tool_calls   JSON         NULL,              -- 引用溯源 / 工具轨迹
    tokens_used  INT          NOT NULL DEFAULT 0,
    created_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_message_session (session_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ========== 用户 / 私有理财资料 ==========
CREATE TABLE IF NOT EXISTS app_user (
    id             BIGINT       NOT NULL AUTO_INCREMENT PRIMARY KEY,
    username       VARCHAR(64)  NOT NULL,
    password_hash  VARCHAR(255) NOT NULL,
    created_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_user_username (username)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS user_finance (
    username    VARCHAR(64)  NOT NULL PRIMARY KEY,   -- 按用户名隔离的私有资料
    content     TEXT         NOT NULL,
    updated_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS user_risk (
    username    VARCHAR(64)  NOT NULL PRIMARY KEY,   -- 风险画像按用户持久化，跨会话可用
    profile     VARCHAR(64)  NOT NULL,               -- 如 "稳健型·R2"
    updated_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ========== 用户反馈（点赞/点踩满意度）==========
CREATE TABLE IF NOT EXISTS feedback (
    id          BIGINT       NOT NULL AUTO_INCREMENT PRIMARY KEY,
    session_id  VARCHAR(64)  NOT NULL,
    question    TEXT         NOT NULL,
    answer      TEXT         NOT NULL,
    agent_type  VARCHAR(32)  NULL,
    rating      VARCHAR(8)   NOT NULL,           -- up / down
    comment     TEXT         NULL,
    applied     SMALLINT     NOT NULL DEFAULT 0, -- 0待审核 / 1已入库 / 2已拒绝
    created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_feedback_session (session_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ========== 幂等补列：对「旧库已存在但缺列」的情况打补丁 ==========
-- MySQL 8 不支持 ADD COLUMN IF NOT EXISTS，这里用 information_schema 判断后动态执行，
-- 已存在则跳过，可安全重复运行。

-- chat_session.owner
SET @ddl := IF(
    NOT EXISTS(SELECT 1 FROM information_schema.columns
               WHERE table_schema=DATABASE() AND table_name='chat_session' AND column_name='owner'),
    'ALTER TABLE chat_session ADD COLUMN owner VARCHAR(64) NULL, ADD INDEX idx_session_owner (owner)',
    'SELECT 1');
PREPARE _s FROM @ddl; EXECUTE _s; DEALLOCATE PREPARE _s;
