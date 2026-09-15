-- ============================================================
-- agent.db 建表脚本 (SQLite)
-- 注意：SQLite 不支持 MySQL 的 ON UPDATE CURRENT_TIMESTAMP，
--       自动更新 updated_at 请改用下方触发器实现。
-- Phase 0–1: fault_event ID hub + option/SSOT catalog
-- ============================================================

PRAGMA foreign_keys = ON;

-- 先删除旧表（注意顺序：先删有外键依赖的表）
DROP TABLE IF EXISTS fault_ssot_revision;
DROP TABLE IF EXISTS fault_option_pending;
DROP TABLE IF EXISTS fault_event_fact;
DROP TABLE IF EXISTS fault_analysis;
DROP TABLE IF EXISTS fault_solution;
DROP TABLE IF EXISTS fault_ssot;
DROP TABLE IF EXISTS fault_option;
DROP TABLE IF EXISTS vision_asset;
DROP TABLE IF EXISTS fault_event;
DROP TABLE IF EXISTS watchdog_log;
DROP TABLE IF EXISTS worker_heartbeat;
DROP TABLE IF EXISTS task_queue;
DROP TABLE IF EXISTS workers;
DROP TABLE IF EXISTS module;
DROP TABLE IF EXISTS channel;

-- ------------------------------------------------------------
-- workers 表用于存储工作节点的信息
-- ------------------------------------------------------------
CREATE TABLE workers (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT    NOT NULL,
    status       TEXT    NOT NULL DEFAULT 'idle',
    group_name   TEXT    NOT NULL DEFAULT 'default',
    last_seen_at TIMESTAMP,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (name, group_name)
);

