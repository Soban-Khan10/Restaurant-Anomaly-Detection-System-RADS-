// Shapes returned by the RADS app server (see app/routes/*.py and the shared contract, section 5).

export type Role = "Admin" | "Manager" | "Technical";
export type IncidentStatus = "New" | "Acknowledged" | "Confirmed violation" | "False alarm";
export type Severity = "Low" | "Medium" | "High";
export type CameraStatus = "online" | "offline" | "reconnecting";

export interface User {
  user_id: number;
  username: string;
  role: Role;
  full_name: string | null;
  phone: string | null;
  active: boolean;
  created_at: string;
  last_login: string | null;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
  expires_at: string;
  user: User;
}

export interface Incident {
  incident_id: number;
  hazard_id: string;
  hazard_name: string;
  rule_id: number;
  camera_id: number;
  camera_name: string;
  zone_id: number;
  zone_name: string;
  track_id: number | null;
  start_time: string;
  end_time: string | null;
  conf_avg: number | null;
  conf_max: number | null;
  severity: Severity;
  model_version: string;
  status: IncidentStatus;
  reviewed_by: number | null;
  reviewed_at: string | null;
  review_note: string | null;
  escalated_at: string | null;
  evidence_id: number | null;
}

export interface IncidentPage {
  items: Incident[];
  total: number;
  page: number;
  page_size: number;
}

export interface IncidentSummary {
  date_from: string;
  date_to: string;
  total: number;
  unreviewed: number;
  by_hazard: { hazard_id: string; name: string; count: number }[];
  by_status: Record<IncidentStatus, number>;
}

export interface Camera {
  camera_id: number;
  name: string;
  enabled: boolean;
  target_fps: number;
  frame_width: number | null;
  frame_height: number | null;
  status: CameraStatus;
  fps: number | null;
  rtsp_url?: string; // only for Admin and Technical
}

export interface Hazard {
  hazard_id: string;
  name: string;
  tied_to: "worker" | "zone";
}

export interface Alert {
  alert_id: number;
  incident_id: number;
  channel: "dashboard" | "whatsapp" | "telegram";
  kind: "first" | "escalation";
  sent_at: string;
  status: "sent" | "failed";
  hazard_id: string;
  hazard_name: string;
  camera_id: number;
  camera_name: string;
  zone_name: string;
  severity: Severity;
  start_time: string;
  end_time: string | null;
  incident_status: IncidentStatus;
}

// WebSocket messages (contract section 5, message 3)
export interface CameraStatusMessage {
  type: "camera.status";
  camera_id: number;
  status: CameraStatus;
  fps: number | null;
  ts: string;
  detection?: "active" | "paused";
  pause_reason?: PauseReason | null;
}

export type LiveMessage =
  | { type: "incident.new"; incident: Incident }
  | { type: "incident.closed"; incident_id: number }
  | { type: "monitoring.changed"; change: string; pause_id: number | null; camera_id: number | null }
  | CameraStatusMessage;

// Incident detail (GET /api/incidents/{id}; PATCH .../review returns the same shape)
export type ReviewStatus = Exclude<IncidentStatus, "New">;

export interface ReviewEvent {
  at: string;
  user_id: number | null;
  user_name: string | null;
  old_status: IncidentStatus | null;
  new_status: IncidentStatus;
  note: string | null;
}

export interface Evidence {
  evidence_id: number;
  kind: "snapshot" | "clip";
  file_path: string;
  captured_at: string;
  detections: unknown;
  deleted: boolean;
  deleted_at: string | null;
  image_url: string;
}

export interface RuleValues {
  rule_id: number;
  hazard_id: string;
  camera_id: number;
  enabled: boolean | number;
  conf_threshold: number;
  window_s: number;
  min_ratio: number;
  end_gap_s: number;
  cooldown_s: number;
  severity: Severity;
}

export interface IncidentAlert {
  alert_id: number;
  incident_id: number;
  user_id: number | null;
  recipient_name: string | null;
  channel: Alert["channel"];
  kind: Alert["kind"];
  sent_at: string;
  status: Alert["status"];
  error: string | null;
}

export interface IncidentDetail extends Incident {
  hazard: Hazard & { violation_class: string; compliant_class: string | null };
  rule: RuleValues;
  rule_changed_since: boolean;
  camera: { camera_id: number; name: string };
  zone: { zone_id: number; name: string; type: "prep" | "waste" | "ignore" };
  evidence: Evidence[];
  alerts: IncidentAlert[];
  reviewed_by_user: { user_id: number; username: string; full_name: string | null } | null;
  history: ReviewEvent[];
}

// Reports (GET/POST /api/reports)
export type ReportFormat = "pdf" | "xlsx";

export interface Report {
  report_id: number;
  created_by: number;
  created_by_name: string;
  period_start: string;
  period_end: string;
  format: ReportFormat;
  file_name: string;
  created_at: string;
  download_url: string;
  available: boolean;
}

// Cameras & areas, detection rules (/api/cameras, /api/zones, /api/rules)
export type ZoneType = "prep" | "waste" | "ignore";
export type Point = [number, number];

export interface Zone {
  zone_id: number;
  camera_id: number;
  name: string;
  type: ZoneType;
  polygon: Point[];
  active: boolean;
  rule_ids: number[];
}

export interface Rule {
  rule_id: number;
  hazard_id: string;
  hazard_name: string;
  camera_id: number;
  enabled: boolean;
  conf_threshold: number;
  window_s: number;
  min_ratio: number;
  end_gap_s: number;
  cooldown_s: number;
  severity: Severity;
  zone_ids: number[];
}

// Pause monitoring (/api/pauses, /api/monitoring/status)
export type PauseReason = "cleaning" | "closed" | "no_food_prep" | "maintenance" | "other";
export type PauseState = "active" | "planned" | "ended" | "cancelled";

export interface Pause {
  pause_id: number;
  camera_id: number | null; // null = all cameras
  camera_name: string;
  reason: PauseReason;
  reason_label: string;
  note: string | null;
  starts_at: string;
  ends_at: string | null; // null = until resumed
  created_by: number;
  created_by_name: string | null;
  created_at: string;
  ended_by: number | null;
  ended_by_name: string | null;
  ended_at: string | null;
  state: PauseState;
}

export interface CameraMonitoring {
  camera_id: number;
  camera_name: string;
  enabled: boolean;
  state: "active" | "paused" | "planned";
  pause: Pause | null;
  next_pause: Pause | null;
}
