PRAGMA foreign_keys = ON;

CREATE TABLE roles (
  role_id   INTEGER PRIMARY KEY,
  name      TEXT NOT NULL UNIQUE CHECK (name IN ('Admin','Manager','Technical'))
);
CREATE TABLE users (
  user_id       INTEGER PRIMARY KEY,
  role_id       INTEGER NOT NULL REFERENCES roles(role_id),
  username      TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  full_name     TEXT,
  phone         TEXT,
  email         TEXT,
  active        INTEGER NOT NULL DEFAULT 1,
  created_at    TEXT NOT NULL,
  last_login    TEXT
);
CREATE TABLE audit_log (
  log_id      INTEGER PRIMARY KEY,
  user_id     INTEGER REFERENCES users(user_id),
  action      TEXT NOT NULL,
  target_type TEXT,
  target_id   INTEGER,
  details     TEXT,            -- JSON
  ip_address  TEXT,
  created_at  TEXT NOT NULL
);
CREATE TABLE reports (
  report_id    INTEGER PRIMARY KEY,
  created_by   INTEGER NOT NULL REFERENCES users(user_id),
  period_start TEXT NOT NULL,
  period_end   TEXT NOT NULL,
  format       TEXT NOT NULL CHECK (format IN ('pdf','xlsx')),
  file_path    TEXT NOT NULL,
  created_at   TEXT NOT NULL
);
CREATE TABLE cameras (
  camera_id    INTEGER PRIMARY KEY,
  name         TEXT NOT NULL,
  rtsp_url     TEXT NOT NULL,
  enabled      INTEGER NOT NULL DEFAULT 1,
  target_fps   REAL NOT NULL DEFAULT 5,
  frame_width  INTEGER,
  frame_height INTEGER
);
CREATE TABLE camera_health (
  camera_id INTEGER PRIMARY KEY REFERENCES cameras(camera_id),
  status    TEXT NOT NULL CHECK (status IN ('online','offline','reconnecting')),
  fps       REAL,
  last_seen TEXT NOT NULL
);
CREATE TABLE zones (
  zone_id   INTEGER PRIMARY KEY,
  camera_id INTEGER NOT NULL REFERENCES cameras(camera_id),
  name      TEXT NOT NULL,
  type      TEXT NOT NULL CHECK (type IN ('prep','waste','ignore')),
  polygon   TEXT NOT NULL,     -- JSON [[x,y],...] in camera pixels
  active    INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE hazards (
  hazard_id       TEXT PRIMARY KEY,
  name            TEXT NOT NULL,
  violation_class TEXT NOT NULL,
  compliant_class TEXT,        -- NULL for H-04
  tied_to         TEXT NOT NULL CHECK (tied_to IN ('worker','zone'))
);
CREATE TABLE rules (
  rule_id        INTEGER PRIMARY KEY,
  hazard_id      TEXT NOT NULL REFERENCES hazards(hazard_id),
  camera_id      INTEGER NOT NULL REFERENCES cameras(camera_id),
  enabled        INTEGER NOT NULL DEFAULT 1,
  conf_threshold REAL NOT NULL DEFAULT 0.45,
  window_s       REAL NOT NULL DEFAULT 5,
  min_ratio      REAL NOT NULL DEFAULT 0.70,
  end_gap_s      REAL NOT NULL DEFAULT 5,
  cooldown_s     REAL NOT NULL DEFAULT 60,
  severity       TEXT NOT NULL DEFAULT 'Medium' CHECK (severity IN ('Low','Medium','High')),
  UNIQUE (hazard_id, camera_id)
);
CREATE TABLE rule_zones (
  rule_id INTEGER NOT NULL REFERENCES rules(rule_id) ON DELETE CASCADE,
  zone_id INTEGER NOT NULL REFERENCES zones(zone_id) ON DELETE CASCADE,
  PRIMARY KEY (rule_id, zone_id)
);
CREATE TABLE incidents (
  incident_id   INTEGER PRIMARY KEY,
  hazard_id     TEXT NOT NULL REFERENCES hazards(hazard_id),
  rule_id       INTEGER NOT NULL REFERENCES rules(rule_id),
  camera_id     INTEGER NOT NULL REFERENCES cameras(camera_id),
  zone_id       INTEGER NOT NULL REFERENCES zones(zone_id),
  reviewed_by   INTEGER REFERENCES users(user_id),
  track_id      INTEGER,          -- NULL for H-04, H-05
  start_time    TEXT NOT NULL,
  end_time      TEXT,             -- NULL while open
  conf_avg      REAL,
  conf_max      REAL,
  severity      TEXT NOT NULL,
  model_version TEXT NOT NULL,
  status        TEXT NOT NULL DEFAULT 'New'
                CHECK (status IN ('New','Acknowledged','Confirmed violation','False alarm')),
  reviewed_at   TEXT,
  review_note   TEXT,
  escalated_at  TEXT
);
CREATE INDEX ix_incidents_start  ON incidents(start_time);
CREATE INDEX ix_incidents_status ON incidents(status);
CREATE INDEX ix_incidents_cam_hz ON incidents(camera_id, hazard_id);
CREATE TABLE evidence (
  evidence_id INTEGER PRIMARY KEY,
  incident_id INTEGER NOT NULL REFERENCES incidents(incident_id),
  kind        TEXT NOT NULL DEFAULT 'snapshot' CHECK (kind IN ('snapshot','clip')),
  file_path   TEXT NOT NULL,      -- relative to rads_data/evidence
  captured_at TEXT NOT NULL,
  detections  TEXT,               -- JSON
  deleted_at  TEXT
);
CREATE TABLE alerts (
  alert_id    INTEGER PRIMARY KEY,
  incident_id INTEGER NOT NULL REFERENCES incidents(incident_id),
  user_id     INTEGER REFERENCES users(user_id),
  channel     TEXT NOT NULL CHECK (channel IN ('dashboard','whatsapp','telegram','email')),
  kind        TEXT NOT NULL CHECK (kind IN ('first','escalation')),
  sent_at     TEXT NOT NULL,
  status      TEXT NOT NULL CHECK (status IN ('sent','failed')),
  error       TEXT
);
-- Pause monitoring. A pause is ACTIVE for a camera when (camera_id = that camera OR camera_id IS NULL)
-- AND starts_at <= now AND (ends_at IS NULL OR ends_at > now) AND ended_at IS NULL  (shared.contract.active_pause).
CREATE TABLE monitoring_pauses (
  pause_id    INTEGER PRIMARY KEY,
  camera_id   INTEGER REFERENCES cameras(camera_id),   -- NULL = all cameras
  reason      TEXT NOT NULL CHECK (reason IN ('cleaning','closed','no_food_prep','maintenance','other')),
  note        TEXT,
  starts_at   TEXT NOT NULL,     -- ISO 8601 +05:00; may be in the future (planned pause)
  ends_at     TEXT,              -- NULL = until resumed manually
  created_by  INTEGER NOT NULL REFERENCES users(user_id),
  created_at  TEXT NOT NULL,
  ended_by    INTEGER REFERENCES users(user_id),       -- set when resumed early or cancelled
  ended_at    TEXT
);
CREATE INDEX ix_pauses_active ON monitoring_pauses(camera_id, starts_at, ends_at);