-- ------------------------------------------------------------
-- task_queue 表用于存储任务队列的信息
-- ------------------------------------------------------------
CREATE TABLE task_queue (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    TEXT    NOT NULL,
    status     TEXT    NOT NULL DEFAULT 'pending'
               CHECK (status IN ('pending','running','pass','fail')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ------------------------------------------------------------
-- worker_heartbeat 表：心跳历史，只追加
-- ------------------------------------------------------------
CREATE TABLE worker_heartbeat (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    worker_id       INTEGER NOT NULL,
    screenshot_path TEXT,
    business_alive  INTEGER,
    pid             INTEGER,
    heartbeat_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (worker_id) REFERENCES workers (id) ON DELETE CASCADE
);

-- ------------------------------------------------------------
-- watchdog_log 表用于记录监控日志
-- ------------------------------------------------------------
CREATE TABLE watchdog_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    worker_id  INTEGER NOT NULL,
    message    TEXT    NOT NULL,
    level      TEXT    NOT NULL DEFAULT 'info',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ------------------------------------------------------------
-- channel / module：ID 驱动维度表
-- ------------------------------------------------------------
CREATE TABLE channel (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT    NOT NULL UNIQUE,
    name        TEXT    NOT NULL,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE module (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT    NOT NULL UNIQUE,
    name        TEXT    NOT NULL,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ------------------------------------------------------------
-- vision_asset：证据图/URL 的 id（fault_event.vision_id）
-- ------------------------------------------------------------
CREATE TABLE vision_asset (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    kind         TEXT    NOT NULL DEFAULT 'other'
                 CHECK (kind IN ('frozen', 'live', 'other')),
    path_or_url  TEXT    NOT NULL,
    sha256       TEXT,
    source       TEXT,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ------------------------------------------------------------
-- fault_option / fault_ssot / fault_solution：目录 + 模板 SSOT + 解法
-- ------------------------------------------------------------
CREATE TABLE fault_option (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT    NOT NULL UNIQUE,
    name        TEXT    NOT NULL,
    channel_id  INTEGER,
    module_id   INTEGER,
    status      TEXT    NOT NULL DEFAULT 'active'
                CHECK (status IN ('active', 'draft', 'deprecated')),
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (channel_id) REFERENCES channel (id) ON DELETE SET NULL,
    FOREIGN KEY (module_id)  REFERENCES module  (id) ON DELETE SET NULL
);

CREATE TABLE fault_ssot (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    option_id   INTEGER NOT NULL,
    keyword     TEXT    NOT NULL,
    value_text  TEXT,
    value_type  TEXT    NOT NULL DEFAULT 'string',
    weight      REAL    NOT NULL DEFAULT 1.0,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (option_id, keyword),
    FOREIGN KEY (option_id) REFERENCES fault_option (id) ON DELETE CASCADE
);

CREATE TABLE fault_solution (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    option_id   INTEGER NOT NULL,
    title       TEXT    NOT NULL,
    steps_text  TEXT,
    steps_json  TEXT,
    priority    INTEGER NOT NULL DEFAULT 100,
    status      TEXT    NOT NULL DEFAULT 'active'
                CHECK (status IN ('active', 'draft', 'deprecated')),
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (option_id) REFERENCES fault_option (id) ON DELETE CASCADE
);

-- ------------------------------------------------------------
-- fault_event：故障事件 hub（ID 关联 + 主干检测字段）
-- status 只允许人工改为 resolved（系统不因心跳恢复自动结案）
-- ------------------------------------------------------------
CREATE TABLE fault_event (
    event_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    worker_id         INTEGER,
    group_id          TEXT    NOT NULL,
    detect_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    fault_type        TEXT    NOT NULL,
    evidence_img_path TEXT,
    evidence_error    TEXT,
    status            TEXT    NOT NULL DEFAULT 'open'
                      CHECK (status IN ('open','resolved')),
    resolved_at       TIMESTAMP,
    -- ID hub (Phase 1)
    channel_id        INTEGER,
    module_id         INTEGER,
    vision_id         INTEGER,
    fault_analysis_id INTEGER,
    task_id           INTEGER,
    option_id         INTEGER,
    solution_id       INTEGER,
    FOREIGN KEY (worker_id) REFERENCES workers (id) ON DELETE SET NULL,
    FOREIGN KEY (channel_id) REFERENCES channel (id) ON DELETE SET NULL,
    FOREIGN KEY (module_id) REFERENCES module (id) ON DELETE SET NULL,
    FOREIGN KEY (vision_id) REFERENCES vision_asset (id) ON DELETE SET NULL,
    FOREIGN KEY (task_id) REFERENCES task_queue (id) ON DELETE SET NULL,
    FOREIGN KEY (option_id) REFERENCES fault_option (id) ON DELETE SET NULL,
    FOREIGN KEY (solution_id) REFERENCES fault_solution (id) ON DELETE SET NULL
);

-- ------------------------------------------------------------
-- fault_analysis：每个 fault_event 一次 vision + 匹配编排结果
-- ------------------------------------------------------------
CREATE TABLE fault_analysis (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id         INTEGER NOT NULL UNIQUE,
    model            TEXT,
    summary          TEXT,
    detail_json      TEXT,
    evidence_used    TEXT    NOT NULL DEFAULT 'none'
                     CHECK (evidence_used IN ('frozen', 'live', 'none')),
    evidence_path    TEXT,
    error            TEXT,
    notified_at      TIMESTAMP,
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    vision_id        INTEGER,
    option_id        INTEGER,
    solution_id      INTEGER,
    match_score      REAL,
    match_status     TEXT,
    ssot_prompt_json TEXT,
    solution_summary TEXT,
    FOREIGN KEY (event_id) REFERENCES fault_event (event_id) ON DELETE CASCADE,
    FOREIGN KEY (vision_id) REFERENCES vision_asset (id) ON DELETE SET NULL,
    FOREIGN KEY (option_id) REFERENCES fault_option (id) ON DELETE SET NULL,
    FOREIGN KEY (solution_id) REFERENCES fault_solution (id) ON DELETE SET NULL
);

-- 实例多维事实（今次真实数据）
CREATE TABLE fault_event_fact (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id    INTEGER NOT NULL,
    keyword     TEXT    NOT NULL,
    value_text  TEXT,
    value_type  TEXT    NOT NULL DEFAULT 'string',
    source      TEXT    NOT NULL DEFAULT 'watchdog',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (event_id) REFERENCES fault_event (event_id) ON DELETE CASCADE
);

CREATE TABLE fault_option_pending (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id        INTEGER NOT NULL,
    analysis_id     INTEGER,
    proposed_code   TEXT,
    proposed_name   TEXT,
    reason          TEXT,
    vision_summary  TEXT,
    status          TEXT    NOT NULL DEFAULT 'open'
                    CHECK (status IN ('open', 'accepted', 'rejected')),
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at     TIMESTAMP,
    FOREIGN KEY (event_id) REFERENCES fault_event (event_id) ON DELETE CASCADE,
    FOREIGN KEY (analysis_id) REFERENCES fault_analysis (id) ON DELETE SET NULL
);

CREATE TABLE fault_ssot_revision (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    option_id   INTEGER,
    event_id    INTEGER,
    action      TEXT    NOT NULL
                CHECK (action IN ('add', 'update', 'delete')),
    keyword     TEXT    NOT NULL,
    old_value   TEXT,
    new_value   TEXT,
    status      TEXT    NOT NULL DEFAULT 'proposed'
                CHECK (status IN ('proposed', 'applied', 'rejected')),
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    applied_at  TIMESTAMP,
    FOREIGN KEY (option_id) REFERENCES fault_option (id) ON DELETE SET NULL,
    FOREIGN KEY (event_id)  REFERENCES fault_event  (event_id) ON DELETE SET NULL
);

-- ------------------------------------------------------------
-- 触发器：实现 updated_at 自动更新
-- ------------------------------------------------------------
CREATE TRIGGER trg_workers_updated_at
AFTER UPDATE ON workers
FOR EACH ROW
BEGIN
    UPDATE workers SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

CREATE TRIGGER trg_task_queue_updated_at
AFTER UPDATE ON task_queue
FOR EACH ROW
BEGIN
    UPDATE task_queue SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

-- ------------------------------------------------------------
-- 常用索引
-- ------------------------------------------------------------
CREATE INDEX idx_task_queue_status     ON task_queue (status);
CREATE INDEX idx_heartbeat_worker_time ON worker_heartbeat (worker_id, heartbeat_at DESC);
CREATE INDEX idx_watchdog_worker       ON watchdog_log (worker_id);
CREATE INDEX idx_workers_group         ON workers (group_name);
CREATE INDEX idx_fault_event_status    ON fault_event (status);
CREATE INDEX idx_fault_event_detect    ON fault_event (detect_at DESC);
CREATE INDEX idx_fault_event_group     ON fault_event (group_id);
CREATE INDEX idx_fault_event_channel   ON fault_event (channel_id);
CREATE INDEX idx_fault_event_module    ON fault_event (module_id);
CREATE INDEX idx_fault_event_option    ON fault_event (option_id);
CREATE INDEX idx_fault_analysis_created ON fault_analysis (created_at DESC);
CREATE INDEX idx_fault_event_fact_event ON fault_event_fact (event_id, keyword);
CREATE INDEX idx_fault_ssot_option     ON fault_ssot (option_id, keyword);
CREATE INDEX idx_fault_option_status   ON fault_option (status);
CREATE INDEX idx_fault_solution_option ON fault_solution (option_id, priority);
CREATE INDEX idx_fault_option_pending_status ON fault_option_pending (status, created_at DESC);
CREATE INDEX idx_fault_ssot_revision_status ON fault_ssot_revision (status, created_at DESC);
CREATE INDEX idx_vision_asset_created  ON vision_asset (created_at DESC);
