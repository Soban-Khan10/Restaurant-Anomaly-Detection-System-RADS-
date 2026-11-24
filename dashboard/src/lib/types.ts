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
}

export type LiveMessage =
  | { type: "incident.new"; incident: Incident }
  | { type: "incident.closed"; incident_id: number }
  | CameraStatusMessage;
